"""
tests/test_m1_challenger_empirical.py

Empirical Challenger Test Suite for Milestone 1: Hardware & Firmware Integration.
Authored by Challenger 1 (critic, specialist).

Verifies the 5 focus areas from Dispatch:
1. Empirically verify 4 states (AVAILABLE, RESERVED, CHARGING, FAULTED) and transitions.
2. Stress-test the 60s reservation countdown timer and timeout auto-revert to AVAILABLE.
3. Stress-test the 8s auth grant window with plug-in current detection (>0.8A -> CHARGING; timeout -> AVAILABLE).
4. Stress-test emergency fast trip (<50ms) on overvoltage (>227V / raw >= 3719) and overcurrent (>29.7A / raw >= 3801).
5. Document empirical execution results, tests run, and discrepancies with C++ firmware.
"""

import pytest
import time
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason,
    FaultCode
)


@pytest.fixture
def hw():
    """Fresh initialized virtual hardware instance."""
    return MockEVSarthiHardware(station_id="ST001", connector_id="C001")


# ==============================================================================
# FOCUS 1: 4 STATES AND TRANSITIONS VERIFICATION
# ==============================================================================

def test_fsm_initial_boot_state(hw):
    """Verify boot state is AVAILABLE with contactor fail-safe DE_ENERGIZED."""
    assert hw.get_state() == "AVAILABLE"
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert not hw.is_relay_energized()
    assert hw.is_red_led_on()
    assert not hw.is_green_led_on()


def test_fsm_nominal_transition_cycle(hw):
    """Verify nominal 4-state lifecycle: AVAILABLE -> RESERVED -> CHARGING -> AVAILABLE."""
    # 1. AVAILABLE -> RESERVED
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_EMPIRICAL_01",
        "user_id": "usr_emp_01",
        "pin": "2580",
        "rfid_uid": "DEADBEEF",
        "duration_seconds": 60
    })
    assert hw.get_state() == "RESERVED"
    assert not hw.is_relay_energized(), "Relay must remain DE-ENERGIZED in RESERVED before auth"
    assert hw.is_red_led_on()

    # 2. RESERVED -> Auth Grant Window (relay energizes for plug detection)
    hw.handle_cmd_message({
        "action": "AUTH_DECISION",
        "status": "ACCEPTED",
        "auth_window_duration_ms": 8000
    })
    assert hw.get_state() == "RESERVED"
    assert hw.is_relay_energized(), "Relay must ENERGIZE during 8s auth window"
    assert hw.is_green_led_on()

    # 3. Cable Insertion Detected -> CHARGING
    hw.set_sensor_values(voltage_v=220.0, current_a=15.0)  # > 0.8A
    hw.tick(100)
    assert hw.get_state() == "CHARGING"
    assert hw.is_relay_energized()
    assert hw.is_green_led_on()

    # 4. CHARGING -> AVAILABLE (User / Backend session stop)
    hw.handle_cmd_message({
        "action": "STOP_CHARGING",
        "relay_action": "DE_ENERGIZE"
    })
    assert hw.get_state() == "AVAILABLE"


def test_fsm_emergency_trip_from_all_states(hw):
    """Verify emergency fast trip forces transition to FAULTED and opens relay from any state."""
    # From AVAILABLE
    hw.set_sensor_values(voltage_v=240.0)  # Overvoltage (>227V)
    assert hw.get_state() == "FAULTED"
    assert not hw.is_relay_energized()
    assert hw.fault_code == FaultCode.OVERVOLTAGE

    # Reset
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)
    hw.restart()
    assert hw.get_state() == "AVAILABLE"

    # From RESERVED
    hw.handle_reserve_message({"action": "CREATE", "duration_seconds": 60})
    assert hw.get_state() == "RESERVED"
    hw.set_sensor_values(voltage_v=235.0)  # Overvoltage
    assert hw.get_state() == "FAULTED"
    assert not hw.is_relay_energized()

    # Reset
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)
    hw.restart()
    hw.handle_reserve_message({"action": "CREATE", "duration_seconds": 60})
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"
    assert hw.is_relay_energized()

    # Overcurrent (>29.7A) while CHARGING
    hw.set_sensor_values(current_a=31.5)
    assert hw.get_state() == "FAULTED"
    assert not hw.is_relay_energized(), "Relay MUST de-energize immediately upon fault"
    assert hw.is_red_led_on()
    assert hw.fault_code == FaultCode.OVERCURRENT


# ==============================================================================
# FOCUS 2: 60-SECOND RESERVATION TIMER STRESS TESTING
# ==============================================================================

def test_60s_reservation_timer_exact_expiry(hw):
    """Empirically step 60s timer to verify auto-revert to AVAILABLE at t=60.0s."""
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_TMR_60",
        "duration_seconds": 60
    })
    assert hw.get_state() == "RESERVED"
    assert hw.get_reservation_time_left_sec() == 60

    # Advance 30 seconds (300 ticks of 100ms)
    for _ in range(300):
        hw.tick(100)
    assert hw.get_state() == "RESERVED"
    assert hw.get_reservation_time_left_sec() == 30

    # Advance to 59.9 seconds (299 more ticks -> 100ms remaining)
    for _ in range(299):
        hw.tick(100)
    assert hw.get_state() == "RESERVED"
    # Note: Mock uses ceiling division (+999 // 1000), yielding 1; firmware uses floor division (diff / 1000), yielding 0.
    assert hw.get_reservation_time_left_sec() in (0, 1)

    # Advance last 100ms to hit exactly 60.0s
    hw.tick(100)
    assert hw.get_state() == "AVAILABLE", "Connector must auto-revert to AVAILABLE at 60s timeout"
    assert hw.get_reservation_time_left_sec() == 0


def test_60s_reservation_timer_mid_cancellation(hw):
    """Verify reservation cancellation prior to timeout immediately frees connector."""
    hw.handle_reserve_message({
        "action": "CREATE",
        "reservation_id": "RES_CANCEL_01",
        "duration_seconds": 60
    })
    assert hw.get_state() == "RESERVED"

    # Advance 15 seconds
    hw.tick(15000)
    assert hw.get_state() == "RESERVED"

    # User cancels reservation
    hw.handle_reserve_message({
        "action": "CANCEL",
        "reservation_id": "RES_CANCEL_01"
    })
    assert hw.get_state() == "AVAILABLE"


# ==============================================================================
# FOCUS 3: 8-SECOND AUTH GRANT WINDOW & PLUG-IN CURRENT DETECTION
# ==============================================================================

def test_8s_auth_window_successful_plug_detection(hw):
    """Verify cable insertion (>0.8A) within 8s transitions to CHARGING."""
    hw.handle_reserve_message({"action": "CREATE", "duration_seconds": 60})
    hw.handle_cmd_message({
        "action": "AUTH_DECISION",
        "status": "ACCEPTED",
        "auth_window_duration_ms": 8000
    })
    assert hw.get_state() == "RESERVED"
    assert hw.is_relay_energized()
    assert hw.get_auth_window_left_sec() == 8

    # 4 seconds pass with current below plug threshold (0.5A < 0.8A)
    hw.set_sensor_values(voltage_v=220.0, current_a=0.5)
    for _ in range(40):
        hw.tick(100)
    assert hw.get_state() == "RESERVED", "Must not transition to CHARGING if current < 0.8A"
    assert hw.is_relay_energized()
    assert hw.get_auth_window_left_sec() == 4

    # Current rises to 0.85A (>0.8A)
    hw.set_sensor_values(voltage_v=220.0, current_a=0.85)
    hw.tick(100)
    assert hw.get_state() == "CHARGING", "Must transition to CHARGING when current >= 0.8A"
    assert hw.is_relay_energized()
    assert hw.is_green_led_on()


def test_8s_auth_window_timeout_without_cable(hw):
    """Verify 8s window timeout without current de-energizes relay."""
    hw.handle_reserve_message({"action": "CREATE", "duration_seconds": 60})
    hw.handle_cmd_message({
        "action": "AUTH_DECISION",
        "status": "ACCEPTED",
        "auth_window_duration_ms": 8000
    })
    assert hw.is_relay_energized()

    # Step 7.9 seconds (79 ticks of 100ms) with no current
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)
    for _ in range(79):
        hw.tick(100)
    assert hw.is_relay_energized()

    # Step final 100ms to 8.0s -> TIMEOUT
    hw.tick(100)
    # Relay de-energizes on auth window timeout
    assert not hw.is_relay_energized(), "Relay must de-energize immediately upon window expiry"
    assert hw.is_red_led_on()


# ==============================================================================
# FOCUS 4: EMERGENCY FAST TRIP (<50ms) ON OVERVOLTAGE & OVERCURRENT
# ==============================================================================

def test_emergency_fast_trip_overvoltage_boundary(hw):
    """Test boundary condition for overvoltage trip (>227.0V)."""
    hw.handle_reserve_message({"action": "CREATE"})
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"

    # Nominal 220.0V -> No trip
    hw.set_sensor_values(voltage_v=220.0, current_a=10.0)
    assert hw.get_state() == "CHARGING"

    # 226.9V -> Boundary just below threshold -> No trip
    hw.set_sensor_values(voltage_v=226.9, current_a=10.0)
    assert hw.get_state() == "CHARGING"

    # 227.1V -> Exceeds 227.0V -> FAST TRIP!
    hw.set_sensor_values(voltage_v=227.1, current_a=10.0)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.OVERVOLTAGE
    assert not hw.is_relay_energized()
    assert hw.is_red_led_on()


def test_emergency_fast_trip_overcurrent_boundary(hw):
    """Test boundary condition for overcurrent trip (>29.7A)."""
    hw.handle_reserve_message({"action": "CREATE"})
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"

    # Nominal 20.0A -> No trip
    hw.set_sensor_values(voltage_v=220.0, current_a=20.0)
    assert hw.get_state() == "CHARGING"

    # 29.6A -> Boundary just below threshold -> No trip
    hw.set_sensor_values(voltage_v=220.0, current_a=29.6)
    assert hw.get_state() == "CHARGING"

    # 29.8A -> Exceeds 29.7A -> FAST TRIP!
    hw.set_sensor_values(voltage_v=220.0, current_a=29.8)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.OVERCURRENT
    assert not hw.is_relay_energized()
    assert hw.is_red_led_on()


# ==============================================================================
# FOCUS 5: ADVERSARIAL EDGE CASES & SECURITY LOCKDOWNS
# ==============================================================================

def test_adversarial_reservation_lockout_when_busy_or_faulted(hw):
    """Stress-test: reservations must be rejected when connector is busy or faulted."""
    # 1. Reject reservation when FAULTED
    hw.set_sensor_values(voltage_v=240.0)
    assert hw.get_state() == "FAULTED"
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_HACK"})
    assert hw.get_state() == "FAULTED", "Must reject reservation while FAULTED"

    # 2. Reject reservation when CHARGING
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)
    hw.restart()
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_ACTIVE"})
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_INTERRUPT"})
    assert hw.get_state() == "CHARGING", "Must reject reservation while actively CHARGING"


def test_energy_accumulation_accuracy(hw):
    """Verify energy integration kWh = kW * dt matches physics formula."""
    hw.handle_reserve_message({"action": "CREATE"})
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.get_state() == "CHARGING"

    # Set 220V, 20A -> 4.4 kW
    hw.set_sensor_values(voltage_v=220.0, current_a=20.0)
    assert abs(hw.get_power_kw() - 4.4) < 0.01

    # Run for 1 hour (3600 seconds = 36000 ticks of 100ms)
    hw.tick(3600000)

    # Expected: 4.4 kW * 1 hour = 4.4 kWh
    assert abs(hw.get_energy_kwh() - 4.4) < 0.05
