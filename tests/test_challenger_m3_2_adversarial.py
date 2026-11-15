"""
tests/test_challenger_m3_2_adversarial.py
=========================================
Empirical Adversarial Verification Suite for Milestone 3 (Challenger 2).

Thoroughly stress-tests:
1. Boundary & Extreme Telemetry Values:
   - Negative, zero, and extreme values (500 kW, 1000 V) in MeterValues.
   - OCA OCPP 1.6 spec compliance: strict non-string rejection for sampledValue[].value.
   - 60-second reservation countdown expiration and 8-second auth window expiration.
2. Emergency Hardware Fault Injection:
   - Overcurrent fault injection (>29.7A) during active transaction -> EmergencyStop StopTransaction + OverCurrentFailure StatusNotification.
   - Overvoltage fault injection (>227V) during active transaction -> EmergencyStop StopTransaction + OverVoltage StatusNotification.
   - Fault injection when no active transaction exists.
   - StopTransaction with unknown/invalid transactionId and mismatching idTag.
   - RemoteStopTransaction with mismatched transactionId rejection.
3. Network Disconnection & Exponential Backoff Reconnection Resiliency:
   - Real local WebSocket server lifecycle testing with OCPP 1.6-J subprotocol.
   - Connection drop detection, client backoff retry, and automatic reconnect recovery.
   - In-flight call cleanup on server drop without memory leaks.
   - Fast failure (ConnectionError) when calling on disconnected live bridge.
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
import websockets


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Boundary & Telemetry Value Stress Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestBoundaryAndTelemetryValueStress:
    """Stress tests telemetry boundaries, extremes, type constraints, and timers."""

    @pytest.mark.asyncio
    async def test_meter_values_negative_electrical_readings(self):
        """Verify handling and string formatting of negative telemetry values."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        telemetry_negative = {
            "connector_id": 1,
            "voltage_v": -12.5,
            "current_a": -15.2,
            "power_kw": -0.190,
            "energy_kwh": -2.5,
            "timestamp": time.time()
        }

        # Send MeterValues with negative values
        resp = await bridge.send_meter_values(telemetry_payload=telemetry_negative, transaction_id=1001)
        assert resp == {}

        # Verify board support model updated with floats
        assert bridge.everest_board.voltage_v == -12.5
        assert bridge.everest_board.current_a == -15.2
        assert bridge.everest_board.power_kw == -0.190
        assert bridge.everest_board.energy_kwh == -2.5

        # Verify CSMS logged sampled values as strict formatted strings
        mv_logs = csms.get_meter_values(transaction_id=1001)
        assert len(mv_logs) == 1
        sampled = mv_logs[0]["meterValue"][0]["sampledValue"]
        sampled_dict = {s["measurand"]: s["value"] for s in sampled}

        assert sampled_dict["Voltage"] == "-12.5"
        assert sampled_dict["Current.Import"] == "-15.20"
        assert sampled_dict["Power.Active.Import"] == "-0.190"
        assert sampled_dict["Energy.Active.Import.Register"] == "-2.5000"

        # Verify all values are strictly strings
        for s in sampled:
            assert isinstance(s["value"], str)

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_meter_values_zero_readings(self):
        """Verify handling of exact zero telemetry values."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        telemetry_zero = {
            "connector_id": 1,
            "voltage_v": 0.0,
            "current_a": 0.0,
            "power_kw": 0.0,
            "energy_kwh": 0.0,
            "timestamp": time.time()
        }

        resp = await bridge.send_meter_values(telemetry_payload=telemetry_zero, transaction_id=1002)
        assert resp == {}

        mv_logs = csms.get_meter_values(transaction_id=1002)
        assert len(mv_logs) == 1
        sampled = mv_logs[0]["meterValue"][0]["sampledValue"]
        sampled_dict = {s["measurand"]: s["value"] for s in sampled}

        assert sampled_dict["Voltage"] == "0.0"
        assert sampled_dict["Current.Import"] == "0.00"
        assert sampled_dict["Power.Active.Import"] == "0.000"
        assert sampled_dict["Energy.Active.Import.Register"] == "0.0000"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_meter_values_extreme_high_readings(self):
        """Verify handling of extreme high grid values (1000V, 500A, 500kW DC fast charging)."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        telemetry_extreme = {
            "connector_id": 1,
            "voltage_v": 1000.0,
            "current_a": 500.0,
            "power_kw": 500.0,
            "energy_kwh": 999999.9999,
            "timestamp": time.time()
        }

        resp = await bridge.send_meter_values(telemetry_payload=telemetry_extreme, transaction_id=1003)
        assert resp == {}

        mv_logs = csms.get_meter_values(transaction_id=1003)
        assert len(mv_logs) == 1
        sampled = mv_logs[0]["meterValue"][0]["sampledValue"]
        sampled_dict = {s["measurand"]: s["value"] for s in sampled}

        assert sampled_dict["Voltage"] == "1000.0"
        assert sampled_dict["Current.Import"] == "500.00"
        assert sampled_dict["Power.Active.Import"] == "500.000"
        assert sampled_dict["Energy.Active.Import.Register"] == "999999.9999"

        # Verify board support model holds values without precision truncation
        assert bridge.everest_board.voltage_v == 1000.0
        assert bridge.everest_board.current_a == 500.0
        assert bridge.everest_board.power_kw == 500.0
        assert bridge.everest_board.energy_kwh == 999999.9999

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_meter_values_non_string_rejection_oca_compliance(self):
        """
        OCA OCPP 1.6 spec Section 7.33 mandates sampledValue.value must be a string (CiString50Type).
        Test that CSMS rejects non-string values (int, float, bool, None, dict, list).
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        non_string_samples = [
            ("integer", 230),
            ("float", 230.5),
            ("boolean", True),
            ("none", None),
            ("dict", {"val": 230}),
            ("list", [230.5]),
        ]

        for desc, invalid_val in non_string_samples:
            invalid_payload = {
                "connectorId": 1,
                "transactionId": 1004,
                "meterValue": [
                    {
                        "timestamp": current_iso_z(),
                        "sampledValue": [
                            {
                                "value": invalid_val,
                                "context": "Sample.Periodic",
                                "measurand": "Voltage",
                                "unit": "V"
                            }
                        ]
                    }
                ]
            }

            # 1. Direct call to VirtualCSMS.process_call
            resp_type, err_payload = await csms.process_call(
                charge_box_id=bridge.charge_box_id,
                action="MeterValues",
                payload=invalid_payload,
                unique_id=f"test_inv_{desc}"
            )
            assert resp_type == OCPPMessageType.CALL_ERROR, f"Expected CALL_ERROR for {desc}, got {resp_type}"
            assert err_payload["errorCode"] == OCPPCallErrorCode.FORMATION_VIOLATION.value
            assert "sampledValue.value must be a string" in err_payload["errorDescription"]
            assert err_payload["errorDetails"]["type"] == type(invalid_val).__name__

            # 2. Call through bridge._send_call raises OCPPCallError
            with pytest.raises(OCPPCallError) as exc_info:
                await bridge._send_call("MeterValues", invalid_payload)
            assert exc_info.value.error_code == OCPPCallErrorCode.FORMATION_VIOLATION.value

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_60s_reservation_timer_expiration(self):
        """Test 60-second reservation countdown tracking and expiration behavior."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # CSMS sends ReserveNow
        now = time.time()
        res_resp = await csms.send_reserve_now(
            charge_box_id=bridge.charge_box_id,
            connector_id=1,
            expiry_date=current_iso_z(now + 60),
            id_tag="DEADBEEF",
            reservation_id=5001
        )
        assert res_resp["status"] == "Accepted"
        assert bridge.everest_manager.state == "RESERVED"
        assert bridge.everest_manager.active_reservation_id == "5001"
        assert bridge.everest_manager.reservation_expires_at is not None
        assert abs(bridge.everest_manager.reservation_expires_at - (now + 60)) < 2.0

        # Simulate hardware timer expiration (60s passed, hardware reverts to AVAILABLE)
        timeout_state_payload = {
            "state": "AVAILABLE",
            "relay_state": "DE_ENERGIZED",
            "transition_reason": "RESERVATION_TIMEOUT",
            "uptime_millis": 65000
        }
        await bridge.handle_mqtt_state("evsarthi/ST001/1/state", timeout_state_payload)

        # StatusNotification sent with Available
        conn_status = csms.get_connector_status(bridge.charge_box_id, 1)
        assert conn_status["status"] == "Available"
        assert conn_status["errorCode"] == "NoError"
        assert bridge.everest_manager.state == "AVAILABLE"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_8s_auth_window_expiration(self):
        """Test 8-second authentication grant window tracking and expiration behavior."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # Simulate accepted authentication
        now = time.time()
        bridge.everest_auth.record_auth_event("RFID", "DEADBEEF", "ACCEPTED", window_ms=8000)

        assert bridge.everest_auth.auth_window_active is True
        assert abs(bridge.everest_auth.auth_window_expiry - (now + 8.0)) < 1.0

        # Simulate hardware timeout notification (user failed to plug in within 8s)
        timeout_payload = {
            "state": "AVAILABLE",
            "relay_state": "DE_ENERGIZED",
            "transition_reason": "AUTH_WINDOW_TIMEOUT",
            "uptime_millis": 12000
        }
        await bridge.handle_mqtt_state("evsarthi/ST001/1/state", timeout_payload)

        # Contactor relay remains de-energized
        assert bridge.everest_board.relay_state == "DE_ENERGIZED"
        assert bridge.everest_manager.state == "AVAILABLE"

        # Explicitly verify denied auth cancels the window
        bridge.everest_auth.record_auth_event("PIN", "9999", "DENIED")
        assert bridge.everest_auth.auth_window_active is False
        assert bridge.everest_auth.auth_window_expiry == 0.0

        await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Emergency Fault Injection & Stop Stress Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEmergencyFaultInjectionAndStopStress:
    """Stress tests hardware fault injection (>29.7A, >227V) during active charging and StopTransaction edge cases."""

    @pytest.mark.asyncio
    async def test_overcurrent_fault_injection_during_active_charging(self):
        """
        Verify that an overcurrent fault trip (>29.7A) during an active transaction
        immediately triggers an EmergencyStop StopTransaction and Faulted StatusNotification.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # 1. Establish an active charging session
        start_res = await bridge.send_start_transaction(id_tag="DEADBEEF", connector_id=1, meter_start=100)
        tx_id = start_res["transactionId"]
        assert tx_id is not None
        assert bridge.everest_manager.state == "CHARGING"
        assert bridge.everest_manager.active_transaction_id == tx_id
        bridge.everest_board.relay_state = "ENERGIZED"

        # 2. Inject overcurrent fault (>29.7A, e.g. 32.5A) via MQTT state payload
        fault_payload = {
            "state": "FAULTED",
            "relay_state": "DE_ENERGIZED",
            "fault_code": "OVERCURRENT",
            "transition_reason": "HARDWARE_TRIP_CURRENT_EXCEEDED: 32.5A > 29.7A",
            "uptime_millis": 45000
        }
        await bridge.handle_mqtt_state("evsarthi/ST001/1/state", fault_payload)

        # 3. Verify transaction was terminated in bridge and CSMS with EmergencyStop
        assert bridge.everest_manager.active_transaction_id is None
        assert bridge.everest_manager.state == "FAULTED"
        assert bridge.everest_board.is_tripped is True
        assert bridge.everest_board.fault_code == "OVERCURRENT"
        assert bridge.everest_board.relay_state == "DE_ENERGIZED"

        # Verify CSMS transaction record
        tx_record = csms.get_transaction(tx_id)
        assert tx_record is not None
        assert tx_record["status"] == "Stopped"
        assert tx_record["reason"] == "EmergencyStop"

        # 4. Verify StatusNotification in CSMS reflects Faulted and OverCurrentFailure
        conn_status = csms.get_connector_status(bridge.charge_box_id, 1)
        assert conn_status is not None
        assert conn_status["status"] == "Faulted"
        assert conn_status["errorCode"] == "OverCurrentFailure"
        assert conn_status["vendorErrorCode"] == "OVERCURRENT"
        assert "HARDWARE_TRIP_CURRENT_EXCEEDED" in conn_status["info"]

        # 5. Verify call log contains StopTransaction preceding StatusNotification
        calls = csms.get_call_log()
        stop_calls = [c for c in calls if c["action"] == "StopTransaction"]
        assert len(stop_calls) >= 1
        assert stop_calls[-1]["payload"]["transactionId"] == tx_id
        assert stop_calls[-1]["payload"]["reason"] == "EmergencyStop"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_overvoltage_fault_injection_during_active_charging(self):
        """
        Verify that an overvoltage fault trip (>227V) during an active transaction
        immediately triggers an EmergencyStop StopTransaction and Faulted StatusNotification.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # 1. Establish an active charging session
        start_res = await bridge.send_start_transaction(id_tag="A1B2C3D4", connector_id=1, meter_start=250)
        tx_id = start_res["transactionId"]
        assert bridge.everest_manager.state == "CHARGING"
        bridge.everest_board.relay_state = "ENERGIZED"

        # 2. Inject overvoltage fault (>227V, e.g. 238.4V) via MQTT state payload
        fault_payload = {
            "state": "FAULTED",
            "relay_state": "DE_ENERGIZED",
            "fault_code": "OVERVOLTAGE",
            "transition_reason": "HARDWARE_TRIP_VOLTAGE_EXCEEDED: 238.4V > 227V",
            "uptime_millis": 52000
        }
        await bridge.handle_mqtt_state("evsarthi/ST001/1/state", fault_payload)

        # 3. Verify transaction stopped with EmergencyStop
        tx_record = csms.get_transaction(tx_id)
        assert tx_record["status"] == "Stopped"
        assert tx_record["reason"] == "EmergencyStop"

        # 4. Verify StatusNotification reflects OverVoltage
        conn_status = csms.get_connector_status(bridge.charge_box_id, 1)
        assert conn_status["status"] == "Faulted"
        assert conn_status["errorCode"] == "OverVoltage"
        assert conn_status["vendorErrorCode"] == "OVERVOLTAGE"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_fault_injection_when_no_active_transaction(self):
        """Verify that a fault occurring while idle/available sends StatusNotification but no StopTransaction."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        assert bridge.everest_manager.active_transaction_id is None
        calls_before = len(csms.get_call_log("StopTransaction"))

        # Inject fault while AVAILABLE
        fault_payload = {
            "state": "FAULTED",
            "relay_state": "DE_ENERGIZED",
            "fault_code": "OVERCURRENT",
            "transition_reason": "COLD_TRIP_ON_STARTUP",
            "uptime_millis": 1000
        }
        await bridge.handle_mqtt_state("evsarthi/ST001/1/state", fault_payload)

        # StatusNotification sent
        conn_status = csms.get_connector_status(bridge.charge_box_id, 1)
        assert conn_status["status"] == "Faulted"
        assert conn_status["errorCode"] == "OverCurrentFailure"

        # NO StopTransaction called
        calls_after = len(csms.get_call_log("StopTransaction"))
        assert calls_after == calls_before

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_transaction_unknown_transaction_id(self):
        """
        Verify StopTransaction behavior with an unknown transactionId (not in CSMS transactions).
        OCPP 1.6 spec Section 3.2 specifies CSMS responds with StopTransaction.conf with idTagInfo.
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # Send StopTransaction for a non-existent transactionId 99999
        resp = await bridge.send_stop_transaction(transaction_id=99999, meter_stop=500, reason="Local")
        assert "idTagInfo" in resp
        assert resp["idTagInfo"]["status"] == "Accepted"

        # Calling send_stop_transaction without any active transaction in manager returns NoActiveTransaction
        resp_no_tx = await bridge.send_stop_transaction()
        assert resp_no_tx == {"status": "NoActiveTransaction"}

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_remote_stop_transaction_mismatched_transaction_id(self):
        """Verify that CSMS RemoteStopTransaction with mismatched transactionId is rejected."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # Start transaction 1001
        start_res = await bridge.send_start_transaction(id_tag="DEADBEEF", connector_id=1)
        tx_id = start_res["transactionId"]
        assert bridge.everest_manager.active_transaction_id == tx_id

        # CSMS attempts to remote stop with a wrong transaction ID 88888
        rem_stop_fail = await csms.send_remote_stop_transaction(
            charge_box_id=bridge.charge_box_id,
            transaction_id=88888
        )
        assert rem_stop_fail == {"status": "Rejected"}
        assert bridge.everest_manager.active_transaction_id == tx_id  # still active

        # CSMS remote stops with the correct transaction ID
        rem_stop_ok = await csms.send_remote_stop_transaction(
            charge_box_id=bridge.charge_box_id,
            transaction_id=tx_id
        )
        assert rem_stop_ok == {"status": "Accepted"}
        assert bridge.everest_manager.active_transaction_id is None

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_transaction_with_mismatching_or_override_id_tag(self):
        """
        Verify StopTransaction carrying an idTag different from the start tag
        (e.g. Master Admin RFID card stopping a user transaction).
        """
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()

        # Start with driver tag
        start_res = await bridge.send_start_transaction(id_tag="CAFEFACE", connector_id=1, meter_start=0)
        tx_id = start_res["transactionId"]

        # Stop with admin tag
        stop_res = await bridge.send_stop_transaction(
            transaction_id=tx_id,
            meter_stop=1200,
            reason="UnlockCommand",
            id_tag="MASTER_ADMIN_TAG"
        )
        assert stop_res.get("idTagInfo", {}).get("status") == "Accepted"

        # Verify CSMS record
        tx_record = csms.get_transaction(tx_id)
        assert tx_record["status"] == "Stopped"
        assert tx_record["meterStop"] == 1200
        assert tx_record["reason"] == "UnlockCommand"

        await bridge.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Disconnection & Exponential Backoff Reconnection Resiliency
# ══════════════════════════════════════════════════════════════════════════════

class TestDisconnectionAndReconnectionResiliency:
    """Stress tests live WebSocket connectivity, abrupt network drops, and exponential backoff retry."""

    @pytest.mark.asyncio
    async def test_live_websocket_connection_and_disconnect_backoff(self):
        """
        Verify real WebSocket connection over localhost with 'ocpp1.6' subprotocol,
        abrupt server drop, connection status transition to False, and clean shutdown.
        """
        received_frames = []

        async def mock_csms_handler(ws):
            try:
                async for raw_msg in ws:
                    msg_type, uid, action, payload, _ = parse_frame(raw_msg)
                    received_frames.append((action, payload))
                    if action == "BootNotification":
                        await ws.send(serialize_call_result(uid, {"status": "Accepted", "interval": 300}))
                    elif action == "StatusNotification":
                        await ws.send(serialize_call_result(uid, {}))
                    elif action == "Heartbeat":
                        await ws.send(serialize_call_result(uid, {"currentTime": current_iso_z()}))
            except Exception:
                pass

        # Spin up local WebSocket server on an OS-assigned port
        server = await websockets.serve(mock_csms_handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"])
        port = server.sockets[0].getsockname()[1]
        csms_url = f"ws://127.0.0.1:{port}"

        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="live", csms_url=csms_url)
        await bridge.start()

        # Verify initial live connection
        assert bridge.is_connected() is True
        assert bridge._boot_accepted is True

        # Send live Heartbeat
        hb_resp = await bridge.send_heartbeat()
        assert "currentTime" in hb_resp

        # Abruptly terminate server and close active client connections
        for ws in list(server.websockets):
            await ws.close(1001, "Server Abrupt Drop")
        server.close()
        await server.wait_closed()

        # Wait briefly for client receive loop to detect connection loss
        for _ in range(20):
            if not bridge.is_connected():
                break
            await asyncio.sleep(0.05)

        assert bridge.is_connected() is False

        # Attempting outbound call while disconnected raises ConnectionError immediately
        with pytest.raises(ConnectionError) as exc_info:
            await bridge.send_heartbeat()
        assert "WebSocket not connected" in str(exc_info.value)

        # Stop bridge and verify clean shutdown
        await bridge.stop()
        assert bridge.is_connected() is False
        assert len(bridge._pending_calls) == 0

    @pytest.mark.asyncio
    async def test_live_websocket_server_reconnect_recovery(self):
        """
        Verify that after an abrupt server drop, the bridge automatically
        reconnects upon server restoration via exponential backoff.
        """
        received_calls = []

        async def mock_csms_handler(ws):
            try:
                async for raw_msg in ws:
                    msg_type, uid, action, payload, _ = parse_frame(raw_msg)
                    received_calls.append(action)
                    if action == "BootNotification":
                        await ws.send(serialize_call_result(uid, {"status": "Accepted", "interval": 300}))
                    elif action == "StatusNotification":
                        await ws.send(serialize_call_result(uid, {}))
                    elif action == "Heartbeat":
                        await ws.send(serialize_call_result(uid, {"currentTime": current_iso_z()}))
                    elif action == "MeterValues":
                        await ws.send(serialize_call_result(uid, {}))
            except Exception:
                pass

        # Use a dynamically allocated ephemeral port for reconnect test to prevent TIME_WAIT port collisions
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = await websockets.serve(mock_csms_handler, "127.0.0.1", port, subprotocols=["ocpp1.6"])
        csms_url = f"ws://127.0.0.1:{port}"

        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="live", csms_url=csms_url)
        await bridge.start()
        assert bridge.is_connected() is True

        # Drop server abruptly
        for ws in list(server.websockets):
            await ws.close(1001, "Simulated network failure")
        server.close()
        await server.wait_closed()

        await asyncio.sleep(0.2)
        assert bridge.is_connected() is False

        # Wait 2.2 seconds (past the 2.0s initial backoff window) and restart server on same port
        await asyncio.sleep(2.2)
        server_restored = await websockets.serve(mock_csms_handler, "127.0.0.1", port, subprotocols=["ocpp1.6"])

        # Wait for bridge client loop to reconnect automatically
        reconnected = False
        for _ in range(50):
            if bridge.is_connected():
                reconnected = True
                break
            await asyncio.sleep(0.1)

        assert reconnected is True, "Bridge failed to auto-reconnect to restored CSMS"
        assert bridge.is_connected() is True

        # Verify live transmission after reconnection
        hb_after = await bridge.send_heartbeat()
        assert "currentTime" in hb_after
        assert "Heartbeat" in received_calls

        # Send MeterValues over the reconnected socket
        mv_payload = {
            "connector_id": 1,
            "voltage_v": 230.0,
            "current_a": 16.0,
            "power_kw": 3.68,
            "energy_kwh": 5.2
        }
        mv_resp = await bridge.send_meter_values(telemetry_payload=mv_payload, transaction_id=999)
        assert mv_resp == {}
        assert "MeterValues" in received_calls

        # Clean shutdown
        await bridge.stop()
        server_restored.close()
        await server_restored.wait_closed()

    @pytest.mark.asyncio
    async def test_server_disconnect_during_in_flight_call_cleanup(self):
        """
        Verify that if the CSMS server drops while a call is in flight,
        the pending call future is cleaned up upon timeout without memory leak.
        """
        async def mock_csms_hanging_handler(ws):
            try:
                async for raw_msg in ws:
                    # Intentionally do not respond; drop the connection instead
                    await ws.close(1011, "Internal server error")
                    break
            except Exception:
                pass

        server = await websockets.serve(mock_csms_hanging_handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"])
        port = server.sockets[0].getsockname()[1]
        csms_url = f"ws://127.0.0.1:{port}"

        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="live", csms_url=csms_url)

        # Connect background loop directly
        task = asyncio.create_task(bridge._live_websocket_loop())
        bridge._background_tasks.append(task)

        for _ in range(20):
            if bridge.is_connected():
                break
            await asyncio.sleep(0.05)

        assert bridge.is_connected() is True

        # Send call with a very short timeout (0.3s) where server hangs and drops
        with pytest.raises((TimeoutError, ConnectionError)):
            await bridge._send_call("Heartbeat", {}, timeout=0.3)

        # Verify no leaking futures in _pending_calls
        assert len(bridge._pending_calls) == 0

        await bridge.stop()
        server.close()
        await server.wait_closed()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_cleans_resources_and_cancels_pending(self):
        """Verify bridge.stop() cleanly cancels background tasks, closes sockets, and flushes state."""
        csms = VirtualCSMS()
        bridge = OCPPBridge(station_id="ST001", connector_id="1", mode="virtual", virtual_csms=csms)
        await bridge.start()
        assert bridge.is_connected() is True

        # Add mock pending future
        loop = asyncio.get_running_loop()
        dummy_fut = loop.create_future()
        bridge._pending_calls["dummy_123"] = dummy_fut

        await bridge.stop()

        assert bridge.is_connected() is False
        assert bridge._stop_event.is_set() is True
        assert len(bridge._pending_calls) == 0
        assert dummy_fut.done() is True
        assert isinstance(dummy_fut.exception(), asyncio.CancelledError)
        assert len(bridge._background_tasks) == 0
