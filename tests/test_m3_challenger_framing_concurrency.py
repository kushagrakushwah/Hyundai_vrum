"""
tests/test_m3_challenger_framing_concurrency.py
===============================================
Adversarial Stress Harness for Milestone 3 (SteVe OCPP 1.6-J CSMS & EVerest Bridge).

Empirically challenges:
1. Protocol Framing Corruption & Fuzzing (truncated frames, invalid MessageTypeIds, empty arrays, malformed JSON).
2. State Machine & Concurrency Stress (concurrent ReserveNow, rapid cancel/re-reserve, telemetry during tx lifecycle).
3. Transaction ID monotonicity, race condition immunity, and authorization during fault state.
"""

import asyncio
import json
import time
import pytest
from typing import List, Dict, Any

from smartreserve.smartreserve_backend.ocpp_bridge import (
    OCPPBridge,
    VirtualCSMS,
    OCPPMessageType,
    OCPPCallErrorCode,
    OCPPCallError,
    parse_frame,
    serialize_call,
    serialize_call_result,
    serialize_call_error,
)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Protocol Framing Corruption & Fuzzing Stress Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestProtocolFramingCorruption:
    """Stress tests protocol parser and CSMS raw frame handler against adversarial inputs."""

    @pytest.mark.asyncio
    async def test_truncated_and_malformed_json(self):
        csms = VirtualCSMS()
        malformed_inputs = [
            "",
            "   \n\t  ",
            "{",
            "}",
            "[",
            "]",
            "[2,",
            '[2, "uid"',
            '[2, "uid",',
            '[2, "uid", "Heartbeat"',
            '[2, "uid", "Heartbeat",',
            '{"action": "Heartbeat"}',
            "not json at all",
            "<xml></xml>",
            "None",
            "undefined",
            "NaN",
            "true",
            "12345",
            '"just a naked string"',
        ]

        for payload in malformed_inputs:
            # 1. Directly test parse_frame
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(payload)
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION.value

            # 2. Test handle_raw_frame returns a valid CallError frame rather than raising unhandled exception
            resp = await csms.handle_raw_frame("BOX_01", payload)
            assert isinstance(resp, str), f"Expected string response for {payload!r}"
            parsed_resp = json.loads(resp)
            assert isinstance(parsed_resp, list)
            assert parsed_resp[0] == OCPPMessageType.CALL_ERROR
            assert parsed_resp[2] == OCPPCallErrorCode.FORMATION_VIOLATION.value

    @pytest.mark.asyncio
    async def test_invalid_message_type_ids(self):
        csms = VirtualCSMS()
        invalid_types = [-99, -1, 0, 1, 5, 6, 7, 99, 1000]

        for msg_type in invalid_types:
            frame = [msg_type, f"uid_{msg_type}", "Heartbeat", {}]
            frame_str = json.dumps(frame)

            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(frame)
            assert exc_info.value.error_code == OCPPCallErrorCode.PROTOCOL_ERROR.value

            # Verify CSMS returns ProtocolError CallError frame
            resp = await csms.handle_raw_frame("BOX_01", frame_str)
            parsed_resp = json.loads(resp)
            assert parsed_resp[0] == OCPPMessageType.CALL_ERROR
            assert parsed_resp[1] == f"uid_{msg_type}"
            assert parsed_resp[2] == OCPPCallErrorCode.PROTOCOL_ERROR.value

    @pytest.mark.asyncio
    async def test_empty_and_boundary_array_lengths(self):
        csms = VirtualCSMS()
        boundary_cases = [
            ([], "empty list", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([2], "Call with 1 element", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([2, "uid1"], "Call with 2 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([2, "uid2", "Heartbeat"], "Call with 3 elements (missing payload)", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([2, "uid3", "Heartbeat", {}, "unexpected_extra"], "Call with 5 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([3], "CallResult with 1 element", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([3, "uid4"], "CallResult with 2 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([3, "uid5", {}, "extra"], "CallResult with 4 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([4], "CallError with 1 element", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([4, "uid6"], "CallError with 2 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([4, "uid7", "ErrCode"], "CallError with 3 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
            ([4, "uid8", "ErrCode", "Desc", {}, "extra6"], "CallError with 6 elements", OCPPCallErrorCode.FORMATION_VIOLATION.value),
        ]

        for frame, desc, expected_code in boundary_cases:
            frame_str = json.dumps(frame)
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(frame)
            assert exc_info.value.error_code == expected_code, f"Failed on {desc}"

            resp = await csms.handle_raw_frame("BOX_01", frame_str)
            parsed_resp = json.loads(resp)
            assert parsed_resp[0] == OCPPMessageType.CALL_ERROR
            assert parsed_resp[2] == expected_code

    @pytest.mark.asyncio
    async def test_invalid_payload_and_field_types(self):
        csms = VirtualCSMS()
        type_violation_cases = [
            # Invalid UniqueId types
            ([2, 12345, "Heartbeat", {}], "integer uniqueId"),
            ([2, None, "Heartbeat", {}], "null uniqueId"),
            ([2, True, "Heartbeat", {}], "bool uniqueId"),
            ([2, ["list_uid"], "Heartbeat", {}], "list uniqueId"),
            ([2, {"k": "v"}, "Heartbeat", {}], "dict uniqueId"),
            # Invalid Action types
            ([2, "uid_act_int", 999, {}], "integer action"),
            ([2, "uid_act_none", None, {}], "null action"),
            ([2, "uid_act_list", ["Heartbeat"], {}], "list action"),
            # Invalid Call Payload types
            ([2, "uid_pay_str", "Heartbeat", "string_payload"], "string payload"),
            ([2, "uid_pay_int", "Heartbeat", 12345], "int payload"),
            ([2, "uid_pay_list", "Heartbeat", [1, 2, 3]], "list payload"),
            ([2, "uid_pay_none", "Heartbeat", None], "null payload"),
            # Invalid CallResult Payload types
            ([3, "uid_res_str", "string_payload"], "CallResult string payload"),
            ([3, "uid_res_list", [1, 2]], "CallResult list payload"),
            ([3, "uid_res_none", None], "CallResult null payload"),
        ]

        for frame, desc in type_violation_cases:
            frame_str = json.dumps(frame)
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(frame)
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION.value, f"Failed on {desc}"

            resp = await csms.handle_raw_frame("BOX_01", frame_str)
            parsed_resp = json.loads(resp)
            assert parsed_resp[0] == OCPPMessageType.CALL_ERROR
            assert parsed_resp[2] == OCPPCallErrorCode.FORMATION_VIOLATION.value

    @pytest.mark.asyncio
    async def test_fuzzing_with_large_and_adversarial_unique_ids(self):
        csms = VirtualCSMS()
        adversarial_uids = [
            "A" * 100_000,                                 # 100 KB uniqueId string
            "⚡🚗🔋 Hyundai EV Sarthi 🚀",                   # Multi-byte UTF-8 emojis
            "\\u0000\\u0001\\u001f\\u007f",                # Escaped control characters
            "<script>alert('xss')</script>",               # Injection string
            "'; DROP TABLE ocpp_tag; --",                  # SQL injection string
            "{'nested': 'json'}",                          # Embedded JSON string
        ]

        for uid in adversarial_uids:
            frame_str = json.dumps([OCPPMessageType.CALL, uid, "Heartbeat", {}])
            resp = await csms.handle_raw_frame("BOX_01", frame_str)
            parsed_resp = json.loads(resp)
            assert parsed_resp[0] == OCPPMessageType.CALL_RESULT
            assert parsed_resp[1] == uid
            assert "currentTime" in parsed_resp[2]

    @pytest.mark.asyncio
    async def test_unimplemented_csms_action_fuzzing(self):
        csms = VirtualCSMS()
        unsupported_actions = [
            "NonExistentAction",
            "FuzzAction_12345",
            "DataTransfer",
            "FirmwareStatusNotification",
            "DiagnosticsStatusNotification",
        ]

        for action in unsupported_actions:
            frame_str = json.dumps([OCPPMessageType.CALL, f"uid_{action}", action, {}])
            resp = await csms.handle_raw_frame("BOX_01", frame_str)
            parsed_resp = json.loads(resp)
            assert parsed_resp[0] == OCPPMessageType.CALL_ERROR
            assert parsed_resp[1] == f"uid_{action}"
            assert parsed_resp[2] == OCPPCallErrorCode.NOT_IMPLEMENTED.value


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Concurrency & State Machine Race Condition Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyAndStateMachine:
    """Stress tests concurrency safety, state transitions, and race condition immunity."""

    @pytest.mark.asyncio
    async def test_concurrent_reserve_now_race_condition(self):
        """
        Adversarial Test: 50 concurrent ReserveNow calls targeting the same AVAILABLE connector.
        Specification invariant: Exactly 1 caller MUST succeed (Accepted).
        All 49 other concurrent callers MUST receive Occupied.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()

        try:
            assert bridge.everest_manager.state == "AVAILABLE"

            async def do_reserve(idx: int):
                return await csms.send_reserve_now(
                    charge_box_id=bridge.charge_box_id,
                    connector_id=1,
                    expiry_date="2026-11-15T18:30:00Z",
                    id_tag=f"TAG_{idx}",
                    reservation_id=2000 + idx
                )

            # Fire 50 simultaneous reserve requests
            results = await asyncio.gather(*[do_reserve(i) for i in range(50)])

            accepted = [r for r in results if r.get("status") == "Accepted"]
            occupied = [r for r in results if r.get("status") == "Occupied"]

            assert len(accepted) == 1, f"Expected exactly 1 Accepted reservation, got {len(accepted)}"
            assert len(occupied) == 49, f"Expected 49 Occupied rejections, got {len(occupied)}"
            assert bridge.everest_manager.state == "RESERVED"
            assert len(csms.reservations) == 1
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cancel_reservation_id_validation_vulnerability(self):
        """
        Adversarial Test: Cancellation with wrong reservationId.
        Specification: OCPP 1.6 - Charge Point SHALL reject CancelReservation if the reservationId
        does NOT match an existing reservation.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()

        try:
            # 1. Create active reservation with ID 777
            res = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T18:30:00Z",
                id_tag="VALID_USER",
                reservation_id=777
            )
            assert res.get("status") == "Accepted"
            assert bridge.everest_manager.state == "RESERVED"
            assert str(bridge.everest_manager.active_reservation_id) == "777"

            # 2. Attacker / wrong client attempts to cancel with non-matching ID 9999
            cancel_wrong = await csms.send_cancel_reservation(bridge.charge_box_id, 9999)

            # CHALLENGER AUDIT ASSERTION:
            # If the bridge accepts this and wipes reservation 777, this is a CRITICAL state bug!
            is_vulnerable = (cancel_wrong.get("status") == "Accepted" and bridge.everest_manager.state == "AVAILABLE")
            
            # We record this empirical finding explicitly:
            assert not is_vulnerable, (
                "VULNERABILITY DETECTED in handle_cancel_reservation: "
                "Non-matching reservationId 9999 was Accepted and wiped out active reservation 777!"
            )
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_rapid_cancel_and_re_reserve_cycling(self):
        """Stress tests 50 rapid sequential reserve-then-cancel cycles."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()

        try:
            for i in range(50):
                res_id = 3000 + i
                # Reserve
                r = await csms.send_reserve_now(
                    charge_box_id=bridge.charge_box_id,
                    connector_id=1,
                    expiry_date="2026-11-15T18:30:00Z",
                    id_tag=f"USER_{i}",
                    reservation_id=res_id
                )
                assert r.get("status") == "Accepted"
                assert bridge.everest_manager.state == "RESERVED"

                # Cancel
                c = await csms.send_cancel_reservation(bridge.charge_box_id, res_id)
                assert c.get("status") == "Accepted"
                assert bridge.everest_manager.state == "AVAILABLE"
                assert bridge.everest_manager.active_reservation_id is None

            assert len(csms.reservations) == 0
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_concurrent_telemetry_streaming_during_tx_lifecycle(self):
        """
        Stress tests high-frequency telemetry ingestion while starting and stopping transactions.
        Verifies:
        - No unhandled exceptions or race conditions during rapid state transitions.
        - MeterValues sampled values are strictly strings.
        - Transaction ID is present and matches active session during charging.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()

        stop_telemetry = asyncio.Event()
        telemetry_count = 0

        async def telemetry_streamer():
            nonlocal telemetry_count
            v = 230.0
            c = 16.0
            e = 1.0
            while not stop_telemetry.is_set():
                await bridge.handle_mqtt_telemetry(
                    f"evsarthi/{bridge.station_id}/{bridge.connector_id}/telemetry",
                    {
                        "voltage_v": v,
                        "current_a": c,
                        "power_kw": round((v * c) / 1000.0, 3),
                        "energy_kwh": e,
                        "millis": int(time.time() * 1000)
                    }
                )
                telemetry_count += 1
                e += 0.01
                await asyncio.sleep(0.01)

        try:
            # Start background high-frequency telemetry
            streamer_task = asyncio.create_task(telemetry_streamer())

            # Perform 5 rapid start/stop transaction cycles while telemetry streams
            for i in range(5):
                await asyncio.sleep(0.03)
                start_res = await bridge.send_start_transaction(
                    id_tag=f"USER_{i}",
                    connector_id=1,
                    meter_start=int(i * 100)
                )
                tx_id = start_res.get("transactionId")
                assert tx_id is not None
                assert bridge.everest_manager.state == "CHARGING"
                assert bridge.everest_manager.active_transaction_id == tx_id

                await asyncio.sleep(0.05)

                stop_res = await bridge.send_stop_transaction(
                    transaction_id=tx_id,
                    meter_stop=int(i * 100 + 50),
                    reason="Local"
                )
                assert stop_res.get("idTagInfo", {}).get("status") == "Accepted"
                assert bridge.everest_manager.state == "AVAILABLE"
                assert bridge.everest_manager.active_transaction_id is None

            stop_telemetry.set()
            await streamer_task

            # Verify telemetry log in CSMS
            meter_logs = csms.meter_values_log
            assert len(meter_logs) >= 10
            for mv_entry in meter_logs:
                for mv in mv_entry.get("meterValue", []):
                    for sv in mv.get("sampledValue", []):
                        assert isinstance(sv["value"], str), f"SampledValue value must be string: {sv}"

            # Verify SD write count tracked in EVerest UI
            assert bridge.everest_ui.sd_log_count == telemetry_count
        finally:
            stop_telemetry.set()
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_transaction_id_monotonicity_and_concurrency(self):
        """
        Stress tests 30 concurrent StartTransaction calls.
        Verifies that VirtualCSMS produces monotonically increasing, strictly unique transaction IDs.
        """
        csms = VirtualCSMS()

        async def start_tx(i: int):
            return await csms.process_call(
                charge_box_id="BOX_01",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": f"TAG_{i}",
                    "meterStart": i * 10,
                    "timestamp": "2026-11-15T18:00:00Z"
                },
                unique_id=f"uid_tx_{i}"
            )

        results = await asyncio.gather(*[start_tx(i) for i in range(30)])
        tx_ids = [payload["transactionId"] for status, payload in results if status == OCPPMessageType.CALL_RESULT]

        assert len(tx_ids) == 30
        assert len(set(tx_ids)) == 30, "Transaction IDs must be strictly unique"
        assert sorted(tx_ids) == tx_ids, "Transaction IDs must be strictly monotonically increasing"
        assert min(tx_ids) == 1001
        assert max(tx_ids) == 1030

    @pytest.mark.asyncio
    async def test_authorization_attempt_during_faulted_state(self):
        """
        Adversarial Test: User scans valid RFID card while connector is in FAULTED state.
        Safety Invariant: Bridge must NEVER energize the contactor relay while hardware is faulted!
        """
        csms = VirtualCSMS()
        mock_messages = []

        class MockMQTT:
            async def publish_cmd(self, **kwargs):
                mock_messages.append(kwargs)

        mqtt_mock = MockMQTT()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mqtt_mock,
            virtual_csms=csms
        )
        await bridge.start()

        try:
            # 1. Trip the hardware into FAULTED state (overcurrent failure)
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "FAULTED",
                    "relay_state": "DE_ENERGIZED",
                    "fault_code": "OVERCURRENT",
                    "transition_reason": "Current exceeded 29.7A safety threshold"
                }
            )
            assert bridge.everest_manager.state == "FAULTED"
            assert bridge.everest_board.is_tripped is True

            # 2. Simulate RFID swipe with whitelisted tag
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "DEADBEEF"},
                    "current_state": "FAULTED"
                }
            )

            # Check published command
            assert len(mock_messages) >= 1
            last_cmd = mock_messages[-1]

            # CHALLENGER SAFETY AUDIT ASSERTION:
            # In a FAULTED state, contactor relay MUST NOT be energized!
            is_safety_violation = (last_cmd.get("relay_action") == "ENERGIZE")
            assert not is_safety_violation, (
                "SAFETY VIOLATION DETECTED: Bridge energized the contactor (relay_action='ENERGIZE') "
                "for whitelisted RFID while the connector was in FAULTED state!"
            )
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_transaction_null_tx_id_vulnerability(self):
        """
        Adversarial Test: RemoteStopTransaction when no transaction is active.
        Calling with no transactionId or empty payload when active_transaction_id is None.
        Invariant: Should return Rejected, not Accepted.
        """
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        await bridge.start()
        try:
            assert bridge.everest_manager.active_transaction_id is None
            res = await bridge.handle_remote_stop_transaction({})
            is_vulnerable = (res.get("status") == "Accepted")
            assert not is_vulnerable, (
                "VULNERABILITY DETECTED: handle_remote_stop_transaction returned Accepted "
                "when no transaction was active and transactionId was null/empty!"
            )
        finally:
            await bridge.stop()

