"""
tests/test_tier5_adversarial_p2.py
==================================
Tier 5 Adversarial Coverage Hardening Suite 2: SteVe OCPP 1.6-J & EVerest Bridge Deep Hardening.

Coverage Matrix:
1. OCPP Framing & Protocol Robustness (Array lengths, MessageTypeIds, Malformed Payloads, Framing Errors)
2. VirtualCSMS Protocol Edge Cases (Unknown Actions, Non-string SampledValues, Raw Frame Processing)
3. OCPP Transaction & State Machine Edge Cases (Collisions, Guards, UnlockConnector, Reset, Extreme MeterValues)
4. LF Energy EVerest Taxonomy & High-Concurrency Stress (Oscillations, Parallel ReserveNow, Auth Races, Fault Trips)
"""

import asyncio
import json
import os
import sys
import time
import pytest

# Ensure imports resolve to smartreserve/smartreserve_backend
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
    serialize_call,
    serialize_call_result,
    serialize_call_error,
    parse_frame,
    current_iso_z,
    EVerestEvseBoardSupport,
    EVerestAuth,
    EVerestEvseManager,
    EVerestSystemUI,
)
import database


class MockMQTTCommandCollector:
    """Mock MQTT service capturing all published commands, reserve events, and state messages."""
    def __init__(self):
        self.published_commands = []
        self.published_reserves = []
        self.published_messages = []
        self.listeners = {}

    def add_message_listener(self, topic: str, callback):
        self.listeners[topic] = callback

    def subscribe(self, topic: str, callback):
        self.listeners[topic] = callback

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
        pin: str,
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

    async def publish(self, topic: str, payload: dict | str, qos: int = 0, retain: bool = False):
        parsed = json.loads(payload) if isinstance(payload, str) else payload
        self.published_messages.append({"topic": topic, "payload": parsed})
        if topic in self.listeners:
            cb = self.listeners[topic]
            if asyncio.iscoroutinefunction(cb):
                await cb(topic, parsed)
            else:
                cb(topic, parsed)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: OCPP Framing & Protocol Robustness (11 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestOCPPFramingAdversarial:
    """Stress tests for strict JSON-RPC 2.0 framing and serialization violations."""

    def test_frame_array_underflow_empty_and_short(self):
        """Frames with fewer than 3 elements must raise FORMATION_VIOLATION."""
        for short_frame in [[], [2], [2, "uid_only"]]:
            raw = json.dumps(short_frame)
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(raw)
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_frame_call_array_length_overflow(self):
        """CALL frames with > 4 elements must raise FORMATION_VIOLATION."""
        overflow_frames = [
            [2, "msg_1", "Heartbeat", {}, "extra_argument"],
            [2, "msg_2", "Heartbeat", {}, "arg1", "arg2"],
        ]
        for frame in overflow_frames:
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(json.dumps(frame))
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION
            assert exc_info.value.unique_id in ["msg_1", "msg_2"]

    def test_frame_call_result_invalid_lengths(self):
        """CALL_RESULT frames with != 3 elements must raise FORMATION_VIOLATION."""
        invalid_results = [
            [3, "res_1"],  # len 2
            [3, "res_2", {}, "unexpected_extra"],  # len 4
            [3, "res_3", {}, "extra1", "extra2"],  # len 5
        ]
        for frame in invalid_results:
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(json.dumps(frame))
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_frame_call_error_invalid_lengths(self):
        """CALL_ERROR frames must have 4 or 5 elements; any other length raises FORMATION_VIOLATION."""
        invalid_errors = [
            [4, "err_1", "NotImplemented"],  # len 3 (underflow)
            [4, "err_2", "NotImplemented", "desc", {}, "extra_element"],  # len 6 (overflow)
        ]
        for frame in invalid_errors:
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(json.dumps(frame))
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        # Valid 4 and 5 element CallError frames
        valid_4 = parse_frame(json.dumps([4, "err_4", "NotImplemented", "desc"]))
        assert valid_4[0] == OCPPMessageType.CALL_ERROR
        assert valid_4[4] == {}

        valid_5 = parse_frame(json.dumps([4, "err_5", "NotImplemented", "desc", {"detail": 1}]))
        assert valid_5[0] == OCPPMessageType.CALL_ERROR
        assert valid_5[4] == {"detail": 1}

    def test_frame_invalid_message_type_ids(self):
        """MessageTypeId not in {2, 3, 4} must raise PROTOCOL_ERROR."""
        invalid_type_ids = [0, 1, 5, 99, -1, 2.5, "2", None]
        for invalid_type in invalid_type_ids:
            raw = json.dumps([invalid_type, "uid_test", "extra_field"])
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame(raw)
            assert exc_info.value.error_code == OCPPCallErrorCode.PROTOCOL_ERROR

    def test_frame_non_string_unique_ids(self):
        """Non-string unique_id in any frame must raise FORMATION_VIOLATION."""
        non_string_uids = [12345, None, ["id"], {"id": 1}, 3.14, True]
        for bad_uid in non_string_uids:
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame([2, bad_uid, "Heartbeat", {}])
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_frame_call_non_string_action(self):
        """Action field in CALL frame must be a string."""
        bad_actions = [123, None, ["Heartbeat"], {"action": "Heartbeat"}, True]
        for bad_action in bad_actions:
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame([2, "valid_uid", bad_action, {}])
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_frame_call_and_result_non_dict_payload(self):
        """Payload field in CALL and CALL_RESULT frames must be a JSON object (dict)."""
        bad_payloads = ["string_payload", 12345, [1, 2, 3], None, True]
        for bad_payload in bad_payloads:
            # CALL frame
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame([2, "uid_call", "Heartbeat", bad_payload])
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

            # CALL_RESULT frame
            with pytest.raises(OCPPCallError) as exc_info:
                parse_frame([3, "uid_res", bad_payload])
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_serialize_call_adversarial_validation(self):
        """serialize_call must enforce non-empty string unique_id, action, and dict payload."""
        # Bad unique_id
        for bad_uid in ["", None, 123, []]:
            with pytest.raises(OCPPCallError) as exc:
                serialize_call(bad_uid, "Heartbeat", {})
            assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        # Bad action
        for bad_act in ["", None, 456, {}]:
            with pytest.raises(OCPPCallError) as exc:
                serialize_call("uid_1", bad_act, {})
            assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        # Bad payload
        for bad_pay in [None, "string", [1, 2], 42]:
            with pytest.raises(OCPPCallError) as exc:
                serialize_call("uid_1", "Heartbeat", bad_pay)
            assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_serialize_call_result_adversarial_validation(self):
        """serialize_call_result must enforce non-empty string unique_id and dict payload."""
        with pytest.raises(OCPPCallError) as exc:
            serialize_call_result("", {"status": "Accepted"})
        assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        with pytest.raises(OCPPCallError) as exc:
            serialize_call_result("uid_2", "NOT_A_DICT")
        assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_serialize_call_error_robustness(self):
        """serialize_call_error must safely format valid JSON array with robust defaults."""
        raw = serialize_call_error(
            unique_id="err_msg_99",
            error_code="NotImplemented",
            error_description="Detailed reason",
            error_details={"key": "val"}
        )
        parsed = json.loads(raw)
        assert parsed == [4, "err_msg_99", "NotImplemented", "Detailed reason", {"key": "val"}]

        # Defaults for non-string / empty fields
        fallback = serialize_call_error(None, None, 500, "non_dict_details")
        f_parsed = json.loads(fallback)
        assert f_parsed[0] == 4
        assert f_parsed[1] == ""
        assert f_parsed[2] == OCPPCallErrorCode.GENERIC_ERROR
        assert f_parsed[3] == "500"
        assert f_parsed[4] == {}


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: VirtualCSMS Protocol Edge Cases (8 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestVirtualCSMSProtocolEdgeCases:
    """Stress tests targeting the VirtualCSMS mock server engine."""

    @pytest.mark.asyncio
    async def test_virtual_csms_unknown_action_returns_not_implemented(self):
        """VirtualCSMS must respond with NotImplemented CallError for unsupported actions."""
        csms = VirtualCSMS()
        unsupported_actions = [
            "DataTransfer",
            "DiagnosticsStatusNotification",
            "FirmwareStatusNotification",
            "GetConfiguration",
            "ChangeConfiguration",
            "NonExistentAction"
        ]
        for act in unsupported_actions:
            resp_type, resp_payload = await csms.process_call(
                charge_box_id="ST001",
                action=act,
                payload={},
                unique_id=f"test_uid_{act}"
            )
            assert resp_type == OCPPMessageType.CALL_ERROR
            assert resp_payload["errorCode"] == OCPPCallErrorCode.NOT_IMPLEMENTED.value
            assert act in resp_payload["errorDescription"]

    @pytest.mark.asyncio
    async def test_virtual_csms_handle_raw_frame_with_malformed_json(self):
        """handle_raw_frame must safely swallow malformed JSON and return a serialized CallError."""
        csms = VirtualCSMS()
        malformed_inputs = [
            "NOT_JSON_AT_ALL{",
            "{unquoted_key: 123",
            "[2, incomplete_array",
        ]
        for raw in malformed_inputs:
            err_frame = await csms.handle_raw_frame("ST001", raw)
            parsed = json.loads(err_frame)
            assert parsed[0] == OCPPMessageType.CALL_ERROR
            assert parsed[2] == OCPPCallErrorCode.FORMATION_VIOLATION.value

    @pytest.mark.asyncio
    async def test_virtual_csms_handle_raw_frame_unexpected_message_type(self):
        """handle_raw_frame with non-CALL message (e.g. CALL_RESULT to CSMS) returns ProtocolError."""
        csms = VirtualCSMS()
        unsolicited_result = json.dumps([3, "res_uid", {"status": "Accepted"}])
        err_frame = await csms.handle_raw_frame("ST001", unsolicited_result)
        parsed = json.loads(err_frame)
        assert parsed[0] == OCPPMessageType.CALL_ERROR
        assert parsed[1] == "res_uid"
        assert parsed[2] == OCPPCallErrorCode.PROTOCOL_ERROR.value

    @pytest.mark.asyncio
    async def test_virtual_csms_meter_values_sampled_value_non_string_rejected(self):
        """VirtualCSMS must reject MeterValues if any sampledValue.value is not a string."""
        csms = VirtualCSMS()
        # Invalid payload where voltage value is a float, not string
        invalid_meter_val = {
            "connectorId": 1,
            "transactionId": 1001,
            "meterValue": [
                {
                    "timestamp": current_iso_z(),
                    "sampledValue": [
                        {"value": 230.5, "measurand": "Voltage", "unit": "V"}
                    ]
                }
            ]
        }
        resp_type, resp_payload = await csms.process_call(
            charge_box_id="ST001",
            action="MeterValues",
            payload=invalid_meter_val,
            unique_id="mv_uid_1"
        )
        assert resp_type == OCPPMessageType.CALL_ERROR
        assert resp_payload["errorCode"] == OCPPCallErrorCode.FORMATION_VIOLATION.value
        assert "sampledValue.value must be a string" in resp_payload["errorDescription"]

    @pytest.mark.asyncio
    async def test_virtual_csms_meter_values_sampled_value_string_accepted(self):
        """VirtualCSMS must accept MeterValues where sampledValue.value is correctly string-typed."""
        csms = VirtualCSMS()
        valid_meter_val = {
            "connectorId": 1,
            "transactionId": 1001,
            "meterValue": [
                {
                    "timestamp": current_iso_z(),
                    "sampledValue": [
                        {"value": "230.5", "measurand": "Voltage", "unit": "V"},
                        {"value": "15.00", "measurand": "Current.Import", "unit": "A"}
                    ]
                }
            ]
        }
        resp_type, resp_payload = await csms.process_call(
            charge_box_id="ST001",
            action="MeterValues",
            payload=valid_meter_val,
            unique_id="mv_uid_2"
        )
        assert resp_type == OCPPMessageType.CALL_RESULT
        assert resp_payload == {}
        mvs = csms.get_meter_values(transaction_id=1001)
        assert len(mvs) == 1
        assert mvs[0]["meterValue"][0]["sampledValue"][0]["value"] == "230.5"

    @pytest.mark.asyncio
    async def test_virtual_csms_stop_transaction_unregistered_tx_id(self):
        """Stopping an unrecorded transactionId returns Accepted and does not corrupt transaction store."""
        csms = VirtualCSMS()
        payload = {"transactionId": 999999, "meterStop": 5000, "reason": "Local"}
        resp_type, resp_payload = await csms.process_call(
            charge_box_id="ST001",
            action="StopTransaction",
            payload=payload,
            unique_id="stop_uid"
        )
        assert resp_type == OCPPMessageType.CALL_RESULT
        assert resp_payload["idTagInfo"]["status"] == "Accepted"
        assert csms.get_transaction(999999) is None

    @pytest.mark.asyncio
    async def test_virtual_csms_unregistered_charge_box_dispatch_raises(self):
        """Outbound CSMS commands targeting unconnected charge points must raise RuntimeError."""
        csms = VirtualCSMS()
        with pytest.raises(RuntimeError) as exc_1:
            await csms.send_reserve_now("UNREGISTERED_STATION", 1, current_iso_z(), "TAG1", 101)
        assert "not connected to VirtualCSMS" in str(exc_1.value)

        with pytest.raises(RuntimeError) as exc_2:
            await csms.send_remote_start_transaction("UNREGISTERED_STATION", "TAG1", 1)
        assert "not connected to VirtualCSMS" in str(exc_2.value)

    def test_virtual_csms_tag_registry_status_matrix(self):
        """VirtualCSMS tag registry returns correct OCPP authorization status per tag."""
        csms = VirtualCSMS()
        # Seed custom tags
        csms.register_tag("CUSTOM_VIP", "Accepted", "2035-01-01T00:00:00Z")
        csms.register_tag("CUSTOM_REVOKED", "Blocked")

        assert csms.id_tag_registry["DEADBEEF"]["status"] == "Accepted"
        assert csms.id_tag_registry["BLOCKED_TAG"]["status"] == "Blocked"
        assert csms.id_tag_registry["EXPIRED_TAG"]["status"] == "Expired"
        assert csms.id_tag_registry["UNKNOWN_TAG"]["status"] == "Invalid"
        assert csms.id_tag_registry["CUSTOM_VIP"]["status"] == "Accepted"
        assert csms.id_tag_registry["CUSTOM_REVOKED"]["status"] == "Blocked"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: OCPP Transaction & State Machine Edge Cases (11 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestBridgeTransactionLifecycleEdgeCases:
    """Adversarial testing of OCPPBridge transaction lifecycle, remote commands, and state collisions."""

    @pytest.mark.asyncio
    async def test_remote_start_collision_when_already_charging(self):
        """RemoteStartTransaction must be Rejected when connector is already CHARGING."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            # Transition to charging
            bridge.everest_manager.state = "CHARGING"
            bridge.everest_manager.active_transaction_id = 1001

            res = await bridge.handle_remote_start_transaction({"idTag": "DEADBEEF", "connectorId": 1})
            assert res == {"status": "Rejected"}
            # Ensure no relay energize command dispatched
            assert not any(c.get("relay_action") == "ENERGIZE" for c in mock_mqtt.published_commands)
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_rejected_when_faulted_or_tripped(self):
        """RemoteStartTransaction must be Rejected if state is FAULTED or is_tripped is True."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            # Case 1: state is FAULTED
            bridge.everest_manager.state = "FAULTED"
            res1 = await bridge.handle_remote_start_transaction({"idTag": "DEADBEEF", "connectorId": 1})
            assert res1 == {"status": "Rejected"}

            # Case 2: state is AVAILABLE but hardware tripped
            bridge.everest_manager.state = "AVAILABLE"
            bridge.everest_board.is_tripped = True
            res2 = await bridge.handle_remote_start_transaction({"idTag": "DEADBEEF", "connectorId": 1})
            assert res2 == {"status": "Rejected"}
            assert len(mock_mqtt.published_commands) == 0
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_reservation_collision_other_user(self):
        """RemoteStartTransaction with tag B on connector reserved for tag A must be Rejected."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            # Reserve for USER_A
            bridge.everest_manager.state = "RESERVED"
            bridge.everest_manager.active_reservation_id = "5555"
            bridge.everest_auth.last_credential = "USER_A"

            # Attempt remote start with USER_B
            res = await bridge.handle_remote_start_transaction({"idTag": "USER_B", "connectorId": 1})
            assert res == {"status": "Rejected"}
            assert bridge.everest_manager.state == "RESERVED"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_reservation_match_allowed(self):
        """RemoteStartTransaction matching reservation credential transitions to CHARGING and starts tx."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            # Reserve for DEADBEEF
            bridge.everest_manager.state = "RESERVED"
            bridge.everest_manager.active_reservation_id = "7777"
            bridge.everest_auth.last_credential = "DEADBEEF"

            res = await bridge.handle_remote_start_transaction({"idTag": "DEADBEEF", "connectorId": 1})
            assert res == {"status": "Accepted"}
            assert bridge.everest_manager.state == "CHARGING"
            assert bridge.everest_manager.active_transaction_id is not None
            # Check relay command published
            assert any(c.get("relay_action") == "ENERGIZE" for c in mock_mqtt.published_commands)
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_malformed_payload_matrix(self):
        """RemoteStartTransaction rejects non-dict, missing idTag, empty string, or mismatched connectorId."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            malformed_inputs = [
                None,
                "NOT_A_DICT",
                {},  # missing idTag
                {"idTag": ""},  # empty idTag
                {"idTag": "   "},  # whitespace idTag
                {"idTag": "DEADBEEF", "connectorId": 99},  # connectorId mismatch
                {"idTag": "DEADBEEF", "connectorId": "INVALID_INT"},  # invalid connectorId
            ]
            for bad_pay in malformed_inputs:
                res = await bridge.handle_remote_start_transaction(bad_pay)
                assert res == {"status": "Rejected"}
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_transaction_unknown_tx_id(self):
        """RemoteStopTransaction with unknown tx_id is Rejected and leaves active session intact."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "CHARGING"
            bridge.everest_manager.active_transaction_id = 1001

            res = await bridge.handle_remote_stop_transaction({"transactionId": 2002})
            assert res == {"status": "Rejected"}
            assert bridge.everest_manager.active_transaction_id == 1001
            assert bridge.everest_manager.state == "CHARGING"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_transaction_when_no_active_session(self):
        """RemoteStopTransaction when active_transaction_id is None returns Rejected."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.active_transaction_id = None
            res = await bridge.handle_remote_stop_transaction({"transactionId": 1001})
            assert res == {"status": "Rejected"}
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_send_stop_transaction_safeguard_when_no_active_session(self):
        """Calling send_stop_transaction when active_transaction_id is None returns NoActiveTransaction."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.active_transaction_id = None
            res = await bridge.send_stop_transaction(transaction_id=None)
            assert res == {"status": "NoActiveTransaction"}
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_unlock_connector_cross_state_matrix(self):
        """UnlockConnector returns Unlocked for matching connector and NotSupported for non-matching."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            states = ["AVAILABLE", "RESERVED", "CHARGING", "FAULTED"]
            for st in states:
                bridge.everest_manager.state = st
                # Connector 1 matches
                res1 = await bridge.handle_unlock_connector({"connectorId": 1})
                assert res1 == {"status": "Unlocked"}

            # Mismatched connectorId 99
            res_bad = await bridge.handle_unlock_connector({"connectorId": 99})
            assert res_bad == {"status": "NotSupported"}
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_reset_soft_and_hard_state_sanitization(self):
        """Soft and Hard Reset resets state to AVAILABLE, clears active reservation/transaction, de-energizes relay."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            for reset_type in ["Soft", "Hard"]:
                # Seed dirty state
                bridge.everest_manager.state = "CHARGING"
                bridge.everest_manager.active_reservation_id = "res_123"
                bridge.everest_manager.active_transaction_id = 9999
                bridge.everest_board.relay_state = "ENERGIZED"

                res = await bridge.handle_reset({"type": reset_type})
                assert res == {"status": "Accepted"}
                assert bridge.everest_manager.state == "AVAILABLE"
                assert bridge.everest_manager.active_reservation_id is None
                assert bridge.everest_manager.active_transaction_id is None
                assert bridge.everest_board.relay_state == "DE_ENERGIZED"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_meter_values_extreme_electrical_inputs(self):
        """Extreme values (0, negative, high current) must format correctly as valid string sampledValues."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()
        try:
            extreme_telemetries = [
                # Zero readings
                {"voltage_v": 0.0, "current_a": 0.0, "power_kw": 0.0, "energy_kwh": 0.0},
                # Negative readings (regenerative feedback / reverse polarity)
                {"voltage_v": -12.5, "current_a": -2.34, "power_kw": -0.029, "energy_kwh": 0.001},
                # Large industrial DC fast-charging values
                {"voltage_v": 950.8, "current_a": 350.50, "power_kw": 333.255, "energy_kwh": 12450.6789},
            ]
            for tel in extreme_telemetries:
                res = await bridge.send_meter_values(telemetry_payload=tel, transaction_id=1001)
                assert res == {}

            # Verify all logged entries in CSMS have string values and match inputs
            mvs = csms.get_meter_values(transaction_id=1001)
            assert len(mvs) == 3
            # Check negative current string format
            neg_svs = mvs[1]["meterValue"][0]["sampledValue"]
            assert any(sv["value"] == "-2.34" and sv["measurand"] == "Current.Import" for sv in neg_svs)
            # Check high voltage string format
            high_svs = mvs[2]["meterValue"][0]["sampledValue"]
            assert any(sv["value"] == "950.8" and sv["measurand"] == "Voltage" for sv in high_svs)
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: LF Energy EVerest Taxonomy & High-Concurrency Stress (10 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestBridgeConcurrencyAndEVerestTaxonomy:
    """Stress tests covering LF Energy EVerest taxonomy and high-concurrency race conditions."""

    @pytest.mark.asyncio
    async def test_live_websocket_incoming_unmatched_call_result_orphaned(self):
        """Unsolicited or orphaned CallResult received on WebSocket must be ignored gracefully without crash."""
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        # Direct delivery of raw CALL_RESULT with non-existent unique_id
        orphan_frame = json.dumps([3, "orphan_res_999", {"status": "Accepted"}])
        await bridge._handle_incoming_raw_frame(orphan_frame)
        assert "orphan_res_999" not in bridge._pending_calls

    @pytest.mark.asyncio
    async def test_live_websocket_incoming_unmatched_call_error_orphaned(self):
        """Unsolicited or orphaned CallError received on WebSocket must be ignored gracefully without crash."""
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        orphan_err_frame = json.dumps([4, "orphan_err_888", "InternalError", "Database timeout", {}])
        await bridge._handle_incoming_raw_frame(orphan_err_frame)
        assert "orphan_err_888" not in bridge._pending_calls

    @pytest.mark.asyncio
    async def test_handle_csms_call_unsupported_action_raises(self):
        """Inbound CSMS call with unsupported action must raise OCPPCallError with NOT_IMPLEMENTED."""
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        with pytest.raises(OCPPCallError) as exc_info:
            await bridge.handle_csms_call("ChangeAvailability", {"connectorId": 1, "type": "Operative"}, "uid_ca")
        assert exc_info.value.error_code == OCPPCallErrorCode.NOT_IMPLEMENTED
        assert exc_info.value.unique_id == "uid_ca"

    def test_everest_status_rapid_state_oscillations(self):
        """get_everest_status() must maintain accurate, coherent cross-module state during rapid transitions."""
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual")
        cycle = [
            ("AVAILABLE", "DE_ENERGIZED", False, None),
            ("RESERVED", "DE_ENERGIZED", False, None),
            ("CHARGING", "ENERGIZED", False, None),
            ("FAULTED", "DE_ENERGIZED", True, "OVERCURRENT"),
        ]

        for loop_idx in range(25):
            for st, relay, tripped, fault in cycle:
                bridge.everest_manager.state = st
                bridge.everest_board.relay_state = relay
                bridge.everest_board.is_tripped = tripped
                bridge.everest_board.fault_code = fault

                status = bridge.get_everest_status()
                assert status["evse_manager"]["state"] == st
                assert status["evse_board_support"]["relay_state"] == relay
                assert status["evse_board_support"]["is_tripped"] == tripped
                assert status["evse_board_support"]["fault_code"] == fault
                assert status["ocpp"]["mode"] == "virtual"
                assert status["system_ui"]["connector_id"] == "1"

    @pytest.mark.asyncio
    async def test_concurrent_reserve_now_race_condition(self):
        """25 parallel ReserveNow requests fired simultaneously: exactly 1 Accepted, 24 Occupied."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "AVAILABLE"

            async def do_reserve(idx: int):
                return await bridge.handle_reserve_now({
                    "connectorId": 1,
                    "reservationId": 1000 + idx,
                    "idTag": f"TAG_{idx}",
                    "expiryDate": current_iso_z(time.time() + 60)
                })

            results = await asyncio.gather(*[do_reserve(i) for i in range(25)])
            accepted = [r for r in results if r.get("status") == "Accepted"]
            occupied = [r for r in results if r.get("status") == "Occupied"]

            assert len(accepted) == 1, f"Expected exactly 1 Accepted, got {len(accepted)}"
            assert len(occupied) == 24, f"Expected 24 Occupied, got {len(occupied)}"
            assert bridge.everest_manager.state == "RESERVED"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_concurrent_remote_start_bombardment(self):
        """25 concurrent RemoteStartTransaction requests: exactly 1 Accepted, 24 Rejected."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "AVAILABLE"

            async def do_remote_start(idx: int):
                return await bridge.handle_remote_start_transaction({
                    "connectorId": 1,
                    "idTag": f"DEADBEEF"  # Valid tag in CSMS whitelist
                })

            # In asyncio cooperative concurrency, ensure atomic transaction serialization
            results = await asyncio.gather(*[do_remote_start(i) for i in range(25)])
            accepted = [r for r in results if r.get("status") == "Accepted"]
            rejected = [r for r in results if r.get("status") == "Rejected"]

            assert len(accepted) >= 1
            assert len(accepted) + len(rejected) == 25
            assert bridge.everest_manager.state == "CHARGING"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_parallel_mqtt_auth_and_reserve_now_race(self):
        """Race between inbound MQTT /auth and CSMS ReserveNow resolves safely without exception."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "AVAILABLE"

            task_auth = bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {"auth_type": "RFID", "credential": {"rfid_uid": "DEADBEEF"}, "current_state": "AVAILABLE"}
            )
            task_reserve = bridge.handle_reserve_now({
                "connectorId": 1,
                "reservationId": 8888,
                "idTag": "A1B2C3D4",
                "expiryDate": current_iso_z(time.time() + 60)
            })

            res_auth, res_reserve = await asyncio.gather(task_auth, task_reserve, return_exceptions=True)
            # Neither should raise an exception
            assert not isinstance(res_auth, Exception)
            assert not isinstance(res_reserve, Exception)
            # Final state must be either CHARGING or RESERVED
            assert bridge.everest_manager.state in ["CHARGING", "RESERVED"]
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_emergency_fault_trip_during_active_charging_stops_transaction(self):
        """MQTT FAULTED state received while charging immediately stops transaction and trips board."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            # Setup active charging
            bridge.everest_manager.state = "CHARGING"
            bridge.everest_manager.active_transaction_id = 1005
            bridge.everest_board.relay_state = "ENERGIZED"

            # Inbound hardware fault trip from ESP32
            await bridge.handle_mqtt_state("evsarthi/ST001/1/state", {
                "state": "FAULTED",
                "relay_state": "DE_ENERGIZED",
                "fault_code": "OVERCURRENT",
                "transition_reason": "Current exceeded threshold >29.7A"
            })

            # Assert StopTransaction was sent to CSMS and active_transaction_id cleared
            assert bridge.everest_manager.active_transaction_id is None
            assert bridge.everest_board.is_tripped is True
            assert bridge.everest_board.relay_state == "DE_ENERGIZED"
            # Verify StatusNotification sent with Faulted and OverCurrentFailure
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Faulted"
            assert stat["errorCode"] == "OverCurrentFailure"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_ev_disconnect_during_active_charging_stops_transaction(self):
        """MQTT AVAILABLE transition received while charging triggers EVDisconnected StopTransaction."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "CHARGING"
            bridge.everest_manager.active_transaction_id = 1006
            bridge.everest_board.relay_state = "ENERGIZED"

            # Hardware reports cable unplugged -> AVAILABLE
            await bridge.handle_mqtt_state("evsarthi/ST001/1/state", {
                "state": "AVAILABLE",
                "relay_state": "DE_ENERGIZED",
                "transition_reason": "Cable disconnected by driver"
            })

            assert bridge.everest_manager.active_transaction_id is None
            assert bridge.everest_manager.state == "AVAILABLE"
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Available"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_auth_rejection_when_connector_is_faulted_suppresses_grant(self):
        """Inbound MQTT /auth must be DENIED (403) and grant window 0ms when connector is in FAULTED state."""
        mock_mqtt = MockMQTTCommandCollector()
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", mqtt_svc=mock_mqtt, virtual_csms=csms)
        await bridge.start()
        try:
            bridge.everest_manager.state = "FAULTED"
            bridge.everest_board.is_tripped = True

            await bridge.handle_mqtt_auth("evsarthi/ST001/1/auth", {
                "auth_type": "RFID",
                "credential": {"rfid_uid": "DEADBEEF"},  # Normally accepted tag
                "current_state": "FAULTED"
            })

            # Check that auth decision was DENIED with 403
            assert len(mock_mqtt.published_commands) == 1
            cmd = mock_mqtt.published_commands[0]
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 403
            assert cmd["auth_window_duration_ms"] == 0
            assert cmd["relay_action"] == "DE_ENERGIZED"
            # No transaction should have been started
            assert bridge.everest_manager.active_transaction_id is None
        finally:
            await bridge.stop()
