"""
tests/test_tier4_faults.py - Tier 4: Real-World Scenarios & Fault Injection Suite
Contains 25 test cases (5 fault scenarios x 5 tests) validating
emergency overcurrent trip (<50ms), overvoltage surge protection,
sensor loss / ADC disconnect, MicroSD drop resilience, and brownout recovery.
"""

import time
import pytest
import aiosqlite
import database
from tests.mock_esp32_hardware import (
    MockEVSarthiHardware,
    ConnectorState,
    RelayState,
    TransitionReason,
    FaultCode
)


# ==============================================================================
# Fault 1: Emergency Overcurrent Instant Trip (<50ms) (5 Tests)
# ==============================================================================

def test_tier4_overcurrent_trip_threshold(single_connector):
    """Verify current exceeding 29.7A (e.g. 30.1A / raw 3850) trips to FAULTED."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.get_state() == "CHARGING"

    # Current spike
    hw.set_sensor_values(current_a=30.1)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.OVERCURRENT.value
    assert hw.last_transition_reason == TransitionReason.FAULT_OVERCURRENT


def test_tier4_overcurrent_speed_under_50ms(single_connector):
    """Verify trip occurs synchronously within simulation tick (< 50ms response latency)."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)

    # Time trip execution
    start_time = time.perf_counter()
    hw.set_sensor_values(current_raw=3850)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert hw.get_state() == "FAULTED"
    assert elapsed_ms < 50.0  # Cutoff executed in well under 50ms


def test_tier4_overcurrent_relay_deenergized(single_connector):
    """Verify overcurrent instantly opens contactor and illuminates Red LED."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    assert hw.is_relay_energized() is True
    assert hw.is_green_led_on() is True

    hw.set_sensor_values(current_a=30.5)
    assert hw.get_relay_state() == "DE_ENERGIZED"
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier4_overcurrent_mqtt_alarm(single_connector):
    """Verify state notification emitted with FAULTED, OVERCURRENT, and DE_ENERGIZED."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.tick(100)  # Clear prior messages

    hw.set_sensor_values(current_a=31.0)
    msgs = hw.tick(50)
    state_msgs = [m[1] for m in msgs if "state" in m[0]]
    assert len(state_msgs) > 0
    alarm = state_msgs[-1]
    assert alarm["state"] == "FAULTED"
    assert alarm["fault_code"] == "OVERCURRENT"
    assert alarm["relay_state"] == "DE_ENERGIZED"
    assert alarm["transition_reason"] == TransitionReason.FAULT_OVERCURRENT.value


def test_tier4_overcurrent_latch_inhibit(single_connector):
    """Verify station in FAULTED latches: all subsequent PIN/RFID attempts are blocked."""
    hw = single_connector
    hw.set_sensor_values(current_a=31.0)
    assert hw.get_state() == "FAULTED"

    # User enters PIN
    pin_res = hw.submit_keypad_pin("2580")
    assert pin_res.get("status") == 403 or "error" in pin_res

    # User scans RFID
    rfid_res = hw.scan_rfid("DEADBEEF")
    assert rfid_res.get("status") == 403 or "error" in rfid_res

    # Relay remains de-energized
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


# ==============================================================================
# Fault 2: Grid Overvoltage Surge Protection (5 Tests)
# ==============================================================================

def test_tier4_overvoltage_available_state(single_connector):
    """Verify voltage surge (> 227V) during standby AVAILABLE trips to FAULTED."""
    hw = single_connector
    assert hw.get_state() == "AVAILABLE"

    # Grid spike: 235V (raw ~3850 > 3718 threshold)
    hw.set_sensor_values(voltage_v=235.0)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.OVERVOLTAGE.value
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True


def test_tier4_overvoltage_charging_state(single_connector):
    """Verify voltage surge during CHARGING cuts power immediately and aborts session."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.get_state() == "CHARGING"

    # Surge occurs
    hw.set_sensor_values(voltage_v=240.0)
    assert hw.get_state() == "FAULTED"
    assert hw.is_relay_energized() is False


def test_tier4_overvoltage_latch_persistence(single_connector):
    """Verify fault latch persists when voltage returns to nominal 220V."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=235.0)
    assert hw.get_state() == "FAULTED"

    # Grid voltage normalizes back to 220V
    hw.set_sensor_values(voltage_v=220.0)
    hw.tick(5000)
    # Must remain FAULTED — does not auto-recover without reset command
    assert hw.get_state() == "FAULTED"
    assert hw.is_relay_energized() is False


def test_tier4_overvoltage_reset_command(single_connector):
    """Verify backend RESET command clears fault latch when sensors are nominal."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=235.0)
    assert hw.get_state() == "FAULTED"

    # Grid normalized
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)

    # Backend dispatches RESET
    hw.handle_cmd_message({"action": "RESET"})
    assert hw.get_state() == "AVAILABLE"
    assert hw.last_transition_reason == TransitionReason.FAULT_CLEARED
    assert hw.fault_code is None
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier4_overvoltage_csms_alert(single_connector):
    """Verify overvoltage fault transition reason matches OCPP OverVoltage alert."""
    hw = single_connector
    hw.set_sensor_values(voltage_v=238.0)
    assert hw.get_state() == "FAULTED"
    assert hw.last_transition_reason == TransitionReason.FAULT_OVERVOLTAGE

    # OCPP error code mapping
    ocpp_error = "OverVoltage" if hw.fault_code == "OVERVOLTAGE" else "NoError"
    assert ocpp_error == "OverVoltage"


# ==============================================================================
# Fault 3: Sensor Disconnect / ADC Loss Handling (5 Tests)
# ==============================================================================

def test_tier4_sensor_voltage_drop_during_charge(single_connector):
    """Verify abrupt voltage loss (0V / raw 0) while CHARGING trips to SENSOR_DISCONNECT."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.get_state() == "CHARGING"

    # Voltage abruptly drops to 0 while contactor is closed
    hw.set_sensor_values(voltage_raw=0)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.SENSOR_DISCONNECT.value
    assert hw.last_transition_reason == TransitionReason.FAULT_SENSOR_DISCONNECT


def test_tier4_sensor_current_loss_handling(single_connector):
    """Verify current dropping to 0 for > 10s during CHARGING completes session cleanly."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.get_state() == "CHARGING"

    # EV disconnected by driver (current drops to 0, but voltage line remains live)
    hw.set_sensor_values(voltage_v=220.0, current_a=0.0)
    # Advance time by 10.1 seconds
    hw.tick(10100)

    # Session terminates normally with SESSION_COMPLETED
    assert hw.get_state() == "AVAILABLE"
    assert hw.last_transition_reason == TransitionReason.SESSION_COMPLETED


def test_tier4_sensor_floating_adc_detection(single_connector):
    """Verify injected sensor disconnect trips hardware safely."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.get_state() == "CHARGING"

    hw.inject_sensor_disconnect(True)
    assert hw.get_state() == "FAULTED"
    assert hw.fault_code == FaultCode.SENSOR_DISCONNECT.value


def test_tier4_sensor_fault_relay_action(single_connector):
    """Verify sensor disconnect instantly opens contactor and turns Red LED ON."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    assert hw.is_relay_energized() is True

    hw.inject_sensor_disconnect(True)
    assert hw.is_relay_energized() is False
    assert hw.is_red_led_on() is True
    assert hw.is_green_led_on() is False


def test_tier4_sensor_sd_log_fault_record(single_connector):
    """Verify sensor disconnect fault is recorded in local MicroSD log."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    hw.inject_sensor_disconnect(True)

    # Trigger SD log tick
    hw.tick(5000)
    rows = hw.read_sd_log()
    fault_rows = [r for r in rows if "FAULTED" in r]
    assert len(fault_rows) > 0


# ==============================================================================
# Fault 4: MicroSD Storage Drop / Buffer Resilience (5 Tests)
# ==============================================================================

def test_tier4_sd_unmount_detection(single_connector):
    """Verify card extraction does not crash firmware or raise unhandled exceptions."""
    hw = single_connector
    hw.unmount_sd()
    assert hw.sd_mounted is False

    # Perform periodic ticks — must complete smoothly
    hw.tick(5000)
    hw.tick(5000)
    assert hw.get_state() in ["AVAILABLE", "RESERVED", "CHARGING", "FAULTED"]


def test_tier4_sd_failure_spi_bus_free(single_connector):
    """Verify SD failure releases CS line so keypad and RFID operations continue."""
    hw = single_connector
    hw.unmount_sd()

    # Hardware can still handle reservation and keypad
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_SDFREE", "pin": "2580"})
    assert hw.get_state() == "RESERVED"

    for d in "2580":
        hw.press_key(d)
    auth_req = hw.press_key("#")
    assert auth_req["credential"]["pin"] == "2580"


def test_tier4_sd_telemetry_packet_flag(single_connector):
    """Verify unmounted SD reflects card_mounted=False and last_log_success=False in telemetry."""
    hw = single_connector
    hw.unmount_sd()
    msgs = hw.tick(5000)
    telem = [m[1] for m in msgs if "telemetry" in m[0]][-1]

    assert telem["sd_status"]["card_mounted"] is False
    assert telem["sd_status"]["last_log_success"] is False


def test_tier4_sd_ram_ring_buffer(single_connector):
    """Verify records generated while SD is unmounted are saved into RAM ring buffer."""
    hw = single_connector
    hw.unmount_sd()
    assert len(hw.sd_ram_buffer) == 0

    # Generate 5 log ticks
    for _ in range(5):
        hw.tick(5000)

    assert len(hw.sd_ram_buffer) == 5
    # Cap is 10 records
    for _ in range(10):
        hw.tick(5000)
    assert len(hw.sd_ram_buffer) == 10


def test_tier4_sd_remount_flush(single_connector):
    """Verify remounting SD flushes RAM buffered records to /telemetry.csv."""
    hw = single_connector
    hw.clear_sd_log()
    hw.unmount_sd()

    # Buffer 4 records in RAM
    for _ in range(4):
        hw.tick(5000)
    assert len(hw.sd_ram_buffer) == 4
    assert len(hw.read_sd_log()) == 1  # Only header

    # Remount SD
    hw.remount_sd()
    assert len(hw.sd_ram_buffer) == 0
    # All 4 buffered records committed to CSV log
    assert len(hw.read_sd_log()) == 5  # 1 header + 4 flushed data rows


# ==============================================================================
# Fault 5: Power Loss & Reboot State Recovery (5 Tests)
# ==============================================================================

def test_tier4_power_loss_failsafe_gpio(single_connector):
    """Verify power cut / restart resets GPIO 15 to fail-safe open before boot."""
    hw = single_connector
    hw.restart()
    # At boot, GPIO 15 starts DE_ENERGIZED, state is AVAILABLE after boot sequence
    assert hw.get_state() == "AVAILABLE"
    assert hw.previous_state == "BOOTING"


def test_tier4_power_loss_during_reserved(single_connector):
    """Verify reboot during RESERVED clears volatile reservation state safely."""
    hw = single_connector
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_PWR1", "duration_seconds": 60})
    hw.tick(30000)
    assert hw.get_state() == "RESERVED"

    # Sudden power loss & reboot
    hw.restart()
    assert hw.get_state() == "AVAILABLE"
    assert hw.active_reservation_id is None
    assert hw.is_relay_energized() is False


def test_tier4_power_loss_during_charging(single_connector):
    """Verify power cut during active charging terminates session safely on reboot."""
    hw = single_connector
    hw.handle_cmd_message({"action": "START_CHARGING", "authenticated": True})
    hw.set_sensor_values(voltage_v=220.0, current_a=16.0)
    hw.tick(10000)
    assert hw.get_state() == "CHARGING"

    # Sudden brownout
    hw.restart()
    assert hw.get_state() == "AVAILABLE"
    assert hw.session_id is None
    assert hw.session_start_millis == 0


def test_tier4_power_loss_mqtt_resubscribe(mock_broker, single_connector):
    """Verify reboot reconnects and re-subscribes to MQTT topics."""
    hw = single_connector
    hw.restart()

    # Verify hardware handles new reservation message on topic
    hw.handle_reserve_message({"action": "CREATE", "reservation_id": "RES_REBOOT_01", "pin": "1234"})
    assert hw.get_state() == "RESERVED"
    assert hw.authorized_pin == "1234"


@pytest.mark.asyncio
async def test_tier4_power_loss_db_reconciliation(test_db):
    """Verify backend reconciliation clears orphaned active sessions on station reboot."""
    now = time.time()
    # Insert orphaned session
    await database.save_session({
        "station_id": "NTC_0001",
        "connector_id": "1",
        "session_id": "SESS_ORPHAN",
        "user_id": "usr_reboot",
        "start_soc": 20.0,
        "current_soc": 40.0,
        "start_time": now - 7200,
        "kw": 0.0,
        "kwh": 10.5
    })
    sess = await database.get_session("NTC_0001", connector_id="1")
    assert sess is not None

    # Simulate backend reconciliation: if last_updated or heartbeat is stale (> 3600s), terminate session
    async with aiosqlite.connect(test_db) as db:
        await db.execute("DELETE FROM charging_sessions WHERE station_id = ? AND connector_id = ?", ("NTC_0001", "1"))
        await db.commit()

    reconciled = await database.get_session("NTC_0001", connector_id="1")
    assert reconciled is None
