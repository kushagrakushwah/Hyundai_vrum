"""
tests/test_challenger_m3_3_remediation.py
=========================================
Empirical Adversarial Verification Suite for Milestone 3 Remediation (Challenger 3).

Adversarial Stress Test Matrix:
1. Simultaneous Auth Scan During Overvoltage vs Overcurrent Faults:
   - Overvoltage fault injection (>227V) with simultaneous RFID / PIN auth.
   - Overcurrent fault injection (>29.7A) with simultaneous RFID / PIN auth.
   - Inbound auth payload specifying current_state='FAULTED'.
   - High-concurrency race condition: 30 concurrent auth attempts dispatched
     simultaneously during emergency fault tripping.
   - Contactor safety invariant: relay_action MUST NEVER be ENERGIZE, status_code
     MUST be 403, auth_decision MUST be DENIED, no transaction can start.

2. CancelReservation Across All 4 FSM States (AVAILABLE, RESERVED, CHARGING, FAULTED):
   - Empty payload, None reservationId, empty string, zero, arbitrary mismatched integer/string.
   - Complex non-scalar types (list, dict, boolean).
   - Matching reservationId applied when in wrong states (AVAILABLE, CHARGING, FAULTED) -> Rejected.
   - Matching reservationId in RESERVED state (string vs int matching) -> Accepted.
   - Idempotency & replay rejection: second cancellation of same ID -> Rejected.
   - State isolation: Rejected cancellation MUST NOT clear active reservation or send MQTT CANCEL.

3. RemoteStopTransaction String vs Integer tx_id, None, and Empty Payloads:
   - Idle state (no active tx): empty payload, None, zero, empty string, arbitrary ID -> Rejected.
   - Active state (tx active): None, empty payload, mismatched int, mismatched str,
     non-scalar objects (list, dict).
   - Type coercion robustness: string tx_id ("9001") matching integer active tx (9001) and vice versa -> Accepted.
   - Double-stop / post-termination rejection -> Rejected.

4. Reverting from CHARGING to AVAILABLE via Disconnect vs Manual Stop vs Timeout:
   - Revert via transition_reason='EV_DISCONNECTED' -> StopTransaction reason='EVDisconnected', active_tx cleared.
   - Revert via transition_reason='MANUAL_STOP' -> StopTransaction stopped, active_tx cleared.
   - Multi-step teardown via state='FINISHING' then state='AVAILABLE' -> no double-stop, clean lifecycle.
   - Hardware 60s reservation timeout (state='AVAILABLE', reason='RESERVATION_TIMEOUT') -> active_reservation_id cleared.
   - Post-teardown idempotency: redundant AVAILABLE states and telemetry do not crash or resurrect sessions.

5. Rapid FSM State Flapping Under High Concurrency:
   - Rapid state cycling (AVAILABLE -> RESERVED -> CHARGING -> FAULTED -> AVAILABLE) while concurrent
     auth, cancel, and remote stop operations bombard the bridge.
"""

import asyncio
import json
import os
import sys
import time
import pytest

# Ensure imports resolve to project root and smartreserve_backend
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "smartreserve", "smartreserve_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ocpp_bridge import (
    OCPPBridge,
    VirtualCSMS,
    OCPPMessageType,
    OCPPChargePointStatus,
    OCPPChargePointErrorCode,
    OCPPCallErrorCode,
    OCPPCallError,
    EVerestEvseBoardSupport,
    EVerestAuth,
    EVerestEvseManager,
    serialize_call,
    serialize_call_result,
    serialize_call_error,
    parse_frame,
    current_iso_z,
)


class MockMQTTCollector:
    """Records published MQTT commands and reservations for invariant verification."""

    def __init__(self):
        self.published_commands = []
        self.published_reserves = []
        self.raw_published = []

    async def publish_cmd(
        self,
        station_id: str,
        connector_id: str,
        action: str,
        status: str,
        status_code: int,
        auth_window_duration_ms: int = 0,
        relay_action: str = "DE_ENERGIZE",
    ):
        self.published_commands.append({
            "station_id": station_id,
            "connector_id": connector_id,
            "action": action,
            "status": status,
            "status_code": status_code,
            "auth_window_duration_ms": auth_window_duration_ms,
            "relay_action": relay_action,
        })

    async def publish_reserve(
        self,
        station_id: str,
        connector_id: str,
        action: str,
        reservation_id: str,
        pin: str = "",
        duration_seconds: int = 60,
        expires_at: int = 0,
        rfid_uid: str = "",
    ):
        self.published_reserves.append({
            "station_id": station_id,
            "connector_id": connector_id,
            "action": action,
            "reservation_id": reservation_id,
            "pin": pin,
            "duration_seconds": duration_seconds,
            "expires_at": expires_at,
            "rfid_uid": rfid_uid,
        })

    async def publish(self, topic: str, payload: dict):
        self.raw_published.append({"topic": topic, "payload": payload})


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Simultaneous Auth Scan During Overvoltage vs Overcurrent Faults
# ══════════════════════════════════════════════════════════════════════════════

class TestSimultaneousAuthDuringFaultTrips:
    """Stress tests electrical safety invariants under fault trips."""

    @pytest.mark.asyncio
    async def test_auth_scan_during_overvoltage_fault(self):
        """
        Verify that an RFID or PIN auth attempt during an overvoltage trip
        (>227V) is strictly denied with 403, relay_action DE_ENERGIZED,
        and no transaction started.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Inject Overvoltage fault
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "FAULTED",
                    "relay_state": "DE_ENERGIZED",
                    "fault_code": "OVERVOLTAGE",
                    "transition_reason": "Voltage exceeded 227V trip threshold (248.5V)",
                    "voltage_v": 248.5,
                },
            )
            assert bridge.everest_manager.state == "FAULTED"
            assert bridge.everest_board.is_tripped is True
            assert bridge.everest_board.fault_code == "OVERVOLTAGE"

            # 2. Present authorized whitelisted RFID card
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "DEADBEEF"},
                    "current_state": "FAULTED",
                },
            )

            # Invariant checks:
            assert len(mqtt_mock.published_commands) == 1
            cmd = mqtt_mock.published_commands[0]
            assert cmd["action"] == "AUTH_DECISION"
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 403
            assert cmd["relay_action"] == "DE_ENERGIZED"
            assert cmd["auth_window_duration_ms"] == 0
            assert bridge.everest_manager.active_transaction_id is None
            assert len(csms.transactions) == 0

            # 3. Present PIN credential
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "PIN",
                    "credential": {"pin": "1234"},
                    "current_state": "FAULTED",
                },
            )
            assert len(mqtt_mock.published_commands) == 2
            cmd2 = mqtt_mock.published_commands[1]
            assert cmd2["status"] == "DENIED"
            assert cmd2["status_code"] == 403
            assert cmd2["relay_action"] == "DE_ENERGIZED"
            assert bridge.everest_manager.active_transaction_id is None

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_auth_scan_during_overcurrent_fault(self):
        """
        Verify that an RFID or PIN auth attempt during an overcurrent trip
        (>29.7A) is strictly denied with 403, relay_action DE_ENERGIZED,
        and no transaction started.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Inject Overcurrent fault
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "FAULTED",
                    "relay_state": "DE_ENERGIZED",
                    "fault_code": "OVERCURRENT",
                    "transition_reason": "Current exceeded 29.7A trip threshold (35.2A)",
                    "current_a": 35.2,
                },
            )
            assert bridge.everest_manager.state == "FAULTED"
            assert bridge.everest_board.is_tripped is True
            assert bridge.everest_board.fault_code == "OVERCURRENT"

            # 2. Present authorized whitelisted RFID card
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "TAG001"},
                    "current_state": "FAULTED",
                },
            )

            assert len(mqtt_mock.published_commands) == 1
            cmd = mqtt_mock.published_commands[0]
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 403
            assert cmd["relay_action"] == "DE_ENERGIZED"
            assert bridge.everest_manager.active_transaction_id is None

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_auth_payload_reporting_faulted_state_even_if_bridge_was_available(self):
        """
        If hardware sends an auth packet with current_state='FAULTED' (e.g. edge device tripped locally),
        the bridge must respect the client's reported FAULTED state and deny authorization.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_board.is_tripped is False

            # Inbound auth payload reports current_state = FAULTED
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "DEADBEEF"},
                    "current_state": "FAULTED",
                },
            )

            assert len(mqtt_mock.published_commands) == 1
            cmd = mqtt_mock.published_commands[0]
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 403
            assert cmd["relay_action"] == "DE_ENERGIZED"
            assert bridge.everest_manager.active_transaction_id is None
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_concurrent_auth_bombardment_during_emergency_trip(self):
        """
        Race condition stress test: 30 simultaneous auth attempts arriving concurrently
        with a hardware emergency trip. Under NO circumstances should any auth attempt
        energize the relay once tripped.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Start an active transaction
            start_res = await bridge.send_start_transaction("CONCURRENT_USER")
            assert "transactionId" in start_res
            active_tx = bridge.everest_manager.active_transaction_id
            assert active_tx is not None

            # 2. Concurrently inject trip AND 30 auth requests
            trip_coro = bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "FAULTED",
                    "relay_state": "DE_ENERGIZED",
                    "fault_code": "OVERCURRENT",
                    "transition_reason": "Severe short-circuit trip",
                },
            )

            auth_coros = [
                bridge.handle_mqtt_auth(
                    "evsarthi/ST001/1/auth",
                    {
                        "auth_type": "RFID" if i % 2 == 0 else "PIN",
                        "credential": {"rfid_uid": f"TAG_{i}", "pin": f"{1000+i}"},
                        "current_state": "FAULTED",
                    },
                )
                for i in range(30)
            ]

            await asyncio.gather(trip_coro, *auth_coros)

            # Invariant: Station must be FAULTED and relay must NOT be ENERGIZED in any command sent post-trip
            assert bridge.everest_manager.state == "FAULTED"
            assert bridge.everest_board.is_tripped is True

            for cmd in mqtt_mock.published_commands:
                # If command is for faulted state, relay_action MUST be DE_ENERGIZED
                if cmd["status"] == "DENIED":
                    assert cmd["relay_action"] in ["DE_ENERGIZED", "DE_ENERGIZE"]
                    assert cmd["status_code"] in [401, 403]
                if cmd["relay_action"] == "ENERGIZE":
                    # Should never happen when station is tripped
                    assert False, f"Contactor energized during fault condition! Cmd: {cmd}"

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: CancelReservation Across All 4 FSM States
# ══════════════════════════════════════════════════════════════════════════════

class TestCancelReservationFSMAdversarial:
    """Stress tests CancelReservation validation across all 4 FSM states and corner cases."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fsm_state", ["AVAILABLE", "CHARGING", "FAULTED"]
    )
    async def test_cancel_reservation_rejected_in_non_reserved_states(self, fsm_state):
        """
        In AVAILABLE, CHARGING, or FAULTED states, any CancelReservation call
        (even with an ID matching a prior reservation) MUST be Rejected.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            bridge.everest_manager.state = fsm_state
            # Even if active_reservation_id was somehow set or matching
            bridge.everest_manager.active_reservation_id = "5555"

            test_payloads = [
                {},
                {"reservationId": None},
                {"reservationId": ""},
                {"reservationId": 0},
                {"reservationId": 9999},
                {"reservationId": "9999"},
                {"reservationId": 5555},
                {"reservationId": "5555"},
                {"reservationId": ["invalid_type"]},
                {"reservationId": {"nested": "id"}},
                {"reservationId": "SELECT * FROM reservations;"},
                {"reservationId": "⚡🚗"},
            ]

            for payload in test_payloads:
                res = await bridge.handle_cancel_reservation(payload)
                assert res.get("status") == "Rejected", (
                    f"Expected Rejected in state {fsm_state} with payload {payload}, got {res}"
                )

            # Ensure no MQTT CANCEL command was published
            assert len(mqtt_mock.published_reserves) == 0

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cancel_reservation_in_reserved_state_adversarial_payloads(self):
        """
        In RESERVED state with active reservation '7777':
        - Mismatched IDs, None, empty strings, malformed types must return Rejected.
        - Matching integer 7777 or string '7777' must return Accepted.
        - Subsequent repeated cancel must return Rejected (idempotent state defense).
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Set station to RESERVED with ID '7777'
            await csms.send_reserve_now(bridge.charge_box_id, 1, current_iso_z(), "USER7", 7777)
            assert bridge.everest_manager.state == "RESERVED"
            assert bridge.everest_manager.active_reservation_id == "7777"

            # 2. Adversarial invalid / mismatched payloads
            invalid_payloads = [
                {},
                {"reservationId": None},
                {"reservationId": ""},
                {"reservationId": 0},
                {"reservationId": 7776},
                {"reservationId": "7778"},
                {"reservationId": -7777},
                {"reservationId": ["7777"]},
                {"reservationId": {"id": 7777}},
                {"reservationId": "7777' OR '1'='1"},
                {"reservationId": None, "connectorId": 1},
            ]

            for payload in invalid_payloads:
                res = await bridge.handle_cancel_reservation(payload)
                assert res.get("status") == "Rejected", (
                    f"Payload {payload} was unexpectedly accepted!"
                )
                # Active reservation MUST remain intact
                assert bridge.everest_manager.active_reservation_id == "7777"
                assert bridge.everest_manager.state == "RESERVED"

            # 3. Valid cancellation with integer 7777
            cancel_res = await bridge.handle_cancel_reservation({"reservationId": 7777})
            assert cancel_res.get("status") == "Accepted"
            assert bridge.everest_manager.active_reservation_id is None
            assert bridge.everest_manager.state == "AVAILABLE"

            # 4. Immediate replay of the same cancellation must be Rejected!
            replay_res = await bridge.handle_cancel_reservation({"reservationId": 7777})
            assert replay_res.get("status") == "Rejected"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cancel_reservation_string_vs_int_type_coercion(self):
        """
        OCPP JSON payloads may represent reservationId as integer or string.
        Ensure both int reservationId and string reservationId cleanly match.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # Case A: Created with int reservationId, cancelled with string
            await bridge.handle_reserve_now({
                "connectorId": 1,
                "reservationId": 12345,
                "idTag": "TAG_A",
                "expiryDate": current_iso_z(),
            })
            assert bridge.everest_manager.state == "RESERVED"
            res_str_cancel = await bridge.handle_cancel_reservation({"reservationId": "12345"})
            assert res_str_cancel.get("status") == "Accepted"
            assert bridge.everest_manager.state == "AVAILABLE"

            # Case B: Created with string reservationId, cancelled with int
            await bridge.handle_reserve_now({
                "connectorId": 1,
                "reservationId": "67890",
                "idTag": "TAG_B",
                "expiryDate": current_iso_z(),
            })
            assert bridge.everest_manager.state == "RESERVED"
            res_int_cancel = await bridge.handle_cancel_reservation({"reservationId": 67890})
            assert res_int_cancel.get("status") == "Accepted"
            assert bridge.everest_manager.state == "AVAILABLE"

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: RemoteStopTransaction String vs Integer tx_id Mismatch, None, Empty
# ══════════════════════════════════════════════════════════════════════════════

class TestRemoteStopTransactionAdversarial:
    """Stress tests RemoteStopTransaction validation and type handling."""

    @pytest.mark.asyncio
    async def test_remote_stop_when_no_active_transaction(self):
        """
        When active_transaction_id is None (idle state):
        Every permutation of empty, None, zero, or arbitrary ID MUST be Rejected.
        """
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        await bridge.start()

        try:
            assert bridge.everest_manager.active_transaction_id is None

            adversarial_payloads = [
                {},
                {"transactionId": None},
                {"transactionId": ""},
                {"transactionId": 0},
                {"transactionId": "0"},
                {"transactionId": -1},
                {"transactionId": 1001},
                {"transactionId": "1001"},
                {"transactionId": [1001]},
                {"transactionId": {"tx": 1001}},
                {"transactionId": False},
                {"transactionId": "NaN"},
            ]

            for payload in adversarial_payloads:
                res = await bridge.handle_remote_stop_transaction(payload)
                assert res.get("status") == "Rejected", (
                    f"RemoteStop unexpectedly succeeded while idle for payload: {payload}"
                )

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_with_active_transaction_adversarial_matrix(self):
        """
        When active_transaction_id is 8008:
        - Mismatched IDs, None, empty payloads, and malformed types must return Rejected.
        - String '8008' or int 8008 must return Accepted.
        - Calling stop again on the terminated transaction must return Rejected.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Start active transaction
            start_res = await bridge.send_start_transaction("TEST_USER")
            tx_id = start_res["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id

            # 2. Inbound adversarial invalid payloads
            invalid_payloads = [
                {},
                {"transactionId": None},
                {"transactionId": ""},
                {"transactionId": 0},
                {"transactionId": tx_id + 1},
                {"transactionId": str(tx_id + 99)},
                {"transactionId": [tx_id]},
                {"transactionId": {"id": tx_id}},
                {"transactionId": f"{tx_id}_extra"},
            ]

            for payload in invalid_payloads:
                res = await bridge.handle_remote_stop_transaction(payload)
                assert res.get("status") == "Rejected", (
                    f"RemoteStop unexpectedly accepted invalid payload {payload} against active tx {tx_id}"
                )
                assert bridge.everest_manager.active_transaction_id == tx_id

            # 3. String match against integer transaction ID
            accepted_res = await bridge.handle_remote_stop_transaction({"transactionId": str(tx_id)})
            assert accepted_res.get("status") == "Accepted"
            assert bridge.everest_manager.active_transaction_id is None

            # 4. Replay of same stop command on already stopped transaction
            replay_res = await bridge.handle_remote_stop_transaction({"transactionId": tx_id})
            assert replay_res.get("status") == "Rejected"

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Reverting from CHARGING to AVAILABLE via Disconnect vs Manual Stop vs Timeout
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionTeardownAndStateReversion:
    """Stress tests cable disconnect, manual stop, and timeout cleanup routines."""

    @pytest.mark.asyncio
    async def test_reverting_charging_to_available_via_cable_disconnect(self):
        """
        When an ongoing charging session transitions to AVAILABLE due to
        EV_DISCONNECTED, verify that:
        - send_stop_transaction is called with reason='EVDisconnected'.
        - active_transaction_id is reset to None.
        - CSMS transaction record is finalized.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # Start session
            start_res = await bridge.send_start_transaction("DISCONNECT_USER")
            tx_id = start_res["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id
            assert tx_id in csms.transactions
            assert csms.transactions[tx_id]["status"] == "Active"

            # Hardware publishes state AVAILABLE (EV unplugged)
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "EV_DISCONNECTED",
                },
            )

            # Invariants:
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_transaction_id is None
            # CSMS transaction record must be marked Stopped
            assert tx_id in csms.transactions
            assert csms.transactions[tx_id]["status"] == "Stopped"
            assert csms.transactions[tx_id]["reason"] == "EVDisconnected"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_reverting_charging_to_available_via_manual_user_stop(self):
        """
        When an ongoing charging session transitions to AVAILABLE due to
        MANUAL_STOP (e.g. user pressed stop button or UI stop), verify clean teardown.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            start_res = await bridge.send_start_transaction("MANUAL_STOP_USER")
            tx_id = start_res["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id

            # Hardware publishes state AVAILABLE with MANUAL_STOP
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "MANUAL_STOP",
                },
            )

            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_transaction_id is None
            assert tx_id in csms.transactions
            assert csms.transactions[tx_id]["status"] == "Stopped"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_staged_teardown_finishing_then_available(self):
        """
        Hardware moves through FINISHING before AVAILABLE.
        Verify that transaction is cleanly terminated once without double-stop errors.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            start_res = await bridge.send_start_transaction("FINISHING_USER")
            tx_id = start_res["transactionId"]

            # Step 1: FINISHING
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "FINISHING",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "CABLE_UNLATCHING",
                },
            )
            assert bridge.everest_manager.active_transaction_id is None
            assert tx_id in csms.transactions
            assert csms.transactions[tx_id]["status"] == "Stopped"

            # Step 2: AVAILABLE
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "SESSION_COMPLETE",
                },
            )
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_transaction_id is None

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_reservation_timeout_cleanses_reservation_id(self):
        """
        When hardware reports RESERVATION_TIMEOUT transition to AVAILABLE,
        the active_reservation_id must be completely flushed.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # 1. Create reservation
            await bridge.handle_reserve_now({
                "connectorId": 1,
                "reservationId": "TIMEOUT_RES_99",
                "idTag": "USER_TIMEOUT",
                "expiryDate": current_iso_z(),
            })
            assert bridge.everest_manager.state == "RESERVED"
            assert bridge.everest_manager.active_reservation_id == "TIMEOUT_RES_99"

            # 2. Hardware 60-second reservation timer expires -> publishes AVAILABLE
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "RESERVATION_TIMEOUT",
                },
            )

            # Invariant: active_reservation_id MUST be flushed to None!
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_reservation_id is None

            # Attempting to cancel the timed-out reservation must be Rejected!
            cancel_res = await bridge.handle_cancel_reservation({"reservationId": "TIMEOUT_RES_99"})
            assert cancel_res.get("status") == "Rejected"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_redundant_available_updates_and_telemetry_idempotency(self):
        """
        Verify that multiple redundant AVAILABLE updates or telemetry streams after
        session termination do not throw unhandled exceptions or resurrect state.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            for _ in range(5):
                await bridge.handle_mqtt_state(
                    "evsarthi/ST001/1/state",
                    {"state": "AVAILABLE", "relay_state": "DE_ENERGIZED"},
                )
                assert bridge.everest_manager.active_transaction_id is None
                assert bridge.everest_manager.active_reservation_id is None

            # Telemetry received while idle
            await bridge.handle_mqtt_telemetry(
                "evsarthi/ST001/1/telemetry",
                {
                    "voltage_v": 230.1,
                    "current_a": 0.0,
                    "power_kw": 0.0,
                    "energy_kwh": 12.34,
                },
            )
            # Board telemetry updated
            assert bridge.everest_board.voltage_v == 230.1
            assert bridge.everest_manager.active_transaction_id is None

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Rapid FSM State Flapping Under High Concurrency
# ══════════════════════════════════════════════════════════════════════════════

class TestRapidStateFlappingAndRaceConditions:
    """Stress tests bridge resilience against rapid asynchronous state flapping."""

    @pytest.mark.asyncio
    async def test_rapid_fsm_cycling_with_concurrent_operations(self):
        """
        Bombard the bridge with concurrent state transitions, auth requests,
        cancellations, and remote stops to ensure no deadlocks or unhandled exceptions.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            states_sequence = [
                {"state": "AVAILABLE", "relay_state": "DE_ENERGIZED"},
                {"state": "RESERVED", "relay_state": "DE_ENERGIZED"},
                {"state": "CHARGING", "relay_state": "ENERGIZED"},
                {"state": "FAULTED", "relay_state": "DE_ENERGIZED", "fault_code": "OVERCURRENT"},
                {"state": "AVAILABLE", "relay_state": "DE_ENERGIZED"},
            ]

            async def state_cycler():
                for s in states_sequence:
                    await bridge.handle_mqtt_state("evsarthi/ST001/1/state", s)
                    await asyncio.sleep(0.01)

            async def auth_sender():
                for i in range(10):
                    await bridge.handle_mqtt_auth(
                        "evsarthi/ST001/1/auth",
                        {"auth_type": "RFID", "credential": {"rfid_uid": f"CYCLE_TAG_{i}"}},
                    )
                    await asyncio.sleep(0.005)

            async def cancel_sender():
                for i in range(10):
                    await bridge.handle_cancel_reservation({"reservationId": i + 500})
                    await asyncio.sleep(0.005)

            async def remote_stop_sender():
                for i in range(10):
                    await bridge.handle_remote_stop_transaction({"transactionId": i + 1000})
                    await asyncio.sleep(0.005)

            # Run all concurrently
            await asyncio.gather(
                state_cycler(),
                auth_sender(),
                cancel_sender(),
                remote_stop_sender(),
            )

            # Bridge must still be operational
            assert bridge.everest_manager.state in ["AVAILABLE", "FAULTED", "RESERVED", "CHARGING"]

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: Concurrency Contention, Idempotency & Inbound Fuzzing
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyContentionAndIdempotency:
    """Stress tests high contention race conditions and malformed payload fuzzing."""

    @pytest.mark.asyncio
    async def test_50_concurrent_cancel_reservation_contention(self):
        """
        When 50 concurrent CancelReservation calls race for the same active reservation,
        exactly ONE must succeed (Accepted), and 49 must be Rejected.
        Zero double-cancellations permitted.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # Setup active reservation
            await bridge.handle_reserve_now({
                "connectorId": 1,
                "reservationId": 99999,
                "idTag": "RACE_USER",
                "expiryDate": current_iso_z(),
            })
            assert bridge.everest_manager.state == "RESERVED"
            assert bridge.everest_manager.active_reservation_id == "99999"

            # 50 concurrent cancellations
            coros = [
                bridge.handle_cancel_reservation({"reservationId": 99999})
                for _ in range(50)
            ]
            results = await asyncio.gather(*coros)

            accepted_count = sum(1 for r in results if r.get("status") == "Accepted")
            rejected_count = sum(1 for r in results if r.get("status") == "Rejected")

            assert accepted_count == 1, f"Expected exactly 1 Accepted, got {accepted_count}"
            assert rejected_count == 49, f"Expected 49 Rejected, got {rejected_count}"
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_reservation_id is None

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_50_concurrent_remote_stop_transaction_contention(self):
        """
        When 50 concurrent RemoteStopTransaction calls race for the same active transaction,
        exactly ONE must succeed (Accepted), and 49 must be Rejected.
        Zero double-stops permitted.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # Start active transaction
            start_res = await bridge.send_start_transaction("RACE_STOP_USER")
            tx_id = start_res["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id

            # 50 concurrent remote stop requests
            coros = [
                bridge.handle_remote_stop_transaction({"transactionId": tx_id})
                for _ in range(50)
            ]
            results = await asyncio.gather(*coros)

            accepted_count = sum(1 for r in results if r.get("status") == "Accepted")
            rejected_count = sum(1 for r in results if r.get("status") == "Rejected")

            assert accepted_count == 1, f"Expected exactly 1 Accepted, got {accepted_count}"
            assert rejected_count == 49, f"Expected 49 Rejected, got {rejected_count}"
            assert bridge.everest_manager.active_transaction_id is None
            assert csms.transactions[tx_id]["status"] == "Stopped"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_malformed_and_fuzz_payloads_in_all_handlers(self):
        """
        Send garbage, None, empty dicts, primitive types, and oversized strings
        to all inbound MQTT and CSMS handlers to ensure robust error handling without unhandled crashes.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            fuzz_payloads = [
                None,
                "",
                "just a string",
                12345,
                [],
                {},
                {"garbage_key": "garbage_value"},
                {"state": None, "relay_state": None},
                {"credential": None},
                {"connectorId": 1},
            ]

            for payload in fuzz_payloads:
                # 1. State handler
                await bridge.handle_mqtt_state("evsarthi/ST001/1/state", payload)

                # 2. Telemetry handler
                await bridge.handle_mqtt_telemetry("evsarthi/ST001/1/telemetry", payload)

                # 3. Auth handler
                await bridge.handle_mqtt_auth("evsarthi/ST001/1/auth", payload)

                # 4. ReserveNow handler (dict expected)
                if isinstance(payload, dict):
                    res_reserve = await bridge.handle_reserve_now(payload)
                    assert res_reserve.get("status") in ["Rejected", "Faulted", "Occupied", "Accepted"]

                # 5. RemoteStop handler (dict expected)
                if isinstance(payload, dict):
                    res_stop = await bridge.handle_remote_stop_transaction(payload)
                    assert res_stop.get("status") in ["Rejected", "Accepted"]

                # 6. UnlockConnector handler (dict expected)
                if isinstance(payload, dict):
                    res_unlock = await bridge.handle_unlock_connector(payload)
                    assert res_unlock.get("status") in ["Unlocked", "NotSupported"]

            # After all fuzzing, bridge must remain intact and functional
            assert bridge.everest_manager.state in ["AVAILABLE", "FAULTED", "RESERVED", "CHARGING"]
            hb_res = await bridge.send_heartbeat()
            assert "currentTime" in hb_res

        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: RemoteStartTransaction Adversarial Vulnerability Challenges
# ══════════════════════════════════════════════════════════════════════════════

class TestRemoteStartTransactionAdversarialChallenges:
    """
    Adversarial challenge tests revealing vulnerabilities in handle_remote_start_transaction.
    These tests demonstrate failure modes where the contactor relay is energized
    prematurely, unauthenticated, or on mismatched connector IDs.
    """

    @pytest.mark.asyncio
    async def test_remote_start_transaction_malformed_connector_crashes_after_relay_energized(self):
        """
        VULNERABILITY CHALLENGE 1:
        When RemoteStartTransaction receives a malformed connectorId string ('invalid'),
        the bridge publishes relay_action='ENERGIZE' to MQTT BEFORE executing int(conn_id).
        int(conn_id) then raises an unhandled ValueError, crashing the inbound frame handler
        and leaving the high-voltage relay energized with NO active transaction!
        Safe invariant: Should validate connectorId and return Rejected WITHOUT energizing relay.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            # Send RemoteStart with invalid connectorId
            try:
                res = await bridge.handle_remote_start_transaction({
                    "connectorId": "invalid",
                    "idTag": "USER_MALFORMED",
                })
            except ValueError:
                # Direct observation: handler crashed with ValueError!
                # Check whether relay was already commanded to ENERGIZE
                energized_cmds = [
                    cmd for cmd in mqtt_mock.published_commands
                    if cmd.get("relay_action") == "ENERGIZE"
                ]
                assert len(energized_cmds) > 0, "Handler crashed without energizing relay"
                assert False, (
                    "CRITICAL SAFETY VULNERABILITY DETECTED in handle_remote_start_transaction: "
                    "Bridge published relay_action='ENERGIZE' to hardware, then crashed with "
                    "ValueError: invalid literal for int() with base 10: 'invalid', leaving the contactor energized "
                    "with NO active transaction!"
                )

            assert res.get("status") == "Rejected"
            assert len(mqtt_mock.published_commands) == 0

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_transaction_mismatched_connector_id_vulnerability(self):
        """
        VULNERABILITY CHALLENGE 2:
        When RemoteStartTransaction is dispatched for a different connector (e.g. connectorId=99
        or connectorId=2 on a single-connector station ST001/1), handle_remote_start_transaction
        fails to validate connectorId (unlike handle_reserve_now and handle_unlock_connector).
        It accepts the command and energizes connector 1's relay for a command intended for connector 99!
        Safe invariant: Should return Rejected when connectorId does not match this bridge connector.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            res = await bridge.handle_remote_start_transaction({
                "connectorId": 99,
                "idTag": "USER_CONN_99",
            })

            # Check if bridge accepted command for mismatched connector
            is_vulnerable = (res.get("status") == "Accepted")
            if is_vulnerable:
                # Check if relay on connector 1 was energized
                energized_cmds = [
                    cmd for cmd in mqtt_mock.published_commands
                    if cmd.get("relay_action") == "ENERGIZE"
                ]
                assert not is_vulnerable, (
                    f"VULNERABILITY DETECTED in handle_remote_start_transaction: "
                    f"Bridge for connector 1 accepted RemoteStart for connector 99 and published "
                    f"relay_action='ENERGIZE' on connector 1! Status: {res.get('status')}"
                )

            assert res.get("status") == "Rejected"

        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_transaction_missing_id_tag_vulnerability(self):
        """
        VULNERABILITY CHALLENGE 3:
        In OCPP 1.6-J Section 4.6, idTag is a required property of RemoteStartTransaction.req.
        When RemoteStartTransaction is called with empty payload or missing idTag (idTag=None),
        handle_remote_start_transaction fails to validate idTag, energizes the contactor relay,
        and initiates an unauthenticated transaction with idTag=None.
        Safe invariant: Should return Rejected when idTag is missing or empty.
        """
        csms = VirtualCSMS()
        mqtt_mock = MockMQTTCollector()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms,
        )
        await bridge.start()

        try:
            res = await bridge.handle_remote_start_transaction({
                "connectorId": 1,
                # Missing idTag!
            })

            is_vulnerable = (res.get("status") == "Accepted")
            if is_vulnerable:
                energized_cmds = [
                    cmd for cmd in mqtt_mock.published_commands
                    if cmd.get("relay_action") == "ENERGIZE"
                ]
                assert not is_vulnerable, (
                    "VULNERABILITY DETECTED in handle_remote_start_transaction: "
                    "Bridge accepted RemoteStartTransaction with missing/null idTag, energizing the relay "
                    "for an anonymous unauthenticated session!"
                )

            assert res.get("status") == "Rejected"

        finally:
            await bridge.stop()

