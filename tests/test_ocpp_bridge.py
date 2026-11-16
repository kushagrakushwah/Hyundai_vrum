"""
tests/test_ocpp_bridge.py
=========================
Comprehensive automated verification suite for EV Sarthi SteVe OCPP 1.6-J CSMS
and LF Energy EVerest Bridge (smartreserve_backend/ocpp_bridge.py).

Covers:
1. OCPP 1.6-J JSON-RPC 2.0 framing, serialization, deserialization, Call, CallResult, CallError.
2. BootNotification & Heartbeat lifecycle with VirtualCSMS.
3. StatusNotification translations across 4 FSM states & error codes.
4. ReserveNow & CancelReservation flows connecting to MQTT and DB.
5. Authorize flow (Keypad PIN, RFID reader, Accepted vs Denied/Blocked/Expired).
6. StartTransaction & StopTransaction flows with integer transactionId tracking.
7. MeterValues translation with string sampled values and standard measurands.
8. Remote management commands (RemoteStartTransaction, RemoteStopTransaction, UnlockConnector, Reset).
9. LF Energy EVerest module taxonomy mapping (get_everest_status).
10. Edge cases (connector occupied, malformed frames, unknown reservation IDs, emergency fault trip).
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
)
import database


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: OCPP 1.6-J JSON Framing & Validation Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestOCPPFraming:
    def test_serialize_call(self):
        frame = serialize_call("msg_001", "BootNotification", {"chargePointVendor": "EV_Sarthi"})
        parsed = json.loads(frame)
        assert isinstance(parsed, list)
        assert len(parsed) == 4
        assert parsed[0] == 2
        assert parsed[1] == "msg_001"
        assert parsed[2] == "BootNotification"
        assert parsed[3] == {"chargePointVendor": "EV_Sarthi"}

    def test_serialize_call_result(self):
        frame = serialize_call_result("msg_002", {"status": "Accepted"})
        parsed = json.loads(frame)
        assert isinstance(parsed, list)
        assert len(parsed) == 3
        assert parsed[0] == 3
        assert parsed[1] == "msg_002"
        assert parsed[2] == {"status": "Accepted"}

    def test_serialize_call_error(self):
        frame = serialize_call_error("msg_003", "FormationViolation", "Payload malformed", {"key": "val"})
        parsed = json.loads(frame)
        assert isinstance(parsed, list)
        assert len(parsed) == 5
        assert parsed[0] == 4
        assert parsed[1] == "msg_003"
        assert parsed[2] == "FormationViolation"
        assert parsed[3] == "Payload malformed"
        assert parsed[4] == {"key": "val"}

    def test_parse_valid_frames(self):
        # Call frame
        call_raw = json.dumps([2, "call_1", "Heartbeat", {}])
        msg_type, uid, action, payload, _ = parse_frame(call_raw)
        assert msg_type == OCPPMessageType.CALL
        assert uid == "call_1"
        assert action == "Heartbeat"
        assert payload == {}

        # CallResult frame
        res_raw = json.dumps([3, "res_1", {"currentTime": "2026-11-15T18:00:00Z"}])
        msg_type, uid, res_payload, _, _ = parse_frame(res_raw)
        assert msg_type == OCPPMessageType.CALL_RESULT
        assert uid == "res_1"
        assert res_payload == {"currentTime": "2026-11-15T18:00:00Z"}

        # CallError frame
        err_raw = json.dumps([4, "err_1", "NotImplemented", "Action unsupported", {}])
        msg_type, uid, err_code, err_desc, err_details = parse_frame(err_raw)
        assert msg_type == OCPPMessageType.CALL_ERROR
        assert uid == "err_1"
        assert err_code == "NotImplemented"
        assert err_desc == "Action unsupported"
        assert err_details == {}

    def test_parse_malformed_json_raises_formation_violation(self):
        with pytest.raises(OCPPCallError) as exc_info:
            parse_frame("NOT_JSON")
        assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_parse_invalid_frame_structure(self):
        # Non-array
        with pytest.raises(OCPPCallError) as exc:
            parse_frame(json.dumps({"msg": 1}))
        assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        # Too short (<3 items)
        with pytest.raises(OCPPCallError) as exc:
            parse_frame(json.dumps([2, "uid"]))
        assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

        # Invalid MessageTypeId (e.g. 5)
        with pytest.raises(OCPPCallError) as exc:
            parse_frame(json.dumps([5, "uid", "payload"]))
        assert exc.value.error_code == OCPPCallErrorCode.PROTOCOL_ERROR

        # Non-dict Call payload
        with pytest.raises(OCPPCallError) as exc:
            parse_frame(json.dumps([2, "uid", "Action", "string_payload"]))
        assert exc.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION

    def test_current_iso_z_format(self):
        ts_str = current_iso_z()
        assert ts_str.endswith("Z")
        assert "T" in ts_str
        assert len(ts_str) == 20  # YYYY-MM-DDTHH:MM:SSZ


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: BootNotification & Heartbeat Lifecycle Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestBootAndHeartbeat:
    @pytest.mark.asyncio
    async def test_boot_notification_lifecycle(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            charge_box_id="EV_SARTHI_ST001",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            assert bridge.is_connected() is True
            assert bridge._boot_accepted is True
            assert bridge._heartbeat_interval == 300

            # Verify registered charge box in CSMS
            assert "EV_SARTHI_ST001" in csms.registered_charge_boxes
            box_info = csms.registered_charge_boxes["EV_SARTHI_ST001"]
            assert box_info["vendor"] == "EV_Sarthi"
            assert box_info["model"] == "ESP32-DevKitC-v4"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_exchange(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            hb_resp = await bridge.send_heartbeat()
            assert "currentTime" in hb_resp
            assert hb_resp["currentTime"].endswith("Z")

            calls = csms.get_call_log(action="Heartbeat")
            assert len(calls) >= 1
            assert calls[-1]["action"] == "Heartbeat"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: StatusNotification & 4-State Machine Mapping Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusNotification:
    @pytest.mark.asyncio
    async def test_all_fsm_state_mappings(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. AVAILABLE -> Available, NoError
            res = await bridge.send_status_notification(status="Available", error_code="NoError")
            assert res == {}
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Available"
            assert stat["errorCode"] == "NoError"

            # 2. RESERVED -> Reserved
            await bridge.send_status_notification(status="Reserved", error_code="NoError")
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Reserved"

            # 3. CHARGING -> Charging
            await bridge.send_status_notification(status="Charging", error_code="NoError")
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Charging"

            # 4. FAULTED with OverCurrentFailure
            await bridge.send_status_notification(
                status="Faulted",
                error_code="OverCurrentFailure",
                info="Current exceeded 29.7A"
            )
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Faulted"
            assert stat["errorCode"] == "OverCurrentFailure"
            assert "29.7A" in stat["info"]

            # 5. FAULTED with OverVoltage
            await bridge.send_status_notification(
                status="Faulted",
                error_code="OverVoltage",
                info="Voltage exceeded 227V"
            )
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Faulted"
            assert stat["errorCode"] == "OverVoltage"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_mqtt_state_listener_dispatches_status_notification(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Publish state via virtual broker
            await mock_broker.publish(
                "evsarthi/ST001/1/state",
                {
                    "state": "CHARGING",
                    "relay_state": "ENERGIZED",
                    "transition_reason": "Cable detected and relay closed",
                    "uptime_millis": 12000
                }
            )
            await asyncio.sleep(0.05)

            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat is not None
            assert stat["status"] == "Charging"
            assert stat["errorCode"] == "NoError"
            assert bridge.everest_manager.state == "CHARGING"
            assert bridge.everest_board.relay_state == "ENERGIZED"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: ReserveNow & CancelReservation Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestReservations:
    @pytest.mark.asyncio
    async def test_reserve_now_success(self, mock_broker, test_db):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            database=database,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Trigger CSMS ReserveNow
            res = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T18:15:00Z",
                id_tag="DEADBEEF",
                reservation_id=501
            )
            assert res.get("status") == "Accepted"
            assert bridge.everest_manager.state == "RESERVED"
            assert bridge.everest_manager.active_reservation_id == "501"

            # Verify MQTT /reserve message published
            reserve_msgs = mock_broker.get_messages("evsarthi/ST001/1/reserve")
            assert len(reserve_msgs) >= 1
            payload = reserve_msgs[-1].payload
            assert payload["action"] == "CREATE"
            assert payload["reservation_id"] == "501"
            assert payload["rfid_uid"] == "DEADBEEF"

            # Verify SQLite DB reservation entry
            db_res = await database.get_reservation("ST001", "1")
            assert db_res is not None
            assert db_res["reservation_id"] == "501"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_reserve_now_occupied_and_faulted_rejections(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Reject when CHARGING
            bridge.everest_manager.state = "CHARGING"
            res_occ = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T18:15:00Z",
                id_tag="DEADBEEF",
                reservation_id=502
            )
            assert res_occ.get("status") == "Occupied"

            # 2. Reject when FAULTED
            bridge.everest_manager.state = "FAULTED"
            res_flt = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T18:15:00Z",
                id_tag="DEADBEEF",
                reservation_id=503
            )
            assert res_flt.get("status") == "Faulted"

            # 3. Reject invalid connector ID
            bridge.everest_manager.state = "AVAILABLE"
            res_inv = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=99,
                expiry_date="2026-11-15T18:15:00Z",
                id_tag="DEADBEEF",
                reservation_id=504
            )
            assert res_inv.get("status") == "Rejected"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cancel_reservation_lifecycle(self, mock_broker, test_db):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            database=database,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # First create reservation
            await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T18:15:00Z",
                id_tag="DEADBEEF",
                reservation_id=601
            )
            assert bridge.everest_manager.state == "RESERVED"

            # Cancel reservation
            cancel_res = await csms.send_cancel_reservation(bridge.charge_box_id, 601)
            assert cancel_res.get("status") == "Accepted"
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_reservation_id is None

            # Verify MQTT /reserve CANCEL published
            reserve_msgs = mock_broker.get_messages("evsarthi/ST001/1/reserve")
            assert reserve_msgs[-1].payload["action"] == "CANCEL"

            # Cancel unknown reservation should reject
            cancel_bad = await csms.send_cancel_reservation(bridge.charge_box_id, 9999)
            assert cancel_bad.get("status") == "Rejected"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Authorize & Access Control Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthorize:
    @pytest.mark.asyncio
    async def test_authorize_rfid_accepted(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Publish RFID auth on MQTT
            await mock_broker.publish(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "DEADBEEF"},
                    "current_state": "AVAILABLE"
                }
            )
            await asyncio.sleep(0.05)

            # Check that command was published with ACCEPTED and ENERGIZE
            cmd_msgs = mock_broker.get_messages("evsarthi/ST001/1/cmd")
            assert len(cmd_msgs) >= 1
            cmd = cmd_msgs[-1].payload
            assert cmd["status"] == "ACCEPTED"
            assert cmd["status_code"] == 200
            assert cmd["relay_action"] == "ENERGIZE"
            assert cmd["auth_window_duration_ms"] in (8000, 30000)

            # Verify transaction was initiated
            assert bridge.everest_manager.active_transaction_id is not None
            assert bridge.everest_manager.state == "CHARGING"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_authorize_denied_cases(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Blocked Tag
            await mock_broker.publish(
                "evsarthi/ST001/1/auth",
                {"auth_type": "RFID", "credential": {"rfid_uid": "BLOCKED_TAG"}}
            )
            await asyncio.sleep(0.05)
            cmd = mock_broker.get_messages("evsarthi/ST001/1/cmd")[-1].payload
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 401
            assert cmd["relay_action"] == "DE_ENERGIZED"

            # 2. Expired Tag
            await mock_broker.publish(
                "evsarthi/ST001/1/auth",
                {"auth_type": "RFID", "credential": {"rfid_uid": "EXPIRED_TAG"}}
            )
            await asyncio.sleep(0.05)
            cmd = mock_broker.get_messages("evsarthi/ST001/1/cmd")[-1].payload
            assert cmd["status"] == "DENIED"

            # 3. Unknown Tag
            await mock_broker.publish(
                "evsarthi/ST001/1/auth",
                {"auth_type": "RFID", "credential": {"rfid_uid": "NON_EXISTENT_TAG"}}
            )
            await asyncio.sleep(0.05)
            cmd = mock_broker.get_messages("evsarthi/ST001/1/cmd")[-1].payload
            assert cmd["status"] == "DENIED"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: Transactions & MeterValues Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTransactionsAndMeterValues:
    @pytest.mark.asyncio
    async def test_full_charging_session_lifecycle(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Start Transaction
            start_res = await bridge.send_start_transaction(id_tag="DEADBEEF", meter_start=0)
            tx_id = start_res["transactionId"]
            assert isinstance(tx_id, int)
            assert tx_id >= 1001
            assert bridge.everest_manager.active_transaction_id == tx_id
            assert bridge.everest_manager.state == "CHARGING"

            # 2. Stream Inbound Telemetry on MQTT
            telemetry_payload = {
                "voltage_v": 230.2,
                "current_a": 15.8,
                "power_kw": 3.637,
                "energy_kwh": 0.852,
                "millis": 45000
            }
            await mock_broker.publish("evsarthi/ST001/1/telemetry", telemetry_payload)
            await asyncio.sleep(0.05)

            # Verify MeterValues recorded in CSMS
            mv_logs = csms.get_meter_values(transaction_id=tx_id)
            assert len(mv_logs) >= 1
            sample_list = mv_logs[-1]["meterValue"][0]["sampledValue"]
            assert len(sample_list) == 4

            measurands = {s["measurand"]: s["value"] for s in sample_list}
            assert "Voltage" in measurands
            assert "Current.Import" in measurands
            assert "Power.Active.Import" in measurands
            assert "Energy.Active.Import.Register" in measurands

            # Critical OCPP spec check: all values must be strings
            for s in sample_list:
                assert isinstance(s["value"], str)

            assert measurands["Voltage"] == "230.2"
            assert measurands["Current.Import"] == "15.80"

            # 3. Stop Transaction
            stop_res = await bridge.send_stop_transaction(
                transaction_id=tx_id,
                meter_stop=1200,
                reason="Local"
            )
            assert stop_res.get("idTagInfo", {}).get("status") == "Accepted"
            assert bridge.everest_manager.active_transaction_id is None
            assert bridge.everest_manager.state == "AVAILABLE"

            tx_record = csms.get_transaction(tx_id)
            assert tx_record["status"] == "Stopped"
            assert tx_record["meterStop"] == 1200
            assert tx_record["reason"] == "Local"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_meter_values_rejects_numeric_values(self):
        """Validates that VirtualCSMS rejects non-string sampledValue values."""
        csms = VirtualCSMS()
        bad_payload = {
            "connectorId": 1,
            "meterValue": [
                {
                    "timestamp": current_iso_z(),
                    "sampledValue": [
                        {"value": 220.0, "measurand": "Voltage"}  # Non-string float
                    ]
                }
            ]
        }
        res_type, res_payload = await csms.process_call("EV_001", "MeterValues", bad_payload, "uid_test")
        assert res_type == OCPPMessageType.CALL_ERROR
        assert res_payload["errorCode"] == OCPPCallErrorCode.FORMATION_VIOLATION.value

    @pytest.mark.asyncio
    async def test_emergency_trip_stops_transaction(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Start active transaction
            await bridge.send_start_transaction(id_tag="DEADBEEF")
            tx_id = bridge.everest_manager.active_transaction_id
            assert tx_id is not None

            # Simulate hardware emergency overcurrent trip (>29.7A)
            await mock_broker.publish(
                "evsarthi/ST001/1/state",
                {
                    "state": "FAULTED",
                    "relay_state": "DE_ENERGIZED",
                    "fault_code": "OVERCURRENT",
                    "transition_reason": "Overcurrent trip 31.2A"
                }
            )
            await asyncio.sleep(0.05)

            # Transaction should be terminated with reason EmergencyStop
            tx_rec = csms.get_transaction(tx_id)
            assert tx_rec["status"] == "Stopped"
            assert tx_rec["reason"] == "EmergencyStop"

            # Connector status in CSMS should be Faulted
            stat = csms.get_connector_status(bridge.charge_box_id, 1)
            assert stat["status"] == "Faulted"
            assert stat["errorCode"] == "OverCurrentFailure"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: Remote CSMS Commands Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRemoteCommands:
    @pytest.mark.asyncio
    async def test_remote_start_and_stop_transaction(self, mock_broker):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Remote Start
            res_start = await csms.send_remote_start_transaction(bridge.charge_box_id, "DEADBEEF", connector_id=1)
            assert res_start["status"] == "Accepted"
            assert bridge.everest_manager.state == "CHARGING"
            tx_id = bridge.everest_manager.active_transaction_id
            assert tx_id is not None

            # Verify MQTT command energized relay
            cmd = mock_broker.get_messages("evsarthi/ST001/1/cmd")[-1].payload
            assert cmd["relay_action"] == "ENERGIZE"

            # 2. Remote Stop
            res_stop = await csms.send_remote_stop_transaction(bridge.charge_box_id, tx_id)
            assert res_stop["status"] == "Accepted"
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_transaction_id is None

            # Verify MQTT command de-energized relay
            cmd_stop = mock_broker.get_messages("evsarthi/ST001/1/cmd")[-1].payload
            assert cmd_stop["relay_action"] == "DE_ENERGIZE"

            # 3. Remote Stop with invalid transactionId
            res_stop_bad = await csms.send_remote_stop_transaction(bridge.charge_box_id, 99999)
            assert res_stop_bad["status"] == "Rejected"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_unlock_connector(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            res_unlock = await csms.send_unlock_connector(bridge.charge_box_id, connector_id=1)
            assert res_unlock["status"] == "Unlocked"

            res_unlock_bad = await csms.send_unlock_connector(bridge.charge_box_id, connector_id=99)
            assert res_unlock_bad["status"] == "NotSupported"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_reset_command(self):
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            bridge.everest_manager.state = "CHARGING"
            bridge.everest_board.relay_state = "ENERGIZED"

            res_reset = await csms.send_reset(bridge.charge_box_id, reset_type="Soft")
            assert res_reset["status"] == "Accepted"
            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_board.relay_state == "DE_ENERGIZED"
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 8: LF Energy EVerest Module Taxonomy Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEVerestTaxonomy:
    @pytest.mark.asyncio
    async def test_get_everest_status(self):
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            charge_box_id="EV_SARTHI_ST001",
            mode="virtual"
        )
        await bridge.start()
        try:
            status = bridge.get_everest_status()
            assert "evse_board_support" in status
            assert "auth" in status
            assert "evse_manager" in status
            assert "system_ui" in status
            assert "ocpp" in status

            # Check individual module keys
            bs = status["evse_board_support"]
            assert "relay_state" in bs
            assert "voltage_v" in bs
            assert "current_a" in bs

            auth = status["auth"]
            assert "auth_window_active" in auth

            mgr = status["evse_manager"]
            assert mgr["state"] == "AVAILABLE"

            ocpp = status["ocpp"]
            assert ocpp["charge_box_id"] == "EV_SARTHI_ST001"
            assert ocpp["connected"] is True
            assert ocpp["boot_accepted"] is True
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_ui_telemetry_and_sd_counter(self, mock_broker):
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker
        )
        await bridge.start()
        try:
            init_sd = bridge.everest_ui.sd_log_count

            await mock_broker.publish(
                "evsarthi/ST001/1/telemetry",
                {"voltage_v": 225.0, "current_a": 10.0, "power_kw": 2.25, "energy_kwh": 0.5}
            )
            await asyncio.sleep(0.05)

            assert bridge.everest_ui.sd_log_count == init_sd + 1
            assert bridge.everest_ui.oled_display["v"] == 225.0
            assert bridge.everest_ui.oled_display["a"] == 10.0
        finally:
            await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 9: Backwards Compatibility Adapter Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestBackwardsCompatibility:
    def test_translate_state_to_status_notification(self):
        state_payload = {
            "state": "AVAILABLE",
            "connector_id": 1,
            "transition_reason": "Boot complete",
            "timestamp": 1700000000
        }
        res = OCPPBridge.translate_state_to_status_notification(state_payload, "ST001")
        assert res["connectorId"] == 1
        assert res["status"] == "Available"
        assert res["errorCode"] == "NoError"
        assert res["vendorId"] == "EV_SARTHI"

    def test_translate_telemetry_to_meter_values(self):
        telemetry_payload = {
            "connector_id": 1,
            "voltage_v": 230.0,
            "current_a": 16.0,
            "power_kw": 3.68,
            "energy_kwh": 1.5,
            "timestamp": 1700000000
        }
        res = OCPPBridge.translate_telemetry_to_meter_values(telemetry_payload, "ST001", transaction_id=1001)
        assert res["connectorId"] == 1
        assert res["transactionId"] == 1001
        assert len(res["meterValue"]) == 1
        sampled = res["meterValue"][0]["sampledValue"]
        assert len(sampled) == 4
        assert sampled[0]["value"] == "230.0"
        assert sampled[0]["measurand"] == "Voltage"
        assert sampled[1]["value"] == "16.00"
        assert sampled[1]["measurand"] == "Current.Import"


# ══════════════════════════════════════════════════════════════════════════════
# Section 10: Milestone 3 Safety, State Consistency & Command Remediation Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestMilestone3RemediationSafeguards:
    @pytest.mark.asyncio
    async def test_auth_denied_and_relay_not_energized_in_faulted_state(self, mock_broker):
        """Safety Invariant: Whitelisted RFID or PIN must NEVER energize contactor during FAULTED state."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Trip hardware into FAULTED
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

            # Attempt auth with whitelisted RFID
            await bridge.handle_mqtt_auth(
                "evsarthi/ST001/1/auth",
                {
                    "auth_type": "RFID",
                    "credential": {"rfid_uid": "DEADBEEF"},
                    "current_state": "FAULTED"
                }
            )
            await asyncio.sleep(0.05)

            cmd_msgs = mock_broker.get_messages("evsarthi/ST001/1/cmd")
            assert len(cmd_msgs) >= 1
            cmd = cmd_msgs[-1].payload
            assert cmd["status"] == "DENIED"
            assert cmd["status_code"] == 403
            assert cmd["relay_action"] == "DE_ENERGIZED"
            assert bridge.everest_manager.active_transaction_id is None
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cancel_reservation_rejects_mismatched_id_and_wrong_state(self, mock_broker, test_db):
        """State Invariant: CancelReservation must strictly reject mismatched IDs or non-RESERVED states."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            database=database,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Reject when no reservation active
            res_bad1 = await bridge.handle_cancel_reservation({"reservationId": 123})
            assert res_bad1["status"] == "Rejected"

            # 2. Create reservation 777
            res = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T19:00:00Z",
                id_tag="VALID_USER",
                reservation_id=777
            )
            assert res["status"] == "Accepted"
            assert bridge.everest_manager.active_reservation_id == "777"

            # 3. Reject mismatched reservationId 9999
            res_bad2 = await bridge.handle_cancel_reservation({"reservationId": 9999})
            assert res_bad2["status"] == "Rejected"
            assert bridge.everest_manager.active_reservation_id == "777"
            assert bridge.everest_manager.state == "RESERVED"

            # 4. Reject empty reservationId
            res_bad3 = await bridge.handle_cancel_reservation({})
            assert res_bad3["status"] == "Rejected"

            # 5. Accept matching reservationId 777
            res_ok = await bridge.handle_cancel_reservation({"reservationId": 777})
            assert res_ok["status"] == "Accepted"
            assert bridge.everest_manager.active_reservation_id is None
            assert bridge.everest_manager.state == "AVAILABLE"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_transaction_rejects_missing_or_null_or_mismatched_tx_id(self):
        """Command Invariant: RemoteStopTransaction must reject empty payload, null ID, or mismatched ID."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # When idle (no transaction active)
            assert bridge.everest_manager.active_transaction_id is None
            res1 = await bridge.handle_remote_stop_transaction({})
            assert res1["status"] == "Rejected"

            res2 = await bridge.handle_remote_stop_transaction({"transactionId": None})
            assert res2["status"] == "Rejected"

            res3 = await bridge.handle_remote_stop_transaction({"transactionId": 1001})
            assert res3["status"] == "Rejected"

            # Start active transaction
            start = await bridge.send_start_transaction("DEADBEEF")
            tx_id = start["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id

            # Reject mismatched transaction ID
            res4 = await bridge.handle_remote_stop_transaction({"transactionId": tx_id + 99})
            assert res4["status"] == "Rejected"
            assert bridge.everest_manager.active_transaction_id == tx_id

            # Accept valid transaction ID
            res5 = await bridge.handle_remote_stop_transaction({"transactionId": tx_id})
            assert res5["status"] == "Accepted"
            assert bridge.everest_manager.active_transaction_id is None
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_cable_disconnect_revert_to_available_terminates_active_transaction(self):
        """Session Invariant: Cable disconnect returning to AVAILABLE terminates transaction with reason EVDisconnected."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Start active transaction
            start = await bridge.send_start_transaction("DEADBEEF")
            tx_id = start["transactionId"]
            assert bridge.everest_manager.active_transaction_id == tx_id

            # Simulate EV unplug -> hardware reports AVAILABLE with EV_DISCONNECTED
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "EV_DISCONNECTED"
                }
            )

            # Transaction must be stopped and cleared
            assert bridge.everest_manager.active_transaction_id is None
            tx_record = csms.get_transaction(tx_id)
            assert tx_record is not None
            assert tx_record["status"] == "Stopped"
            assert tx_record["reason"] == "EVDisconnected"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_revert_to_available_cleanses_reservation_state(self):
        """Timer Invariant: Transitioning to AVAILABLE flushes any active reservation ID."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # Active reservation
            res = await csms.send_reserve_now(
                charge_box_id=bridge.charge_box_id,
                connector_id=1,
                expiry_date="2026-11-15T19:00:00Z",
                id_tag="DEADBEEF",
                reservation_id=888
            )
            assert res["status"] == "Accepted"
            assert bridge.everest_manager.active_reservation_id == "888"

            # Hardware reports timeout -> AVAILABLE
            await bridge.handle_mqtt_state(
                "evsarthi/ST001/1/state",
                {
                    "state": "AVAILABLE",
                    "relay_state": "DE_ENERGIZED",
                    "transition_reason": "RESERVATION_TIMEOUT"
                }
            )

            assert bridge.everest_manager.state == "AVAILABLE"
            assert bridge.everest_manager.active_reservation_id is None
            assert bridge.everest_manager.reservation_expires_at is None
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_start_transaction_safeguards_and_contactor_interlock(self, mock_broker):
        """
        RemoteStart Safeguards:
        1. Malformed or non-integer connectorId is rejected without crashing or energizing relay.
        2. Mismatched connectorId is rejected without energizing relay on connector 1.
        3. Missing or empty idTag is rejected without energizing relay.
        4. Request during FAULTED state is rejected without energizing relay.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(
            station_id="ST001",
            connector_id="1",
            mode="virtual",
            mqtt_svc=mock_broker,
            virtual_csms=csms
        )
        await bridge.start()
        try:
            # 1. Malformed connectorId
            res_malformed = await bridge.handle_remote_start_transaction({
                "connectorId": "invalid",
                "idTag": "USER1"
            })
            assert res_malformed["status"] == "Rejected"
            assert len(mock_broker.get_messages("evsarthi/ST001/1/cmd")) == 0

            # 2. Mismatched connectorId (e.g. 99)
            res_mismatched = await bridge.handle_remote_start_transaction({
                "connectorId": 99,
                "idTag": "USER1"
            })
            assert res_mismatched["status"] == "Rejected"
            assert len(mock_broker.get_messages("evsarthi/ST001/1/cmd")) == 0

            # 3. Missing idTag
            res_missing_id = await bridge.handle_remote_start_transaction({
                "connectorId": 1
            })
            assert res_missing_id["status"] == "Rejected"
            assert len(mock_broker.get_messages("evsarthi/ST001/1/cmd")) == 0

            # 4. In FAULTED state
            bridge.everest_manager.state = "FAULTED"
            res_faulted = await bridge.handle_remote_start_transaction({
                "connectorId": 1,
                "idTag": "USER1"
            })
            assert res_faulted["status"] == "Rejected"
            assert len(mock_broker.get_messages("evsarthi/ST001/1/cmd")) == 0
        finally:
            await bridge.stop()
