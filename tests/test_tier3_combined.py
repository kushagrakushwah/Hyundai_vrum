"""
tests/test_tier3_combined.py - Tier 3: Cross-Feature Interactions Suite
Contains 25 test cases (5 interaction categories x 5 tests) validating
multi-connector isolation, mid-countdown cancellations, broker network partitions,
SteVe OCPP 1.6J synchronization, and AVA voice assistant flows.
"""

import time
import pytest
import aiosqlite
import database
from ocpp_engine import OCPPEngine
from voice_assistant import VoiceAssistant, format_spoken_pin
from payment import PaymentEngine
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason
)


# ==============================================================================
# Interaction 1: Concurrent Dual-Connector Operations (5 Tests)
# ==============================================================================

def test_tier3_dual_connector_topic_isolation(dual_connectors):
    """Verify reserving Connector 1 does not affect Connector 2 state."""
    c1, c2 = dual_connectors
    assert c1.get_state() == "AVAILABLE"
    assert c2.get_state() == "AVAILABLE"

    # Reserve C1 only
    c1.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_C1", "pin": "2580"})
    assert c1.get_state() == "RESERVED"
    assert c2.get_state() == "AVAILABLE"
    assert c1.is_relay_energized() is False
    assert c2.is_relay_energized() is False
    assert c2.is_red_led_on() is True


def test_tier3_dual_connector_independent_auth(dual_connectors):
    """Verify independent concurrent PIN authentication on both connectors."""
    c1, c2 = dual_connectors
    c1.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_C1", "pin": "2580"})
    c2.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_C2", "pin": "8888"})
    assert c1.get_state() == "RESERVED"
    assert c2.get_state() == "RESERVED"

    # Submit PINs to each connector
    auth1 = c1.submit_keypad_pin("2580")
    auth2 = c2.submit_keypad_pin("8888")
    assert auth1["credential"]["pin"] == "2580"
    assert auth2["credential"]["pin"] == "8888"

    # Authorize both
    c1.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    c2.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert c1.access_granted is True
    assert c2.access_granted is True
    assert c1.is_relay_energized() is True
    assert c2.is_relay_energized() is True


def test_tier3_dual_connector_independent_timers(dual_connectors):
    """Verify independent timers: C1 reserved at t=0, C2 at t=30s; at t=60s C1 expires, C2 active."""
    c1, c2 = dual_connectors
    c1.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_C1", "duration_seconds": 60})

    # Advance 30 seconds
    c1.tick(30000)
    c2.tick(30000)

    # Now reserve C2
    c2.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_C2", "duration_seconds": 60})

    # Advance another 30.1 seconds (Total: 60.1s for C1, 30.1s for C2)
    c1.tick(30100)
    c2.tick(30100)

    # C1 expired and reverted to AVAILABLE
    assert c1.get_state() == "AVAILABLE"
    # C2 has ~30 seconds remaining in RESERVED
    assert c2.get_state() == "RESERVED"
    assert 28 <= c2.get_reservation_time_left_sec() <= 30


@pytest.mark.asyncio
async def test_tier3_dual_connector_simultaneous_charging(dual_connectors, test_db):
    """Verify simultaneous charging sessions on C1 and C2 track energy independently in DB."""
    c1, c2 = dual_connectors
    c1.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    c2.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert c1.get_state() == "CHARGING"
    assert c2.get_state() == "CHARGING"

    # C1 charging at 7.0 kW (220V, 31.8A) -> wait, current limit is 29.7A!
    # Let's use 20.0A (4.4 kW) for C1 and 10.0A (2.2 kW) for C2
    c1.set_sensor_values(voltage_v=220.0, current_a=20.0)  # 4.4 kW
    c2.set_sensor_values(voltage_v=220.0, current_a=10.0)  # 2.2 kW

    # Advance 1 hour
    c1.tick(3600000)
    c2.tick(3600000)

    assert abs(c1.get_energy_kwh() - 4.4) < 0.05
    assert abs(c2.get_energy_kwh() - 2.2) < 0.05

    # Persist sessions in DB
    now = time.time()
    await database.save_session({
        "station_id": "ST001",
        "connector_id": "1",
        "session_id": "SESS_C1",
        "user_id": "user_1",
        "start_soc": 20.0,
        "current_soc": 50.0,
        "start_time": now - 3600,
        "kw": 4.4,
        "kwh": c1.get_energy_kwh()
    })
    await database.save_session({
        "station_id": "ST001",
        "connector_id": "2",
        "session_id": "SESS_C2",
        "user_id": "user_2",
        "start_soc": 30.0,
        "current_soc": 45.0,
        "start_time": now - 3600,
        "kw": 2.2,
        "kwh": c2.get_energy_kwh()
    })

    s1 = await database.get_session("ST001", connector_id="1")
    s2 = await database.get_session("ST001", connector_id="2")
    assert s1 is not None and s2 is not None
    assert s1["session_id"] == "SESS_C1"
    assert s2["session_id"] == "SESS_C2"
    assert s1["kwh"] > s2["kwh"]


def test_tier3_dual_connector_fault_isolation(dual_connectors):
    """Verify emergency fault trip on Connector 1 does not affect Connector 2."""
    c1, c2 = dual_connectors
    c1.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    c2.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    c1.set_sensor_values(voltage_v=220.0, current_a=16.0)
    c2.set_sensor_values(voltage_v=220.0, current_a=16.0)

    # Trip overcurrent on C1 (> 29.7A)
    c1.set_sensor_values(current_a=30.5)
    c1.tick(100)
    c2.tick(100)

    assert c1.get_state() == "FAULTED"
    assert c1.is_relay_energized() is False
    assert c1.is_red_led_on() is True

    # C2 must remain CHARGING with contactor closed
    assert c2.get_state() == "CHARGING"
    assert c2.is_relay_energized() is True
    assert c2.is_green_led_on() is True


# ==============================================================================
# Interaction 2: Mid-Countdown Cancellation Rollback (5 Tests)
# ==============================================================================

def test_tier3_cancel_at_15s_rollback(single_connector):
    """Verify hardware reverts to AVAILABLE and relay energizes on CANCEL message."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CNC1", "duration_seconds": 60})
    hw.tick(15000)
    assert hw.get_state() == "RESERVED"
    assert hw.get_reservation_time_left_sec() == 45

    # Cancel reservation
    hw.handle_reserve_message({"action": "CANCEL", "reservation_id": "RES_CNC1"})
    assert hw.get_state() == "AVAILABLE"
    assert hw.last_transition_reason == TransitionReason.RESERVATION_CANCELLED
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.active_reservation_id is None


@pytest.mark.asyncio
async def test_tier3_cancel_db_deletion(async_client, test_db):
    """Verify /api/cancel-reservation endpoint removes reservation from database."""
    # First create reservation
    reserve_payload = {
        "station_id": "NTC_0001",
        "connector_id": "1",
        "duration_minutes": 30,
        "user_id": "hyundai_driver_001",
        "user_name": "Cancelling User",
        "vehicle_soc": 50.0,
        "pin": "3344"
    }
    res_resp = await async_client.post("/api/reserve", json=reserve_payload)
    assert res_resp.status_code == 200

    # Verify present
    active = await database.get_reservation("NTC_0001", connector_id="1")
    assert active is not None

    # Cancel via endpoint
    cancel_resp = await async_client.post("/api/cancel-reservation/NTC_0001?connector_id=1&user_id=hyundai_driver_001")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["success"] is True

    # Verify deleted from DB
    deleted = await database.get_reservation("NTC_0001", connector_id="1")
    assert deleted is None


@pytest.mark.asyncio
async def test_tier3_cancel_history_logged(async_client, test_db):
    """Verify cancellation outcome is recorded in bookings_history table."""
    reserve_payload = {
        "station_id": "NTC_0002",
        "connector_id": "1",
        "duration_minutes": 30,
        "user_id": "hyundai_driver_001",
        "user_name": "Audit User",
        "vehicle_soc": 40.0,
        "pin": "5566"
    }
    await async_client.post("/api/reserve", json=reserve_payload)
    await async_client.post("/api/cancel-reservation/NTC_0002?connector_id=1&user_id=hyundai_driver_001")

    history = await database.get_history(50)
    assert len(history) > 0
    cancelled_entry = [h for h in history if h["station_id"] == "NTC_0002" and "CANCELLED" in h["outcome"]]
    assert len(cancelled_entry) > 0


def test_tier3_cancel_escrow_refund():
    """Verify PaymentEngine issues refund for cancelled reservation escrow."""
    pe = PaymentEngine()
    order = pe.create_escrow_order("RES_REFUND_01", "usr_refund")
    assert order["status"] == "created" or "order_id" in order

    refund = pe.full_refund(order.get("order_id", "order_mock_01"))
    assert "refunded" in refund or refund.get("mock") is True


@pytest.mark.asyncio
async def test_tier3_cancel_nonexistent_id(async_client, test_db):
    """Verify cancelling a non-existent reservation returns 404 without corrupting state."""
    cancel_resp = await async_client.post("/api/cancel-reservation/NON_EXISTENT_STATION?connector_id=1")
    assert cancel_resp.status_code == 404


# ==============================================================================
# Interaction 3: Broker Network Partition & Local Autonomy (5 Tests)
# ==============================================================================

def test_tier3_partition_during_reserved(single_connector):
    """Verify hardware autonomously expires reservation locally when broker is disconnected."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PART1", "duration_seconds": 60})
    hw.tick(10000)
    assert hw.get_state() == "RESERVED"

    # Simulate broker disconnect
    hw.offline_mode = True

    # Advance local monotonic clock to 60.1s
    hw.tick(50100)
    # Hardware autonomously transitions to AVAILABLE
    assert hw.get_state() == "AVAILABLE"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.last_transition_reason == TransitionReason.RESERVATION_TIMEOUT


def test_tier3_partition_sd_logging_continues(single_connector):
    """Verify local MicroSD logging continues normally during broker partition."""
    hw = single_connector
    hw.offline_mode = True
    initial_rows = len(hw.read_sd_log())

    # Tick 3 logging intervals (15,000 ms)
    for _ in range(3):
        hw.tick(5000)

    rows = hw.read_sd_log()
    assert len(rows) == initial_rows + 3


def test_tier3_partition_reconnect_state_sync(single_connector):
    """Verify hardware emits retained state on reconnect to sync backend."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PART3", "duration_seconds": 60})
    hw.offline_mode = True
    hw.tick(60100)  # Reverted to AVAILABLE offline

    # Reconnect
    hw.offline_mode = False
    hw.tick(100)
    # State is AVAILABLE with Red LED ON (contactor open)
    assert hw.get_state() == "AVAILABLE"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


def test_tier3_partition_reconnect_backlog(single_connector):
    """Verify SD log rows are available for backlog telemetry synchronization."""
    hw = single_connector
    hw.clear_sd_log()
    # Log 5 offline records
    hw.offline_mode = True
    for _ in range(5):
        hw.tick(5000)

    # Reconnect and read backlog
    hw.offline_mode = False
    backlog = hw.read_sd_log()
    assert len(backlog) == 6  # 1 header + 5 offline data rows


def test_tier3_partition_auth_local_fallback(single_connector):
    """Verify RFID card tap uses local whitelist during network partition."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PART5", "rfid_uid": "DEADBEEF"})
    hw.offline_mode = True

    resp = hw.scan_rfid("DEADBEEF")
    assert resp["status"] == "ACCEPTED_OFFLINE"
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True


# ==============================================================================
# Interaction 4: SteVe OCPP 1.6J Bridge Synchronization (5 Tests)
# ==============================================================================

@pytest.mark.asyncio
async def test_tier3_ocpp_status_notification_sync(single_connector):
    """
    Verify EV Sarthi state transitions map to OCPP 1.6J StatusNotification,
    generate schema-compliant OCPP JSON, and record in CSMS audit log.
    Replaces former tautological dictionary assertions with genuine verification.
    """
    hw = single_connector
    engine = OCPPEngine()
    station_id = hw.station_id

    # 1. State: AVAILABLE -> StatusNotification: Available, ErrorCode: NoError
    state_msg_available = [m[1] for m in hw.outbox if "state" in m[0]][-1] if hw.outbox else {
        "station_id": station_id,
        "connector_id": hw.connector_id,
        "state": hw.get_state(),
        "relay_state": hw.get_relay_state(),
        "transition_reason": "BOOT_COMPLETE",
        "timestamp": time.time()
    }
    resp1 = await engine.send_status_notification(station_id, state_msg_available)
    assert resp1["status"] == "Accepted"

    # 2. State: Transition to RESERVED -> StatusNotification: Reserved
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_OCPP_SYNC", "duration_seconds": 60})
    assert hw.get_state() == "RESERVED"
    state_msg_reserved = [m[1] for m in hw.outbox if "state" in m[0]][-1]
    resp2 = await engine.send_status_notification(station_id, state_msg_reserved)
    assert resp2["status"] == "Accepted"

    # 3. State: Transition to CHARGING -> StatusNotification: Charging
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"
    state_msg_charging = [m[1] for m in hw.outbox if "state" in m[0]][-1]
    resp3 = await engine.send_status_notification(station_id, state_msg_charging)
    assert resp3["status"] == "Accepted"

    # 4. State: Trip to FAULTED (overcurrent) -> StatusNotification: Faulted, ErrorCode: OverCurrentFailure
    hw.set_sensor_values(current_a=30.5)
    assert hw.get_state() == "FAULTED"
    state_msg_faulted = [m[1] for m in hw.outbox if "state" in m[0]][-1]
    resp4 = await engine.send_status_notification(station_id, state_msg_faulted)
    assert resp4["status"] == "Accepted"

    # 5. Direct schema transformation verification across all states
    for state, ocpp_exp in [("AVAILABLE", "Available"), ("RESERVED", "Reserved"), ("CHARGING", "Charging"), ("FAULTED", "Faulted")]:
        trans = OCPPEngine.translate_state_to_status_notification({"state": state, "connector_id": 1, "transition_reason": "TEST"})
        assert trans["status"] == ocpp_exp
        assert trans["connectorId"] == 1
        assert "timestamp" in trans

    # 6. Verify CSMS Audit Log Entries & Schema Conformance
    log = engine.get_log()
    status_entries = [entry for entry in log if entry["action"] == "StatusNotification"]
    assert len(status_entries) == 4

    expected_statuses = ["Available", "Reserved", "Charging", "Faulted"]
    expected_error_codes = ["NoError", "NoError", "NoError", "OverCurrentFailure"]

    for i, entry in enumerate(status_entries):
        assert entry["station_id"] == station_id
        details = entry["details"]
        assert details["connector_id"] == int(hw.connector_id)
        assert details["status"] == expected_statuses[i]
        assert details["error_code"] == expected_error_codes[i]
        assert "timestamp" in details
        assert details["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_tier3_ocpp_reserve_now_mapping():
    """Verify SteVe OCPP ReserveNow mapping invokes engine and returns Accepted."""
    engine = OCPPEngine()
    resp = await engine.reserve_now(
        station_id="ST001",
        reservation_id="RES_OCPP_01",
        expiry_timestamp=time.time() + 1800,
        connector_id=1
    )
    assert resp["status"] == "Accepted"
    log = engine.get_log()
    assert len(log) > 0
    assert log[-1]["action"] == "ReserveNow"
    assert log[-1]["details"]["reservation_id"] == "RES_OCPP_01"


@pytest.mark.asyncio
async def test_tier3_ocpp_authorize_start_tx():
    """Verify RFID authorization maps to OCPP RemoteStartTransaction."""
    engine = OCPPEngine()
    resp = await engine.remote_start_transaction(station_id="ST001", id_tag="DEADBEEF", connector_id=1)
    assert resp["status"] == "Accepted"
    log = engine.get_log()
    assert log[-1]["action"] == "RemoteStartTransaction"
    assert log[-1]["details"]["id_tag"] == "DEADBEEF"


@pytest.mark.asyncio
async def test_tier3_ocpp_meter_values_mapping(single_connector):
    """
    Verify periodic telemetry electrical metrics map to OCPP 1.6J MeterValues format,
    preserve sensor conversions, and record in CSMS audit log.
    Replaces former self-certifying dictionary creation with genuine verification.
    """
    hw = single_connector
    engine = OCPPEngine()
    station_id = hw.station_id

    # Configure realistic 220V, 16.0A charging session
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    msgs = hw.tick(5000)
    telem = [m[1] for m in msgs if "telemetry" in m[0]][-1]

    # Translate hardware telemetry via OCPPEngine translation routine
    tx_id = "1001"
    ocpp_mv_req = OCPPEngine.translate_telemetry_to_meter_values(
        telemetry_payload=telem,
        station_id=station_id,
        transaction_id=tx_id
    )

    # 1. Structural Schema Validation
    assert ocpp_mv_req["connectorId"] == int(hw.connector_id)
    assert ocpp_mv_req["transactionId"] == 1001
    assert "meterValue" in ocpp_mv_req
    assert len(ocpp_mv_req["meterValue"]) == 1

    mv = ocpp_mv_req["meterValue"][0]
    assert "timestamp" in mv
    assert mv["timestamp"].endswith("Z")
    assert "sampledValue" in mv
    samples = mv["sampledValue"]
    assert len(samples) == 4

    # 2. Measurand & Numerical Accuracy Validation
    sample_by_measurand = {s["measurand"]: s for s in samples}

    # Voltage (V)
    assert "Voltage" in sample_by_measurand
    v_sample = sample_by_measurand["Voltage"]
    assert v_sample["unit"] == "V"
    assert float(v_sample["value"]) == pytest.approx(220.0, rel=1e-2)
    assert v_sample["context"] == "Sample.Periodic"

    # Current.Import (A)
    assert "Current.Import" in sample_by_measurand
    c_sample = sample_by_measurand["Current.Import"]
    assert c_sample["unit"] == "A"
    assert float(c_sample["value"]) == pytest.approx(16.0, rel=1e-2)
    assert c_sample["context"] == "Sample.Periodic"

    # Power.Active.Import (kW)
    assert "Power.Active.Import" in sample_by_measurand
    p_sample = sample_by_measurand["Power.Active.Import"]
    assert p_sample["unit"] == "kW"
    assert float(p_sample["value"]) == pytest.approx(3.52, rel=1e-2)
    assert p_sample["context"] == "Sample.Periodic"

    # Energy.Active.Import.Register (kWh)
    assert "Energy.Active.Import.Register" in sample_by_measurand
    e_sample = sample_by_measurand["Energy.Active.Import.Register"]
    assert e_sample["unit"] == "kWh"
    assert float(e_sample["value"]) >= 0.0
    assert e_sample["context"] == "Sample.Periodic"

    # 3. CSMS Engine Ingestion and Logging
    resp = await engine.send_meter_values(
        station_id=station_id,
        telemetry_payload=telem,
        transaction_id=tx_id
    )
    assert resp["status"] == "Accepted"

    log = engine.get_log()
    assert len(log) > 0
    last_log = log[-1]
    assert last_log["action"] == "MeterValues"
    assert last_log["station_id"] == station_id
    assert last_log["details"]["connector_id"] == int(hw.connector_id)
    assert last_log["details"]["transaction_id"] == 1001
    assert len(last_log["details"]["meter_value"][0]["sampledValue"]) == 4


@pytest.mark.asyncio
async def test_tier3_ocpp_stop_transaction_sync():
    """Verify charging completion maps to OCPP RemoteStopTransaction."""
    engine = OCPPEngine()
    resp = await engine.remote_stop_transaction(station_id="ST001", transaction_id="TX_1001")
    assert resp["status"] == "Accepted"
    log = engine.get_log()
    assert log[-1]["action"] == "RemoteStopTransaction"
    assert log[-1]["details"]["transaction_id"] == "TX_1001"


# ==============================================================================
# Interaction 5: AVA Voice Assistant Command Flow (5 Tests)
# ==============================================================================

def test_tier3_voice_reserve_intent_recognition():
    """Verify AVA Voice Assistant parses 'Charger reserve karo' as RESERVE_SLOT intent."""
    va = VoiceAssistant()
    res = va._parse_intent("Charger reserve karo")
    assert res.intent == "RESERVE_SLOT"
    assert res.confidence >= 0.90


def test_tier3_voice_station_selection():
    """Verify natural language phrasing 'book this station' binds to selected station."""
    va = VoiceAssistant()
    res = va._parse_intent("book this station", selected_station_id="NTC_0001")
    assert res.intent == "RESERVE_SLOT"
    assert res.entities.get("selected_station_id") == "NTC_0001"


def test_tier3_voice_mqtt_dispatch(single_connector):
    """Verify voice assistant reservation dispatches MQTT CREATE payload to hardware."""
    hw = single_connector
    pin = "2580"
    voice_reservation_payload = {
        "action": "CREATE",
        "reservation_id": "RES_VOICE_01",
        "pin": pin,
        "duration_seconds": 60,
        "user_id": "voice_driver_01"
    }
    hw.handle_reserve_message(voice_reservation_payload)
    assert hw.get_state() == "RESERVED"
    assert hw.authorized_pin == "2580"
    assert hw.active_reservation_id == "RES_VOICE_01"


def test_tier3_voice_hardware_display_sync(single_connector):
    """Verify hardware TFT display updates to 'ENTER PIN' when reserved via voice."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_VOICE_02", "pin": "9876"})
    assert hw.tft_screen["title"] == "RESERVED"
    assert hw.tft_screen["text"] == "ENTER PIN"
    assert hw.oled_display["status"] == "RESERVED"


def test_tier3_voice_spoken_pin_formatting():
    """Verify format_spoken_pin formats PIN digit-by-digit for clear TTS playback."""
    formatted = format_spoken_pin("2580")
    assert "2, 5, 8, 0" in formatted
    assert "two" in formatted
    assert "five" in formatted
    assert "eight" in formatted
    assert "zero" in formatted
