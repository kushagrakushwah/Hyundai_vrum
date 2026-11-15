"""
tests/test_tier1_features.py - Tier 1: Core Feature Verification Suite
Contains 35 test cases (7 features x 5 tests) validating MQTT schemas,
nominal reservations, keypad/RFID authentication, ADC telemetry math,
relay/LED truth tables, and MicroSD CSV logging.
"""

import json
import time
import math
import pytest
import jsonschema
import database
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason
)


# ── JSON Schemas for Topic Contract Validation ─────────────────────────────────

SCHEMA_RESERVE = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "action": {"type": "string", "enum": ["CREATE", "CANCEL"]},
        "reservation_id": {"type": "string"},
        "user_id": {"type": "string"},
        "pin": {"type": "string"},
        "duration_seconds": {"type": "number"},
        "expires_at": {"type": "number"},
        "rfid_uid": {"type": ["string", "null"]}
    },
    "required": ["action", "reservation_id"]
}

SCHEMA_STATE = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "station_id": {"type": "string"},
        "connector_id": {"type": "string"},
        "state": {"type": "string", "enum": ["AVAILABLE", "RESERVED", "CHARGING", "FAULTED"]},
        "previous_state": {"type": "string"},
        "transition_reason": {"type": "string"},
        "relay_state": {"type": "string", "enum": ["ENERGIZED", "DE_ENERGIZED"]},
        "uptime_millis": {"type": "number"}
    },
    "required": ["station_id", "connector_id", "state", "relay_state", "uptime_millis"]
}

SCHEMA_TELEMETRY = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "station_id": {"type": "string"},
        "connector_id": {"type": "string"},
        "state": {"type": "string"},
        "millis": {"type": "number"},
        "voltage_v": {"type": "number"},
        "current_a": {"type": "number"},
        "power_kw": {"type": "number"},
        "energy_kwh": {"type": "number"},
        "voltage_raw": {"type": "integer", "minimum": 0, "maximum": 4095},
        "current_raw": {"type": "integer", "minimum": 0, "maximum": 4095},
        "raw": {"type": "object"},
        "electrical": {"type": "object"},
        "energy": {"type": "object"},
        "sd_status": {"type": "object"}
    },
    "required": ["station_id", "connector_id", "voltage_v", "current_a", "voltage_raw", "current_raw"]
}

SCHEMA_AUTH = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "station_id": {"type": "string"},
        "connector_id": {"type": "string"},
        "auth_type": {"type": "string", "enum": ["PIN", "RFID"]},
        "credential": {"type": "object"},
        "current_state": {"type": "string"}
    },
    "required": ["station_id", "connector_id", "auth_type", "credential"]
}

SCHEMA_CMD = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "action": {"type": "string", "enum": ["AUTH_DECISION", "START_CHARGING", "STOP_CHARGING", "RESET"]},
        "status": {"type": "string", "enum": ["ACCEPTED", "DENIED"]},
        "status_code": {"type": "integer"},
        "relay_action": {"type": "string", "enum": ["ENERGIZE", "DE_ENERGIZE"]}
    },
    "required": ["action"]
}


# ==============================================================================
# Feature 1: MQTT Schemas (5 Tests)
# ==============================================================================

def test_tier1_mqtt_schema_reserve(payload_builder):
    """Validate CREATE and CANCEL payloads comply with JSONSchema Draft-07."""
    create_payload = payload_builder.reserve_create(station_id="ST001", connector_id="1", pin="2580")
    jsonschema.validate(instance=create_payload, schema=SCHEMA_RESERVE)

    cancel_payload = payload_builder.reserve_cancel(station_id="ST001", connector_id="1", reservation_id="RES_01")
    jsonschema.validate(instance=cancel_payload, schema=SCHEMA_RESERVE)


def test_tier1_mqtt_schema_state(single_connector):
    """Validate state payloads across all 4 lifecycle states match schema."""
    hw = single_connector
    # 1. AVAILABLE
    msgs = hw.tick(100)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    assert len(state_msgs) > 0
    jsonschema.validate(instance=state_msgs[-1], schema=SCHEMA_STATE)
    assert state_msgs[-1]["state"] == "AVAILABLE"

    # 2. RESERVED
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_01", "pin": "2580", "duration_seconds": 60})
    msgs = hw.tick(100)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    jsonschema.validate(instance=state_msgs[-1], schema=SCHEMA_STATE)
    assert state_msgs[-1]["state"] == "RESERVED"

    # 3. CHARGING
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    msgs = hw.tick(100)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    jsonschema.validate(instance=state_msgs[-1], schema=SCHEMA_STATE)
    assert state_msgs[-1]["state"] == "CHARGING"

    # 4. FAULTED
    hw.set_sensor_values(current_a=31.0)
    msgs = hw.tick(100)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    jsonschema.validate(instance=state_msgs[-1], schema=SCHEMA_STATE)
    assert state_msgs[-1]["state"] == "FAULTED"


def test_tier1_mqtt_schema_telemetry(payload_builder, single_connector):
    """Validate high-frequency telemetry payload schema and bounds."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    msgs = hw.tick(5000)  # Trigger telemetry
    telem_msgs = [m[1] for m in msgs if "telemetry" in m[0]]
    assert len(telem_msgs) > 0
    payload = telem_msgs[-1]
    jsonschema.validate(instance=payload, schema=SCHEMA_TELEMETRY)
    assert 0 <= payload["voltage_raw"] <= 4095
    assert 0 <= payload["current_raw"] <= 4095
    assert payload["electrical"]["frequency_hz"] == 50.0


def test_tier1_mqtt_schema_auth(single_connector):
    """Validate auth submission schema for both PIN and RFID."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_01", "pin": "2580"})
    hw.tick(100)

    # PIN auth
    pin_payload = hw.submit_keypad_pin("2580")
    jsonschema.validate(instance=pin_payload, schema=SCHEMA_AUTH)
    assert pin_payload["auth_type"] == "PIN"
    assert pin_payload["credential"]["pin"] == "2580"

    # RFID auth
    rfid_payload = hw.scan_rfid("DEADBEEF")
    jsonschema.validate(instance=rfid_payload, schema=SCHEMA_AUTH)
    assert rfid_payload["auth_type"] == "RFID"
    assert rfid_payload["credential"]["rfid_uid"] == "DEADBEEF"


def test_tier1_mqtt_schema_cmd(payload_builder):
    """Validate backend decision /cmd schema compliance."""
    accepted_cmd = payload_builder.cmd_decision(status="ACCEPTED", status_code=200, relay_action="ENERGIZE")
    jsonschema.validate(instance=accepted_cmd, schema=SCHEMA_CMD)

    denied_cmd = payload_builder.cmd_decision(status="DENIED", status_code=401, relay_action="DE_ENERGIZE")
    jsonschema.validate(instance=denied_cmd, schema=SCHEMA_CMD)


# ==============================================================================
# Feature 2: Nominal Reservation Lifecycle (5 Tests)
# ==============================================================================

def test_tier1_reservation_nominal_create(single_connector):
    """Verify hardware transitions from AVAILABLE to RESERVED on valid reserve message."""
    hw = single_connector
    assert hw.get_state() == "AVAILABLE"
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_1001",
        "user_id": "usr_test",
        "pin": "2580",
        "duration_seconds": 60
    })
    assert hw.get_state() == "RESERVED"
    assert hw.active_reservation_id == "RES_1001"
    assert hw.authorized_pin == "2580"


def test_tier1_reservation_relay_lock(single_connector):
    """Verify relay remains DE_ENERGIZED (Red LED ON, Green LED OFF, contactor isolated) on transition from AVAILABLE to RESERVED."""
    hw = single_connector
    # In AVAILABLE, relay is DE_ENERGIZED for electrical contactor safety
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False

    # In RESERVED, relay remains DE_ENERGIZED until authentication
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_1002", "pin": "2580"})
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier1_reservation_timer_armed(single_connector):
    """Verify 60-second countdown timer is properly armed."""
    hw = single_connector
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_1003",
        "pin": "2580",
        "duration_seconds": 60
    })
    assert hw.get_reservation_time_left_sec() == 60
    assert hw.reservation_expiry_millis == hw.current_millis + 60000


def test_tier1_reservation_state_published(single_connector):
    """Verify retained MQTT state message is emitted on reservation."""
    hw = single_connector
    hw.tick(100)  # Clear boot messages
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_1004", "pin": "2580"})
    msgs = hw.tick(100)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    assert len(state_msgs) > 0
    assert state_msgs[-1]["state"] == "RESERVED"
    assert state_msgs[-1]["transition_reason"] == TransitionReason.RESERVATION_RECEIVED.value
    assert state_msgs[-1]["active_reservation_id"] == "RES_1004"


@pytest.mark.asyncio
async def test_tier1_reservation_db_sync(async_client, test_db):
    """Verify FastAPI POST /api/reserve synchronizes with SQLite reservations table."""
    payload = {
        "station_id": "NTC_0001",
        "connector_id": "1",
        "duration_minutes": 30,
        "user_id": "user_e2e_01",
        "user_name": "E2E Tester",
        "vehicle_soc": 45.0,
        "pin": "2580"
    }
    response = await async_client.post("/api/reserve", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert "reservation_id" in res_data

    # Query DB to verify persistence
    row = await database.get_reservation("NTC_0001", connector_id="1")
    assert row is not None
    assert row["pin"] == "2580"
    assert row["user_id"] == "user_e2e_01"


# ==============================================================================
# Feature 3: Keypad PIN Authentication (5 Tests)
# ==============================================================================

def test_tier1_keypad_valid_4digit_pin(single_connector):
    """Verify entering and submitting 4-digit PIN transitions to access grant window."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PIN1", "pin": "2580"})

    # Enter PIN keys
    for k in "2580":
        hw.press_key(k)
    auth_req = hw.press_key("#")
    assert auth_req["credential"]["pin"] == "2580"

    # Backend responds ACCEPTED
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True
    assert hw.is_green_led_on() is True


def test_tier1_keypad_valid_6digit_pin(single_connector):
    """Verify entering and submitting 6-digit PIN works seamlessly."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PIN2", "pin": "123456"})

    for k in "123456":
        hw.press_key(k)
    auth_req = hw.press_key("#")
    assert auth_req["credential"]["pin"] == "123456"

    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True


def test_tier1_keypad_invalid_pin_rejected(single_connector):
    """Verify incorrect PIN submission is rejected and relay stays de-energized."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PIN3", "pin": "2580"})

    for k in "9999":
        hw.press_key(k)
    hw.press_key("#")

    # Backend returns DENIED
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "DENIED", "status_code": 401})
    assert hw.access_granted is False
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.get_state() == "RESERVED"


def test_tier1_keypad_auth_payload_structure(single_connector):
    """Verify '#' submission generates exact schema-compliant auth payload."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PIN4", "pin": "4321"})

    for k in "4321":
        hw.press_key(k)
    payload = hw.press_key("#")
    assert payload["auth_type"] == "PIN"
    assert payload["credential"] == {"pin": "4321"}
    assert payload["station_id"] == hw.station_id
    assert payload["connector_id"] == hw.connector_id
    assert payload["current_state"] == "RESERVED"


def test_tier1_keypad_denied_attempt_counter(single_connector):
    """Verify multiple failed PIN attempts do not lock out or energize relay improperly."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PIN5", "pin": "2580"})

    for _ in range(3):
        for k in "0000":
            hw.press_key(k)
        hw.press_key("#")
        hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "DENIED", "status_code": 401})
        assert hw.access_granted is False
        assert hw.is_relay_energized() is False
        assert hw.get_state() == "RESERVED"


# ==============================================================================
# Feature 4: RFID Card Authentication (5 Tests)
# ==============================================================================

def test_tier1_rfid_valid_4byte_card(single_connector):
    """Verify scanning valid 4-byte RFID card ('DEADBEEF') grants access."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_RF1", "rfid_uid": "DEADBEEF"})

    auth_payload = hw.scan_rfid("DEADBEEF")
    assert auth_payload["credential"]["rfid_uid"] == "DEADBEEF"

    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True
    assert hw.is_green_led_on() is True


def test_tier1_rfid_valid_7byte_card(single_connector):
    """Verify scanning valid 7-byte MIFARE card UID works cleanly."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_RF2", "rfid_uid": "04A1B2C3D4E5F6"})

    auth_payload = hw.scan_rfid("04A1B2C3D4E5F6")
    assert auth_payload["credential"]["rfid_uid"] == "04A1B2C3D4E5F6"

    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True


def test_tier1_rfid_unauthorized_card(single_connector):
    """Verify unauthorized RFID card tap is rejected."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_RF3", "rfid_uid": "DEADBEEF"})

    hw.scan_rfid("BAADF00D")
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "DENIED", "status_code": 403})
    assert hw.access_granted is False
    assert hw.is_relay_energized() is False
    assert hw.get_state() == "RESERVED"


def test_tier1_rfid_payload_formatting(single_connector):
    """Verify lowercase or formatted RFID string is normalized to uppercase hex."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_RF4"})

    auth_payload = hw.scan_rfid("de:ad:be:ef")
    assert auth_payload["credential"]["rfid_uid"] == "DEADBEEF"

    auth_payload2 = hw.scan_rfid("04 a1 b2 c3 d4 e5 f6")
    assert auth_payload2["credential"]["rfid_uid"] == "04A1B2C3D4E5F6"


def test_tier1_rfid_local_whitelist_fallback(single_connector):
    """Verify local whitelist grants access when station is offline."""
    hw = single_connector
    hw.offline_mode = True
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_RF5", "rfid_uid": "DEADBEEF"})

    resp = hw.scan_rfid("DEADBEEF")
    assert resp["status"] == "ACCEPTED_OFFLINE"
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True


# ==============================================================================
# Feature 5: ADC Telemetry Calculations (5 Tests)
# ==============================================================================

def test_tier1_adc_voltage_conversion(single_connector):
    """Verify voltage scaling formula V = (voltage_raw / 4095.0) * 250.0."""
    hw = single_connector
    hw.set_sensor_values(voltage_raw=3767)
    expected_v = (3767 / 4095.0) * 250.0
    assert abs(hw.voltage_v - round(expected_v, 1)) < 0.1
    assert abs(hw.voltage_v - 230.0) < 0.2


def test_tier1_adc_current_conversion(single_connector):
    """Verify current scaling formula I = (current_raw / 4095.0) * 32.0."""
    hw = single_connector
    hw.set_sensor_values(current_raw=2048)
    expected_a = (2048 / 4095.0) * 32.0
    assert abs(hw.current_a - round(expected_a, 2)) < 0.05
    assert abs(hw.current_a - 16.0) < 0.1


def test_tier1_adc_power_calculation(single_connector):
    """Verify active power formula P = (V * I) / 1000.0 kW."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=230.0, current_a=16.0)
    expected_kw = (230.0 * 16.0) / 1000.0
    assert abs(hw.get_power_kw() - round(expected_kw, 3)) < 0.005
    assert hw.get_power_kw() == 3.68


def test_tier1_adc_energy_accumulation(single_connector):
    """Verify energy accumulation E = sum(P * delta_t / 3600) in CHARGING state."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)  # 3.52 kW

    # Advance simulated time by 1 hour (3,600,000 ms)
    hw.tick(3600000)
    assert abs(hw.get_energy_kwh() - 3.52) < 0.01


def test_tier1_adc_frequency_constant(single_connector):
    """Verify grid frequency constant 50.0 Hz in electrical telemetry packet."""
    hw = single_connector
    msgs = hw.tick(5000)
    telem_msgs = [m[1] for m in msgs if "telemetry" in m[0]]
    assert len(telem_msgs) > 0
    assert telem_msgs[-1]["electrical"]["frequency_hz"] == 50.0


# ==============================================================================
# Feature 6: Relay & Status LED Truth Table (5 Tests)
# ==============================================================================

def test_tier1_relay_available_state(single_connector):
    """Verify AVAILABLE: Relay DE_ENERGIZED, Red LED ON, Green LED OFF (fail-safe contactor open)."""
    hw = single_connector
    assert hw.get_state() == "AVAILABLE"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier1_relay_reserved_state(single_connector):
    """Verify RESERVED: Relay DE_ENERGIZED, Red LED ON, Green LED OFF."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_R1", "pin": "2580"})
    assert hw.get_state() == "RESERVED"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier1_relay_charging_state(single_connector):
    """Verify CHARGING: Relay ENERGIZED, Green LED ON, Red LED OFF."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"
    assert hw.get_relay_state() == "ENERGIZED"
    assert hw.is_green_led_on() is True
    assert hw.is_red_led_on() is False


def test_tier1_relay_faulted_state(single_connector):
    """Verify FAULTED: Relay DE_ENERGIZED, Red LED ON, Green LED OFF."""
    hw = single_connector
    hw.set_sensor_values(current_a=30.0)  # Trip overcurrent
    assert hw.get_state() == "FAULTED"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier1_relay_boot_failsafe(single_connector):
    """Verify hardware boot state initializes GPIO 15 LOW (fail-safe open)."""
    hw = single_connector
    # Execute restart() to test raw boot initialization
    hw.restart()
    # At boot, state returns to AVAILABLE after completing boot sequence with relay DE_ENERGIZED
    assert hw.get_state() == "AVAILABLE"
    assert hw.previous_state == "BOOTING"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


# ==============================================================================
# Feature 7: MicroSD CSV Logging Schema (5 Tests)
# ==============================================================================

def test_tier1_sd_csv_header_format(single_connector):
    """Verify /telemetry.csv first line contains exact 11-column header."""
    hw = single_connector
    rows = hw.read_sd_log()
    assert len(rows) >= 1
    expected_header = "timestamp_utc,millis,station_id,connector_id,state,voltage_raw,current_raw,voltage_v,current_a,power_kw,energy_kwh\n"
    assert rows[0] == expected_header


def test_tier1_sd_csv_column_count(single_connector):
    """Verify logged CSV lines have exactly 11 columns with valid tokens."""
    hw = single_connector
    hw.tick(5000)  # Log one row
    rows = hw.read_sd_log()
    assert len(rows) >= 2
    data_row = rows[1].strip()
    columns = data_row.split(",")
    assert len(columns) == 11
    # Check column names: timestamp_utc,millis,station_id,connector_id,state,...
    assert columns[2] == hw.station_id
    assert columns[3] == hw.connector_id
    assert columns[4] == hw.get_state()


def test_tier1_sd_csv_data_formatting(single_connector):
    """Verify numeric field precision in CSV log row."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=230.5, current_a=16.20)
    hw.tick(5000)
    rows = hw.read_sd_log()
    data_row = rows[-1].strip().split(",")
    # voltage_v is index 7 (%.1f), current_a is index 8 (%.2f), power_kw is index 9 (%.3f)
    assert data_row[7] == "230.5"
    assert data_row[8] == "16.20"
    assert data_row[9] == "3.734"


def test_tier1_sd_csv_append_mode(single_connector):
    """Verify periodic logging appends lines without overwriting file."""
    hw = single_connector
    hw.clear_sd_log()
    assert len(hw.read_sd_log()) == 1  # Header only

    for _ in range(5):
        hw.tick(5000)

    rows = hw.read_sd_log()
    assert len(rows) == 6  # 1 header + 5 appended rows


def test_tier1_sd_csv_flush_integrity(single_connector):
    """Verify data is committed to simulated storage buffer immediately."""
    hw = single_connector
    initial_len = len(hw.read_sd_log())
    hw.tick(5000)
    new_len = len(hw.read_sd_log())
    assert new_len == initial_len + 1
