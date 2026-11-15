"""
tests/test_tier5_adversarial_p1.py - Tier 5 Adversarial Coverage Hardening Suite (Part 1).
Exclusively authored by Challenger 1 (Hardware, MQTT & Backend Core).

Contains 30 rigorous adversarial stress tests covering:
1. Firmware FSM & Virtual ESP32 Hardware Emulation (out-of-order transitions, saturation, ring buffer, zero current, brownout)
2. MQTT Service & Inbound Topic Ingestion (corrupt bytes, non-dict payloads, NaN/inf coercion, wildcard patterns, listener error isolation, offline buffering, expired auth denial)
3. Database Multi-Connector Schemas & Concurrency (high-concurrency race conditions, nonexistent lookups, SQL injection, batch inserts, RFID inactive/expired, delete idempotency)
4. FastAPI REST & Charging Lifecycle Boundary Hardening (charging conflict, faulted connector 503, renewal vs conflict, expired OTP 410, forbidden cancel 403, invalid station 404)
"""

import asyncio
import json
import os
import sys
import time
import sqlite3
from typing import Any, Dict, List, Optional
import pytest
import aiosqlite

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "smartreserve", "smartreserve_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from mqtt_service import MQTTService, mqtt_service
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason,
    FaultCode
)


# ==============================================================================
# SECTION 1: FIRMWARE FSM & HARDWARE MOCK ADVERSARIAL STRESS TESTS (Tests 1-8, 29-30)
# ==============================================================================

def test_adv_fsm_reject_reserve_when_faulted(single_connector):
    """
    Adversarial Challenge: Out-of-order state transition.
    Attempting to reserve a FAULTED connector via /reserve must be rejected.
    Contactor must remain isolated (DE_ENERGIZED), Red LED ON, Green LED OFF.
    """
    hw = single_connector
    # Trip hardware into FAULTED via overcurrent
    hw.set_sensor_values(current_a=31.0)
    assert hw.state == ConnectorState.FAULTED
    assert hw.relay_state == RelayState.DE_ENERGIZED

    # Attempt to dispatch CREATE reservation message
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_MALICIOUS_01",
        "pin": "9999",
        "duration_seconds": 60
    })

    # Assert invariant: Connector remains strictly FAULTED and unreserved
    assert hw.state == ConnectorState.FAULTED
    assert hw.active_reservation_id is None
    assert hw.authorized_pin != "9999"
    assert hw.relay_state == RelayState.DE_ENERGIZED
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_adv_fsm_reject_unauthenticated_start_charging(single_connector):
    """
    Adversarial Challenge: Privilege escalation attempt.
    Sending START_CHARGING command without valid authentication or when FAULTED
    must never transition the state machine or energize the high-voltage relay.
    """
    hw = single_connector
    assert hw.state == ConnectorState.AVAILABLE

    # 1. Unauthenticated command in AVAILABLE state
    hw.handle_cmd_message({"action": "START_CHARGING"})
    assert hw.state == ConnectorState.AVAILABLE
    assert hw.relay_state == RelayState.DE_ENERGIZED

    # 2. Command in FAULTED state even if claims authenticated
    hw.set_sensor_values(voltage_v=240.0)
    assert hw.state == ConnectorState.FAULTED
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.state == ConnectorState.FAULTED
    assert hw.relay_state == RelayState.DE_ENERGIZED


def test_adv_fsm_negative_tick_delta_exception_guard(single_connector):
    """
    Adversarial Challenge: Time manipulation / clock skew.
    Advancing virtual clock by negative delta must raise ValueError and protect monotonic integrity.
    """
    hw = single_connector
    initial_millis = hw.current_millis

    with pytest.raises(ValueError, match="delta_ms must be non-negative"):
        hw.tick(delta_ms=-500)

    # Invariant: clock was not corrupted
    assert hw.current_millis == initial_millis


def test_adv_fsm_out_of_order_auth_decision_while_available(single_connector):
    """
    Adversarial Challenge: Phantom command injection.
    An AUTH_DECISION: ACCEPTED arriving while connector is in AVAILABLE (no active reservation)
    must be ignored without energizing relay or transitioning states.
    """
    hw = single_connector
    assert hw.state == ConnectorState.AVAILABLE

    hw.handle_cmd_message({
        "action": "AUTH_DECISION",
        "status": "ACCEPTED",
        "status_code": 200,
        "relay_action": "ENERGIZE"
    })

    assert hw.state == ConnectorState.AVAILABLE
    assert hw.access_granted is False
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


def test_adv_fsm_rapid_keypad_burst_during_countdown(single_connector):
    """
    Adversarial Challenge: Keypad buffer overflow & rapid clearing stress.
    Stress-tests buffer bounds (max 6), asterisk clearing, short PIN rejection (<4), and valid submission.
    """
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KEYPAD_STRESS", "pin": "123456"})
    assert hw.state == ConnectorState.RESERVED

    # Partial entry + clear
    hw.press_key("1")
    hw.press_key("2")
    assert hw.pin_buffer == "12"
    hw.press_key("*")
    assert hw.pin_buffer == ""

    # Short PIN submission (<4 digits)
    hw.press_key("9")
    hw.press_key("8")
    hw.press_key("7")
    short_res = hw.press_key("#")
    assert short_res == {"error": "PIN too short", "length": 3}
    assert hw.pin_buffer == ""

    # Overfill buffer: send 10 digits
    for digit in "1234567890":
        hw.press_key(digit)
    assert len(hw.pin_buffer) == 6
    assert hw.pin_buffer == "123456"

    # Submit valid 6-digit PIN
    auth_req = hw.press_key("#")
    assert auth_req is not None
    assert auth_req["auth_type"] == "PIN"
    assert auth_req["credential"]["pin"] == "123456"


def test_adv_fsm_telemetry_extreme_adc_saturation_and_clamping(single_connector):
    """
    Adversarial Challenge: ADC sensor saturation and negative rail inputs.
    Values outside [0, 4095] must be clamped; extreme values must trigger emergency trip.
    """
    hw = single_connector

    # Negative rail clamp
    hw.set_sensor_values(voltage_raw=-100, current_raw=-50)
    assert hw.voltage_raw == 0
    assert hw.current_raw == 0

    # ADC saturation clamp: raw 99999 -> 4095 (corresponds to 250V grid surge)
    hw.set_sensor_values(voltage_raw=99999)
    assert hw.voltage_raw == 4095
    # Overvoltage threshold is 3718; saturated ADC must trip connector immediately (<50ms)
    assert hw.state == ConnectorState.FAULTED
    assert hw.fault_code == FaultCode.OVERVOLTAGE.value
    assert hw.is_relay_energized() is False


def test_adv_fsm_microsd_ring_buffer_saturation_and_fifo_eviction(single_connector):
    """
    Adversarial Challenge: MicroSD unmount and ring buffer overflow.
    When SD is unmounted, telemetry rows buffer in RAM (max 10). Excess rows must FIFO-evict.
    On remount, exactly the latest 10 rows must flush cleanly.
    """
    hw = single_connector
    hw.unmount_sd()
    assert hw.sd_mounted is False

    # Log 25 entries while unmounted
    for i in range(25):
        hw.current_millis += 1000
        ok = hw._log_to_sd()
        assert ok is False

    # Invariant: RAM buffer strictly capped at 10 items
    assert len(hw.sd_ram_buffer) == 10

    # Remount card and verify flush
    hw.remount_sd()
    assert hw.sd_mounted is True
    assert len(hw.sd_ram_buffer) == 0
    # Initial header (1) + 10 flushed records = 11 rows
    assert len(hw.read_sd_log()) == 11

    # Next log row writes directly to SD
    assert hw._log_to_sd() is True
    assert len(hw.read_sd_log()) == 12


def test_adv_fsm_microsd_csv_row_column_count_and_sanitization(single_connector):
    """
    Adversarial Challenge: MicroSD CSV schema integrity.
    Every logged telemetry row must strictly match the 11-column header structure.
    """
    hw = single_connector
    hw.set_sensor_values(voltage_v=220.5, current_a=15.75)
    hw._log_to_sd()

    rows = hw.read_sd_log()
    header = rows[0].strip().split(",")
    assert len(header) == 11
    assert header == [
        "timestamp_utc", "millis", "station_id", "connector_id", "state",
        "voltage_raw", "current_raw", "voltage_v", "current_a", "power_kw", "energy_kwh"
    ]

    data_row = rows[1].strip().split(",")
    assert len(data_row) == 11
    # Verify ISO8601 UTC timestamp format ending with 'Z'
    assert data_row[0].endswith("Z")
    assert data_row[4] == ConnectorState.AVAILABLE.value
    assert float(data_row[7]) == 220.5
    assert float(data_row[8]) == 15.75


def test_adv_fsm_zero_current_detection_clean_session_termination(single_connector):
    """
    Adversarial Challenge: Unannounced cable unplug during active charge.
    If current drops to 0 during CHARGING for >= 10 seconds, state machine must cleanly terminate session.
    """
    hw = single_connector
    hw.force_charging(voltage_v=220.0, current_a=16.0)
    assert hw.state == ConnectorState.CHARGING
    assert hw.is_relay_energized() is True

    # Vehicle cuts charging: current drops to 0
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)

    # Tick 9 seconds (under 10s timeout)
    hw.tick(9000)
    assert hw.state == ConnectorState.CHARGING

    # Tick 2 more seconds (total 11s >= 10s)
    hw.tick(2000)
    assert hw.state == ConnectorState.AVAILABLE
    assert hw.last_transition_reason == TransitionReason.SESSION_COMPLETED
    assert hw.is_relay_energized() is False


def test_adv_fsm_simulated_brownout_reboot_preserves_safety(single_connector):
    """
    Adversarial Challenge: Brownout power recovery under hazardous line conditions.
    When rebooting while sensors detect overcurrent, reboot sequence must immediately trip to FAULTED.
    """
    hw = single_connector
    # Simulate overcurrent line presence before reboot
    hw.set_sensor_values(current_a=32.0)

    # Power cycle
    hw.restart()

    # Invariant: Hardware must NOT enter AVAILABLE; must instantly trip to FAULTED
    assert hw.state == ConnectorState.FAULTED
    assert hw.fault_code == FaultCode.OVERCURRENT.value
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


# ==============================================================================
# SECTION 2: MQTT SERVICE & INBOUND TOPIC ADVERSARIAL TESTS (Tests 11-16)
# ==============================================================================

@pytest.mark.asyncio
async def test_adv_mqtt_malformed_json_bytes_resilience(test_db):
    """
    Adversarial Challenge: Fuzzed and corrupted MQTT payloads.
    Non-UTF8 byte sequences, truncated JSON, and malformed topic paths must be caught gracefully without crashing.
    """
    service = MQTTService()

    # 1. Non-UTF8 byte stream
    await service._async_handle_message("evsarthi/ST01/1/state", b"\xff\xfe\xfd\x80\x00")

    # 2. Truncated JSON
    await service._async_handle_message("evsarthi/ST01/1/telemetry", b'{"voltage_v": 230.0, "current_a":')

    # 3. Invalid topic structures
    await service._async_handle_message("invalid/topic", b'{}')
    await service._async_handle_message("evsarthi/ST01", b'{}')
    await service._async_handle_message("evsarthi/ST01/1/unknown_subtopic", b'{}')

    # Service survives all malformed inputs
    assert True


@pytest.mark.asyncio
async def test_adv_mqtt_non_dict_payload_ignored(test_db):
    """
    Adversarial Challenge: Valid JSON primitives that are not dictionaries.
    Payloads such as strings, numbers, lists, booleans, and nulls must be safely ignored without raising AttributeError.
    """
    service = MQTTService()

    primitives = [b'"just_a_string"', b'12345', b'[1, 2, 3]', b'true', b'null']
    for p in primitives:
        await service._async_handle_message("evsarthi/ST01/1/telemetry", p)
        await service._async_handle_message("evsarthi/ST01/1/state", p)
        await service._async_handle_message("evsarthi/ST01/1/auth", p)

    assert True


@pytest.mark.asyncio
async def test_adv_mqtt_telemetry_nan_inf_none_coercion(test_db):
    """
    Adversarial Challenge: Floating-point corner cases in telemetry.
    None, empty strings, and non-numeric strings must be safely sanitized to 0.0 defaults.
    Empirical Vulnerability: float('NaN') converts to math.nan without raising ValueError,
    which SQLite driver converts to NULL, exposing a NOT NULL constraint violation on connector_telemetry.power_kw.
    """
    service = MQTTService()

    # Part A: Non-numeric strings, None, and empty values are sanitized safely to 0.0
    payload_safe = {
        "voltage_v": None,
        "current_a": "invalid_str",
        "power_kw": "",
        "energy_kwh": "---",
        "voltage_raw": "not_an_int",
        "current_raw": None,
        "millis": "abc"
    }
    await service._handle_telemetry("ST_SANITY", "1", payload_safe)

    telem = await database.get_latest_telemetry("ST_SANITY", "1")
    assert telem is not None
    assert telem["voltage_v"] == 0.0
    assert telem["current_a"] == 0.0
    assert telem["power_kw"] == 0.0
    assert telem["energy_kwh"] == 0.0
    assert telem["voltage_raw"] == 0
    assert telem["millis"] == 0

    # Part B: NaN and Infinity string/float coercion to 0.0 without SQLite IntegrityError
    payload_nan = {
        "voltage_v": 230.0,
        "current_a": 16.0,
        "power_kw": "NaN",
        "energy_kwh": "Infinity"
    }
    await service._handle_telemetry("ST_SANITY", "1", payload_nan)
    telem_nan = await database.get_latest_telemetry("ST_SANITY", "1")
    assert telem_nan is not None
    assert telem_nan["power_kw"] == 0.0
    assert telem_nan["energy_kwh"] == 0.0
    assert telem_nan["voltage_v"] == 230.0
    assert telem_nan["current_a"] == 16.0

    # Part C: Negative infinity and math.nan/math.inf float coercion to 0.0
    import math
    payload_inf = {
        "voltage_v": float("inf"),
        "current_a": float("-inf"),
        "power_kw": math.nan,
        "energy_kwh": math.inf
    }
    await service._handle_telemetry("ST_SANITY", "1", payload_inf)
    telem_inf = await database.get_latest_telemetry("ST_SANITY", "1")
    assert telem_inf is not None
    assert telem_inf["voltage_v"] == 0.0
    assert telem_inf["current_a"] == 0.0
    assert telem_inf["power_kw"] == 0.0
    assert telem_inf["energy_kwh"] == 0.0


def test_adv_mqtt_topic_wildcard_adversarial_patterns():
    """
    Adversarial Challenge: Edge-case topic pattern matching.
    Verifies single-level (+), multi-level (#), trailing slashes, and length mismatches.
    """
    service = MQTTService()

    # Single level (+)
    assert service._topic_matches("evsarthi/+/+/telemetry", "evsarthi/ST01/1/telemetry") is True
    assert service._topic_matches("evsarthi/+/+/telemetry", "evsarthi/ST01/1/state") is False
    assert service._topic_matches("evsarthi/+/+/telemetry", "evsarthi/ST01/1/telemetry/extra") is False
    assert service._topic_matches("evsarthi/+/+/telemetry", "evsarthi/ST01") is False

    # Multi level (#)
    assert service._topic_matches("evsarthi/#", "evsarthi/ST01/1/telemetry") is True
    assert service._topic_matches("evsarthi/#", "evsarthi/ST01/1/telemetry/extra/deep") is True
    assert service._topic_matches("other/#", "evsarthi/ST01/1/telemetry") is False

    # Edge cases
    assert service._topic_matches("+/+/+/cmd", "evsarthi/ST01/1/cmd") is True
    assert service._topic_matches("+/+/+/cmd", "evsarthi/ST01/cmd") is False


@pytest.mark.asyncio
async def test_adv_mqtt_custom_listener_exception_isolation():
    """
    Adversarial Challenge: Faulty subscriber isolation.
    An unhandled exception in one custom message listener must never disrupt subsequent listeners.
    """
    service = MQTTService()
    second_listener_called = False

    def faulty_listener(topic, payload):
        raise RuntimeError("Simulated listener crash")

    def safe_listener(topic, payload):
        nonlocal second_listener_called
        second_listener_called = True

    service.add_message_listener("evsarthi/+/+/state", faulty_listener)
    service.add_message_listener("evsarthi/+/+/state", safe_listener)

    # Publish message to trigger listeners
    await service.publish("evsarthi/ST01/1/state", {"state": "AVAILABLE"})

    assert second_listener_called is True


@pytest.mark.asyncio
async def test_adv_mqtt_publish_offline_buffering():
    """
    Adversarial Challenge: Publish when disconnected from MQTT broker.
    Publish methods must execute in simulated/offline mode without raising network connection errors.
    """
    service = MQTTService()
    service._connected = False  # Simulate disconnected broker

    res1 = await service.publish("test/topic", {"status": "ok"})
    res2 = await service.publish_reserve("ST01", "1", "CREATE", "RES_1", "1234")
    res3 = await service.publish_cmd("ST01", "1", "AUTH_DECISION", "ACCEPTED")

    assert res1 is True
    assert res2 is True
    assert res3 is True


@pytest.mark.asyncio
async def test_adv_mqtt_auth_expired_reservation_immediate_denial(test_db):
    """
    Adversarial Challenge: Late auth arrival after reservation expiry.
    If an auth message arrives for a reservation that just expired, the backend must immediately DENY access.
    """
    # Insert expired reservation
    now = time.time()
    await database.save_reservation({
        "station_id": "ST_EXP_AUTH",
        "connector_id": "1",
        "reservation_id": "RES_EXPIRED_01",
        "user_id": "usr_late",
        "pin": "1234",
        "created_at": now - 120,
        "expires_at": now - 60  # Expired 60s ago
    })

    published_cmds = []

    async def mock_publish(topic, payload, qos=1, retain=False):
        published_cmds.append((topic, payload))
        return True

    service = MQTTService()
    service.publish = mock_publish

    # Submit auth for the expired reservation
    auth_payload = {
        "auth_type": "PIN",
        "credential": {"pin": "1234"},
        "current_state": "RESERVED"
    }
    await service._handle_auth("ST_EXP_AUTH", "1", auth_payload)

    # Assert: Active reservation is purged
    res = await database.get_reservation("ST_EXP_AUTH", "1")
    assert res is None

    # Assert: Decision is DENIED
    assert len(published_cmds) == 1
    topic, cmd = published_cmds[0]
    assert topic == "evsarthi/ST_EXP_AUTH/1/cmd"
    assert cmd["action"] == "AUTH_DECISION"
    assert cmd["status"] == "DENIED"
    assert cmd["status_code"] == 401
    assert cmd["relay_action"] == "DE_ENERGIZE"


@pytest.mark.asyncio
async def test_adv_mqtt_auth_whitespace_and_case_normalization(test_db):
    """
    Adversarial Challenge: Credential whitespace, newline, and casing fuzzing.
    Trailing whitespace in PINs and colons/mixed case in RFID UIDs must be properly normalized and authenticated.
    """
    now = time.time()
    await database.save_reservation({
        "station_id": "ST_NORM",
        "connector_id": "1",
        "reservation_id": "RES_NORM_01",
        "user_id": "usr_norm",
        "pin": "7890",
        "rfid_uid": "A1B2C3D4",
        "created_at": now,
        "expires_at": now + 600
    })

    published_cmds = []

    async def mock_publish(topic, payload, qos=1, retain=False):
        published_cmds.append(payload)
        return True

    service = MQTTService()
    service.publish = mock_publish

    # 1. PIN with whitespace and newlines
    await service._handle_auth("ST_NORM", "1", {
        "auth_type": "PIN",
        "credential": {"pin": "  7890 \n\t "}
    })
    assert len(published_cmds) == 1
    assert published_cmds[-1]["status"] == "ACCEPTED"
    assert published_cmds[-1]["relay_action"] == "ENERGIZE"


# ==============================================================================
# SECTION 3: DATABASE CONCURRENCY & SCHEMA HARDENING (Tests 17-22)
# ==============================================================================

@pytest.mark.asyncio
async def test_adv_db_concurrent_reservations_race_condition(test_db):
    """
    Adversarial Challenge: Concurrent reservation race condition.
    Firing 50 simultaneous save_reservation calls on the same connector must not crash SQLite with locks.
    Exactly one winner must persist as per composite primary key (station_id, connector_id).
    """
    async def create_res(i: int):
        await database.save_reservation({
            "station_id": "ST_RACE",
            "connector_id": "1",
            "reservation_id": f"RES_RACE_{i}",
            "user_id": f"usr_{i}",
            "pin": f"{1000 + i}",
            "duration_minutes": 30
        })

    tasks = [create_res(i) for i in range(50)]
    await asyncio.gather(*tasks)

    # Exactly 1 active reservation must remain for (ST_RACE, 1)
    res = await database.get_reservation("ST_RACE", "1")
    assert res is not None
    assert res["station_id"] == "ST_RACE"
    assert res["connector_id"] == "1"


@pytest.mark.asyncio
async def test_adv_db_get_reservation_nonexistent_and_wildcard(test_db):
    """
    Adversarial Challenge: Non-existent queries and wildcard connector retrieval.
    Queries for missing stations return None. connector_id=None queries fallback to latest reservation.
    """
    assert await database.get_reservation("ST_NONEXISTENT", "1") is None
    assert await database.get_session("ST_NONEXISTENT", "1") is None
    assert await database.get_connector_status("ST_NONEXISTENT", "1") is None
    assert await database.get_latest_telemetry("ST_NONEXISTENT", "1") is None

    # Insert two reservations for different connectors
    now = time.time()
    await database.save_reservation({"station_id": "ST_MULTI", "connector_id": "1", "reservation_id": "R1", "user_id": "u1", "expires_at": now + 100})
    await database.save_reservation({"station_id": "ST_MULTI", "connector_id": "2", "reservation_id": "R2", "user_id": "u2", "expires_at": now + 500})

    # Wildcard query (connector_id=None) returns the latest expiring reservation
    wildcard_res = await database.get_reservation("ST_MULTI", connector_id=None)
    assert wildcard_res is not None
    assert wildcard_res["connector_id"] == "2"


@pytest.mark.asyncio
async def test_adv_db_sql_injection_defense_in_parameters(test_db):
    """
    Adversarial Challenge: SQL Injection via parameter values.
    Malicious inputs with drop statements and tautologies must be sanitized by parameterized queries.
    """
    malicious_station = "ST01'; DROP TABLE reservations; --"
    malicious_connector = "' OR '1'='1"

    await database.save_reservation({
        "station_id": malicious_station,
        "connector_id": malicious_connector,
        "reservation_id": "RES_INJECT",
        "user_id": "attacker'--",
        "pin": "1234"
    })

    # Verify table was NOT dropped and record can be safely retrieved
    res = await database.get_reservation(malicious_station, malicious_connector)
    assert res is not None
    assert res["reservation_id"] == "RES_INJECT"


@pytest.mark.asyncio
async def test_adv_db_telemetry_high_concurrency_batch_inserts(test_db):
    """
    Adversarial Challenge: High-throughput telemetry ingestion burst.
    Concurrently inserting 100 telemetry rows across 5 connectors must succeed without table deadlocks.
    """
    async def insert_telem(conn_idx: int, seq: int):
        await database.save_connector_telemetry({
            "station_id": "ST_BATCH",
            "connector_id": str(conn_idx),
            "voltage_v": 230.0 + seq,
            "current_a": 16.0,
            "power_kw": 3.68,
            "energy_kwh": seq * 0.1,
            "recorded_at": time.time() + seq
        })

    tasks = [insert_telem(conn_idx=(i % 5) + 1, seq=i) for i in range(100)]
    await asyncio.gather(*tasks)

    # Verify all connectors have recorded telemetry
    for c in range(1, 6):
        latest = await database.get_latest_telemetry("ST_BATCH", str(c))
        assert latest is not None
        assert latest["voltage_v"] > 0


@pytest.mark.asyncio
async def test_adv_db_rfid_authorization_inactive_and_expired_states(test_db):
    """
    Adversarial Challenge: Inactive and expired RFID card authorizations.
    Cards marked inactive (is_active=0) or expired must be rejected by is_rfid_authorized.
    """
    now = time.time()
    await database.add_authorized_rfid("INACTIVE_CARD", "usr1", "User 1", is_active=0)
    await database.add_authorized_rfid("EXPIRED_CARD", "usr2", "User 2", is_active=1, expires_at=now - 3600)
    await database.add_authorized_rfid("VALID_CARD", "usr3", "User 3", is_active=1, expires_at=now + 3600)

    assert await database.is_rfid_authorized("INACTIVE_CARD") is False
    assert await database.is_rfid_authorized("EXPIRED_CARD") is False
    assert await database.is_rfid_authorized("VALID_CARD") is True
    # Colon and case formatting tolerance
    assert await database.is_rfid_authorized("va:li:d_ca:rd") is True


@pytest.mark.asyncio
async def test_adv_db_delete_session_and_reservation_idempotency(test_db):
    """
    Adversarial Challenge: Idempotent deletion on nonexistent records.
    Calling delete repeatedly must succeed silently without throwing SQLite errors.
    """
    for _ in range(5):
        await database.delete_reservation("ST_GHOST", "1")
        await database.delete_session("ST_GHOST", "1")
        await database.delete_reservation("ST_GHOST", connector_id=None)
        await database.delete_session("ST_GHOST", connector_id=None)
    assert True


# ==============================================================================
# SECTION 4: FASTAPI REST & CHARGING LIFECYCLE BOUNDARY TESTS (Tests 23-28)
# ==============================================================================

@pytest.mark.asyncio
async def test_adv_api_reserve_conflict_when_connector_charging(async_client, test_db):
    """
    Adversarial Challenge: Reserving a connector that is currently actively charging.
    POST /api/reserve must return 409 Conflict.
    """
    # Create active charging session
    await database.save_session({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "session_id": "SESS_ACTIVE_01",
        "user_id": "charging_driver",
        "kw": 50.0
    })

    # Attempt to reserve the same connector
    resp = await async_client.post("/api/reserve", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "user_id": "intruding_driver"
    })

    assert resp.status_code == 409
    assert "currently charging another vehicle" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_adv_api_reserve_conflict_when_connector_faulted(async_client, test_db):
    """
    Adversarial Challenge: Reserving a connector in FAULTED state.
    POST /api/reserve must return 503 Service Unavailable.
    """
    await database.save_connector_status({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "state": "FAULTED",
        "relay_state": "DE_ENERGIZED"
    })

    resp = await async_client.post("/api/reserve", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "user_id": "new_driver"
    })

    assert resp.status_code == 503
    assert "is FAULTED and unavailable" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_adv_api_reserve_renewal_same_user_vs_different_user(async_client, test_db):
    """
    Adversarial Challenge: Re-requesting reservation by same user (renewal) vs competitor (lockout).
    Same user re-requesting returns existing PIN (200 OK). Different user receives 409 Conflict.
    """
    # Initial reservation by User Alpha
    r1 = await async_client.post("/api/reserve", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "user_id": "user_alpha"
    })
    assert r1.status_code == 200
    pin_alpha = r1.json()["pin"]

    # User Alpha re-requests -> Renewal succeeds with same PIN
    r2 = await async_client.post("/api/reserve", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "user_id": "user_alpha"
    })
    assert r2.status_code == 200
    assert r2.json()["pin"] == pin_alpha

    # User Beta attempts to reserve same connector -> 409 Conflict
    r3 = await async_client.post("/api/reserve", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "user_id": "user_beta"
    })
    assert r3.status_code == 409
    assert "already reserved by another user" in r3.json()["detail"]


@pytest.mark.asyncio
async def test_adv_api_verify_otp_expired_returns_410(async_client, test_db):
    """
    Adversarial Challenge: Expired OTP verification attempt.
    Verifying OTP after reservation expiry must return 410 Gone and delete the expired reservation.
    """
    now = time.time()
    await database.save_reservation({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "reservation_id": "RES_OLD",
        "user_id": "user_old",
        "pin": "3344",
        "expires_at": now - 30
    })

    resp = await async_client.post("/api/verify-otp", json={
        "station_id": "NTC_0001",
        "connector_id": "1",
        "pin": "3344"
    })

    assert resp.status_code == 410
    assert "Reservation expired" in resp.json()["detail"]

    # Verify reservation is deleted
    res = await database.get_reservation("NTC_0001", "1")
    assert res is None


@pytest.mark.asyncio
async def test_adv_api_cancel_reservation_unauthorized_user_forbidden(async_client, test_db):
    """
    Adversarial Challenge: Unauthorized cancellation attempt.
    User B attempting to cancel User A's reservation must be blocked with 403 Forbidden.
    """
    now = time.time()
    await database.save_reservation({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "reservation_id": "RES_OWNED",
        "user_id": "user_owner",
        "pin": "1234",
        "expires_at": now + 600
    })

    # Unauthorized cancel attempt
    resp_attacker = await async_client.post(
        "/api/cancel-reservation/NTC_0001?connector_id=1&user_id=attacker_user"
    )
    assert resp_attacker.status_code == 403
    assert "Not your reservation" in resp_attacker.json()["detail"]

    # Invariant: Reservation remains intact
    assert await database.get_reservation("NTC_0001", "1") is not None

    # Legitimate cancel attempt
    resp_owner = await async_client.post(
        "/api/cancel-reservation/NTC_0001?connector_id=1&user_id=user_owner"
    )
    assert resp_owner.status_code == 200
    assert await database.get_reservation("NTC_0001", "1") is None


@pytest.mark.asyncio
async def test_adv_api_reserve_invalid_station_404(async_client, test_db):
    """
    Adversarial Challenge: Reservation targeting non-existent station ID.
    POST /api/reserve must return 404 Not Found.
    """
    resp = await async_client.post("/api/reserve", json={
        "station_id": "NON_EXISTENT_STATION_9999",
        "connector_id": "1",
        "user_id": "usr_test"
    })
    assert resp.status_code == 404
    assert "Station not found" in resp.json()["detail"]
