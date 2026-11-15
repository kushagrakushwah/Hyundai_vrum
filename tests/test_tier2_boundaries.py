"""
tests/test_tier2_boundaries.py - Tier 2: Boundary & Corner Cases Suite
Contains 30 test cases (6 boundary groups x 5 tests) validating
temporal edges (60s countdown, 8s auth window), keypad buffer overflow/clear,
late PIN handling, and ADC rail limits.
"""

import time
import math
import pytest
import aiosqlite
import database
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason
)


# ==============================================================================
# Boundary 1: Exact 60s Reservation Timeout (5 Tests)
# ==============================================================================

def test_tier2_timeout_at_59s_active(single_connector):
    """Verify at t=59.0s reservation remains active, time_left=1s, relay LOW."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_T1", "duration_seconds": 60})
    hw.tick(59000)
    assert hw.get_state() == "RESERVED"
    assert hw.get_reservation_time_left_sec() == 1
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


def test_tier2_timeout_at_59_9s_edge(single_connector):
    """Verify at t=59.9s (100ms before deadline) state is still RESERVED."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_T2", "duration_seconds": 60})
    hw.tick(59900)
    assert hw.get_state() == "RESERVED"
    assert hw.is_relay_energized() is False


def test_tier2_timeout_at_60_0s_exact(single_connector):
    """Verify at exactly t=60.0s (60,000ms) hardware expires reservation."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_T3", "duration_seconds": 60})
    msgs = hw.tick(60000)
    assert hw.get_state() == "AVAILABLE"
    assert hw.last_transition_reason == TransitionReason.RESERVATION_TIMEOUT
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    assert len(state_msgs) > 0
    assert state_msgs[-1]["state"] == "AVAILABLE"
    assert state_msgs[-1]["transition_reason"] == TransitionReason.RESERVATION_TIMEOUT.value


def test_tier2_timeout_at_60_1s_reverted(single_connector):
    """Verify at t=60.1s state reverts to AVAILABLE with fail-safe DE_ENERGIZED relay (Red LED ON, ready for next reservation)."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_T4", "duration_seconds": 60})
    hw.tick(60100)
    assert hw.get_state() == "AVAILABLE"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


@pytest.mark.asyncio
async def test_tier2_timeout_db_cleanup(test_db):
    """Verify expired reservations in SQLite are cleaned up and logged to bookings_history."""
    now = time.time()
    expired_res = {
        "station_id": "NTC_0001",
        "connector_id": "1",
        "reservation_id": "RES_EXP_01",
        "user_id": "user_exp_01",
        "user_name": "Late Driver",
        "pin": "1111",
        "duration_minutes": 1,
        "created_at": now - 120,
        "expires_at": now - 60,
        "vehicle_soc": 25.0,
        "payment_order_id": "order_mock",
        "payment_status": "PENDING"
    }
    await database.save_reservation(expired_res)

    # Verify present
    active = await database.get_reservation("NTC_0001", connector_id="1")
    assert active is not None
    assert active["reservation_id"] == "RES_EXP_01"

    # Simulate cleanup routine for expired rows
    async with aiosqlite.connect(test_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reservations WHERE expires_at <= ?', (time.time(),)) as cursor:
            expired_rows = [dict(r) for r in await cursor.fetchall()]

    for r in expired_rows:
        conn_id = r.get("connector_id", "1")
        await database.delete_reservation(r["station_id"], conn_id)
        await database.save_booking_history({
            **r,
            "outcome": "EXPIRED_NO_SHOW",
            "total_kwh": 0,
            "amount_charged_rs": 0
        })

    # Assert deleted from reservations and logged to history
    remaining = await database.get_reservation("NTC_0001", connector_id="1")
    assert remaining is None

    history = await database.get_history(50)
    assert len(history) > 0
    assert any(h["reservation_id"] == "RES_EXP_01" and h["outcome"] == "EXPIRED_NO_SHOW" for h in history)


# ==============================================================================
# Boundary 2: Late PIN Entry (5 Tests)
# ==============================================================================

def test_tier2_pin_at_59_5s_success(single_connector):
    """Verify valid PIN submitted just before deadline (59.5s) succeeds."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_LP1", "pin": "2580", "duration_seconds": 60})
    hw.tick(59500)
    assert hw.get_state() == "RESERVED"

    for k in "2580":
        hw.press_key(k)
    auth_req = hw.press_key("#")
    assert auth_req["credential"]["pin"] == "2580"

    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True
    assert hw.is_relay_energized() is True


def test_tier2_pin_at_60_1s_rejected(single_connector):
    """Verify PIN submission after 60s timeout is rejected."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_LP2", "pin": "2580", "duration_seconds": 60})
    hw.tick(60100)
    assert hw.get_state() == "AVAILABLE"

    # Driver attempts late submission
    res = hw.submit_keypad_pin("2580")
    assert res.get("status") == 408 or "error" in res


def test_tier2_pin_buffered_during_timeout(single_connector):
    """Verify partially buffered digits before timeout are wiped on expiry."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_LP3", "pin": "2580", "duration_seconds": 60})
    hw.tick(58000)
    # Enter 3 digits before deadline
    hw.press_key("2")
    hw.press_key("5")
    hw.press_key("8")
    assert hw.pin_buffer == "258"

    # Timeout occurs
    hw.tick(2100)
    assert hw.get_state() == "AVAILABLE"
    # When state transitioned to AVAILABLE, pin_buffer was wiped
    assert hw.pin_buffer == ""

    # Submitting '#' in AVAILABLE returns None (no active reservation)
    res = hw.press_key("#")
    assert res is None


def test_tier2_backend_cmd_race_condition(single_connector):
    """Verify delayed backend /cmd arriving after hardware reverted to AVAILABLE is dropped."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_LP4", "duration_seconds": 60})
    hw.tick(60100)
    assert hw.get_state() == "AVAILABLE"

    # Delayed backend command arrives
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    # Must remain AVAILABLE, not enter auth window for an expired session
    assert hw.get_state() == "AVAILABLE"
    assert hw.access_granted is False


@pytest.mark.asyncio
async def test_tier2_expired_pin_reuse_prevention(test_db):
    """Verify expired PIN cannot be re-used after cancellation/expiry."""
    now = time.time()
    await database.save_reservation({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "reservation_id": "RES_EXP_REUSE",
        "user_id": "u1",
        "user_name": "U1",
        "pin": "7777",
        "duration_minutes": 1,
        "created_at": now - 100,
        "expires_at": now - 10,
        "vehicle_soc": 20.0
    })
    # Query with active timestamp
    res = await database.get_reservation("NTC_0001", connector_id="1")
    # Verify expired check
    is_active = res and res["expires_at"] > time.time()
    assert is_active is False


# ==============================================================================
# Boundary 3: 8-Second Auth Window Timeout (5 Tests)
# ==============================================================================

def test_tier2_auth_window_current_at_7_0s(single_connector):
    """Verify vehicle plug-in and current detection at t=7.0s locks into CHARGING."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_AW1"})
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.access_granted is True

    # Advance 7 seconds
    hw.tick(7000)
    assert hw.access_granted is True

    # Plug in cable and current starts flowing
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    hw.tick(100)
    assert hw.get_state() == "CHARGING"
    assert hw.access_granted is False
    assert hw.is_relay_energized() is True


def test_tier2_auth_window_current_at_7_9s(single_connector):
    """Verify vehicle plug-in at t=7.9s (100ms before 8s window ends) succeeds."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_AW2"})
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})

    # Advance 7.9 seconds
    hw.tick(7900)
    assert hw.access_granted is True

    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    hw.tick(50)
    assert hw.get_state() == "CHARGING"


def test_tier2_auth_window_timeout_at_8_1s(single_connector):
    """Verify at t=8.1s without current, window expires, relay safely de-energizes, and state reverts to AVAILABLE."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_AW3"})
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})
    assert hw.is_relay_energized() is True

    # Advance past 8,000ms with no current
    hw.tick(8100)
    assert hw.access_granted is False
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.get_state() == "AVAILABLE"


def test_tier2_auth_window_sub_threshold_current(single_connector):
    """Verify current below 0.8A threshold (e.g. 0.39A) is ignored, window expires, and reverts to AVAILABLE."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_AW4"})
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})

    # Set sub-threshold current
    hw.set_sensor_values(voltage_v=220.0, current_a=0.39)
    assert hw.current_a < 0.8

    # Tick across 8,000 ms
    hw.tick(8100)
    assert hw.access_granted is False
    assert hw.get_state() == "AVAILABLE"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


def test_tier2_auth_window_zero_current_fallback(single_connector):
    """Verify zero current fallback emits AUTH_WINDOW_TIMEOUT notification and reverts to AVAILABLE."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_AW5"})
    hw.handle_cmd_message({"action": "AUTH_DECISION", "status": "ACCEPTED", "status_code": 200})

    msgs = hw.tick(8001)
    timeout_msgs = [m[1] for m in msgs if m[1].get("transition_reason") == TransitionReason.AUTH_WINDOW_TIMEOUT.value]
    assert len(timeout_msgs) > 0
    assert hw.get_state() == "AVAILABLE"
    assert timeout_msgs[-1]["state"] == "AVAILABLE"


# ==============================================================================
# Boundary 4: Keypad Buffer Overflow Handling (5 Tests)
# ==============================================================================

def test_tier2_keypad_exact_6digits(single_connector):
    """Verify keypad accepts up to exact maximum length of 6 digits."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KB1"})
    for d in "123456":
        hw.press_key(d)
    assert len(hw.pin_buffer) == 6
    assert hw.pin_buffer == "123456"


def test_tier2_keypad_7th_digit_dropped(single_connector):
    """Verify 7th digit is silently dropped and buffer remains capped at 6."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KB2"})
    for d in "1234567":
        hw.press_key(d)
    assert len(hw.pin_buffer) == 6
    assert hw.pin_buffer == "123456"


def test_tier2_keypad_12digits_stress(single_connector):
    """Verify entering 12 rapid keystrokes maintains buffer cap and stability."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KB3"})
    for d in "123456789012":
        hw.press_key(d)
    assert len(hw.pin_buffer) == 6
    assert hw.pin_buffer == "123456"


def test_tier2_keypad_non_numeric_ignored(single_connector):
    """Verify non-matrix characters are rejected and leave buffer unchanged."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KB4"})
    hw.press_key("1")
    hw.press_key("A")
    hw.press_key("B")
    hw.press_key("!")
    hw.press_key("2")
    assert hw.pin_buffer == "12"


def test_tier2_keypad_short_pin_submission(single_connector):
    """Verify submission of PIN shorter than 4 digits on '#' returns error."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_KB5"})
    hw.press_key("1")
    hw.press_key("2")
    res = hw.press_key("#")
    assert res is not None
    assert "error" in res
    assert res["error"] == "PIN too short"
    assert res["length"] == 2


# ==============================================================================
# Boundary 5: Keypad '*' Clear Functionality (5 Tests)
# ==============================================================================

def test_tier2_keypad_clear_empty_buffer(single_connector):
    """Verify pressing '*' on empty buffer does not crash or underflow."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CLR1"})
    assert hw.pin_buffer == ""
    hw.press_key("*")
    assert hw.pin_buffer == ""


def test_tier2_keypad_clear_partial_buffer(single_connector):
    """Verify pressing '*' clears partially filled buffer."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CLR2"})
    hw.press_key("2")
    hw.press_key("5")
    assert hw.pin_buffer == "25"
    hw.press_key("*")
    assert hw.pin_buffer == ""


def test_tier2_keypad_clear_full_buffer(single_connector):
    """Verify pressing '*' clears full 6-digit buffer."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CLR3"})
    for d in "123456":
        hw.press_key(d)
    assert len(hw.pin_buffer) == 6
    hw.press_key("*")
    assert hw.pin_buffer == ""


def test_tier2_keypad_consecutive_clears(single_connector):
    """Verify pressing '*' repeatedly does not affect responsiveness."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CLR4"})
    for _ in range(5):
        hw.press_key("*")
    assert hw.pin_buffer == ""


def test_tier2_keypad_entry_after_clear(single_connector):
    """Verify user can correct mistake using '*' and submit valid PIN."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_CLR5"})
    # Mistake
    hw.press_key("9")
    hw.press_key("9")
    # Clear
    hw.press_key("*")
    assert hw.pin_buffer == ""
    # Correct entry
    for d in "2580":
        hw.press_key(d)
    auth_req = hw.press_key("#")
    assert auth_req["credential"]["pin"] == "2580"


# ==============================================================================
# Boundary 6: ADC Rail Limits (5 Tests)
# ==============================================================================

def test_tier2_adc_voltage_min_rail(single_connector):
    """Verify raw 0 on voltage ADC produces 0.0V and no division errors."""
    hw = single_connector
    hw.set_sensor_values(voltage_raw=0)
    assert hw.voltage_raw == 0
    assert hw.voltage_v == 0.0
    assert hw.power_kw == 0.0


def test_tier2_adc_voltage_max_rail(single_connector):
    """Verify raw 4095 on voltage ADC produces 250.0V and triggers overvoltage trip."""
    hw = single_connector
    hw.set_sensor_values(voltage_raw=4095)
    assert hw.voltage_raw == 4095
    assert hw.voltage_v == 250.0
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == "OVERVOLTAGE"


def test_tier2_adc_current_min_rail(single_connector):
    """Verify raw 0 on current ADC produces 0.00A and zero power."""
    hw = single_connector
    hw.set_sensor_values(current_raw=0)
    assert hw.current_raw == 0
    assert hw.current_a == 0.0
    assert hw.power_kw == 0.0


def test_tier2_adc_current_max_rail(single_connector):
    """Verify raw 4095 on current ADC produces 32.00A and triggers overcurrent trip."""
    hw = single_connector
    hw.set_sensor_values(current_raw=4095)
    assert hw.current_raw == 4095
    assert hw.current_a == 32.0
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == "OVERCURRENT"


def test_tier2_adc_boundary_precision(single_connector):
    """Verify boundary precision at raw = 1 and raw = 4094 produces valid finite floats."""
    hw = single_connector
    hw.set_sensor_values(voltage_raw=1, current_raw=1)
    assert hw.voltage_v > 0.0
    assert hw.current_a > 0.0
    assert not math.isnan(hw.voltage_v)
    assert not math.isinf(hw.current_a)

    hw.set_sensor_values(voltage_raw=4094, current_raw=4094)
    assert not math.isnan(hw.voltage_v)
    assert not math.isinf(hw.current_a)
