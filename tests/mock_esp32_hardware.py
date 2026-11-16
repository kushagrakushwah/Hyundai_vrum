"""
tests/mock_esp32_hardware.py - Virtual ESP32 DevKit C v4 Simulator for EV Sarthi.
Provides high-fidelity, deterministic in-memory emulation of ESP32 firmware,
ADC sensors (GPIO 34/35), SPDT relay and status LEDs (GPIO 15), 3x4 Keypad,
MFRC522 RFID reader, MicroSD persistent logging (/telemetry.csv), and MQTT pub/sub contracts.
"""

from enum import Enum
import time
import uuid
import datetime
from typing import Callable, Optional, Dict, Any, List, Tuple


class ConnectorState(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    CHARGING = "CHARGING"
    FAULTED = "FAULTED"


class RelayState(str, Enum):
    ENERGIZED = "ENERGIZED"        # GPIO 15 HIGH (3.3V), Green LED ON (NO), Red LED OFF
    DE_ENERGIZED = "DE_ENERGIZED"  # GPIO 15 LOW (0V), Red LED ON (NC), Green LED OFF


class TransitionReason(str, Enum):
    BOOT_COMPLETE = "BOOT_COMPLETE"
    RESERVATION_RECEIVED = "RESERVATION_RECEIVED"
    RESERVATION_TIMEOUT = "RESERVATION_TIMEOUT"
    RESERVATION_CANCELLED = "RESERVATION_CANCELLED"
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_WINDOW_TIMEOUT = "AUTH_WINDOW_TIMEOUT"
    CURRENT_DETECTED = "CURRENT_DETECTED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    FAULT_OVERVOLTAGE = "FAULT_OVERVOLTAGE"
    FAULT_OVERCURRENT = "FAULT_OVERCURRENT"
    FAULT_SENSOR_DISCONNECT = "FAULT_SENSOR_DISCONNECT"
    FAULT_CLEARED = "FAULT_CLEARED"
    HEARTBEAT = "HEARTBEAT"


class FaultCode(str, Enum):
    OVERVOLTAGE = "OVERVOLTAGE"
    OVERCURRENT = "OVERCURRENT"
    SENSOR_DISCONNECT = "SENSOR_DISCONNECT"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    SD_ERROR = "SD_ERROR"


class MockEVSarthiHardware:
    """High-fidelity virtual ESP32 DevKit C v4 hardware simulator."""

    # Calibration Constants
    ADC_MAX_RAW: int = 4095
    VOLTAGE_MAX_V: float = 250.0
    CURRENT_MAX_A: float = 32.0

    # Safety and Transition Thresholds
    OVERCURRENT_THRESHOLD_A: float = 29.7
    OVERCURRENT_RAW_THRESHOLD: int = 3800
    OVERVOLTAGE_THRESHOLD_V: float = 227.0
    OVERVOLTAGE_RAW_THRESHOLD: int = 3718
    CABLE_DETECT_THRESHOLD_A: float = 0.8
    CABLE_DETECT_RAW_THRESHOLD: int = 100

    # Default Durations (milliseconds)
    RESERVATION_DURATION_MS: int = 60000
    AUTH_WINDOW_DURATION_MS: int = 30000
    TELEMETRY_INTERVAL_STANDBY_MS: int = 5000
    TELEMETRY_INTERVAL_CHARGING_MS: int = 2000
    SD_LOG_INTERVAL_MS: int = 5000

    def __init__(
        self,
        station_id: str = "STATION_01",
        connector_id: Any = 1,
        current_millis: int = 0,
        on_publish: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> None:
        self.station_id: str = str(station_id)
        self.connector_id: str = str(connector_id)
        self.firmware_version: str = "1.0.0"

        # Monotonic time tracking
        self.current_millis: int = current_millis
        self.boot_millis: int = current_millis

        # Initial Boot State (Fail-safe GPIO 15 LOW)
        self.relay_pin: int = 0
        self.relay_state: RelayState = RelayState.DE_ENERGIZED
        self.red_led: bool = True
        self.green_led: bool = False

        # State Machine
        self.state: ConnectorState = ConnectorState.AVAILABLE
        self.previous_state: str = "BOOTING"
        self.last_transition_reason: TransitionReason = TransitionReason.BOOT_COMPLETE
        self.last_state_change_millis: int = current_millis

        # Transition to AVAILABLE (fail-safe contactor isolated / Red LED ON)
        self._set_relay(False)

        # Reservation lifecycle
        self.active_reservation_id: Optional[str] = None
        self.active_user_id: Optional[str] = None
        self.authorized_pin: Optional[str] = None
        self.authorized_uid: Optional[str] = "DEADBEEF"
        self.reservation_expiry_millis: int = 0

        # Charging session
        self.access_granted: bool = False
        self.access_expiry_millis: int = 0
        self.session_id: Optional[str] = None
        self.session_start_millis: int = 0
        self.energy_kwh: float = 0.0
        self.zero_current_start_millis: Optional[int] = None

        # ADC Sensors (GPIO 35 Voltage, GPIO 34 Current)
        self.voltage_raw: int = 3604  # ~220.0 V nominal grid (< 227V overvoltage threshold)
        self.current_raw: int = 0     # 0.0 A idle
        self.voltage_v: float = 220.0
        self.current_a: float = 0.0
        self.power_kw: float = 0.0
        self.sensor_loss_injected: bool = False

        # Keypad & RFID Input Buffers
        self.pin_buffer: str = ""
        self.max_pin_length: int = 4

        # MicroSD In-Memory Logging & RAM Buffer
        self.sd_mounted: bool = True
        self.sd_write_fail: bool = False
        self.sd_log_rows: List[str] = []
        self.sd_ram_buffer: List[str] = []
        self.last_sd_log_millis: int = 0
        self._init_sd_log()

        # Display Semantic States
        self.oled_display: Dict[str, Any] = {
            "title": "EV-SARTHI",
            "station": self.station_id,
            "connector": self.connector_id,
            "status": "AVAILABLE",
            "voltage": "230.0V",
            "current": "0.00A"
        }
        self.tft_screen: Dict[str, Any] = {
            "bg": "GREEN",
            "title": "AVAILABLE",
            "text": "READY FOR CHARGING",
            "pin_masked": ""
        }

        # Fault tracking
        self.fault_code: Optional[str] = None
        self.offline_mode: bool = False

        # Message Outbox & Callback
        self.outbox: List[Tuple[str, Dict[str, Any]]] = []
        self.on_publish: Optional[Callable[[str, Dict[str, Any]], None]] = on_publish
        self.last_telemetry_millis: int = 0

        # Enforce safety check before emitting initial boot state
        self._check_safety_interlocks()
        if self.state == ConnectorState.AVAILABLE:
            self._publish_state(TransitionReason.BOOT_COMPLETE)

    # -------------------------------------------------------------------------
    # Topic & Payload Helpers
    # -------------------------------------------------------------------------
    def topic(self, channel: str) -> str:
        """Construct standard hierarchical topic name."""
        return f"evsarthi/{self.station_id}/{self.connector_id}/{channel}"

    def _publish(self, channel: str, payload: Dict[str, Any]) -> None:
        """Enqueue message in outbox and fire callback if registered."""
        top = self.topic(channel)
        self.outbox.append((top, payload))
        if self.on_publish:
            try:
                self.on_publish(top, payload)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Actuator & Hardware Subsystems
    # -------------------------------------------------------------------------
    def _set_relay(self, energized: bool) -> None:
        """Drive GPIO 15 and update dual status LEDs."""
        if energized:
            self.relay_state = RelayState.ENERGIZED
            self.relay_pin = 1
            self.green_led = True
            self.red_led = False
        else:
            self.relay_state = RelayState.DE_ENERGIZED
            self.relay_pin = 0
            self.green_led = False
            self.red_led = True

    def _transition_to(
        self,
        target_state: ConnectorState,
        reason: TransitionReason,
        fault_code: Optional[str] = None
    ) -> None:
        """Perform verified state machine transition with hardware interlocks."""
        self.previous_state = self.state.value if isinstance(self.state, ConnectorState) else str(self.state)
        self.state = target_state
        self.last_transition_reason = reason
        self.fault_code = fault_code
        self.last_state_change_millis = self.current_millis

        if target_state == ConnectorState.AVAILABLE:
            self._set_relay(False)  # Contactor isolated (fail-safe), Red LED ON, Green LED OFF
            self.active_reservation_id = None
            self.active_user_id = None
            self.authorized_pin = None
            self.reservation_expiry_millis = 0
            self.access_granted = False
            self.access_expiry_millis = 0
            self.session_id = None
            self.session_start_millis = 0
            self.energy_kwh = 0.0
            self.pin_buffer = ""
            self.fault_code = None
            self.zero_current_start_millis = None
            self.oled_display["status"] = "AVAILABLE"
            self.tft_screen = {"bg": "GREEN", "title": "AVAILABLE", "text": "READY", "pin_masked": ""}

        elif target_state == ConnectorState.RESERVED:
            self._set_relay(False)  # Isolated contactor, Red LED ON
            self.oled_display["status"] = "RESERVED"
            self.tft_screen = {"bg": "BLACK", "title": "RESERVED", "text": "ENTER PIN", "pin_masked": ""}

        elif target_state == ConnectorState.CHARGING:
            self._set_relay(True)  # Contactor closed, Green LED ON
            self.access_granted = False  # Handshake finished
            if self.session_start_millis == 0:
                self.session_start_millis = self.current_millis
            self.zero_current_start_millis = None
            self.oled_display["status"] = "CHARGING"
            self.tft_screen = {"bg": "BLUE", "title": "CHARGING", "text": "ACTIVE", "pin_masked": ""}

        elif target_state == ConnectorState.FAULTED:
            self._set_relay(False)  # Safety trip cut, Red LED ON
            self.access_granted = False
            self.oled_display["status"] = "FAULTED"
            self.tft_screen = {"bg": "RED", "title": "FAULTED", "text": f"FAULT: {fault_code}", "pin_masked": ""}

        self._publish_state(reason)

    def _publish_state(self, reason: TransitionReason) -> None:
        """Publish retained state notification conforming to schema."""
        res_left = 0
        if self.state == ConnectorState.RESERVED and self.reservation_expiry_millis > self.current_millis:
            res_left = (self.reservation_expiry_millis - self.current_millis + 999) // 1000

        auth_left = 0
        if self.access_granted and self.access_expiry_millis > self.current_millis:
            auth_left = (self.access_expiry_millis - self.current_millis + 999) // 1000

        payload = {
            "message_id": f"state-{self.station_id}-{self.connector_id}-{uuid.uuid4().hex[:8]}",
            "timestamp": int(time.time()),
            "station_id": self.station_id,
            "connector_id": self.connector_id,
            "state": self.state.value,
            "previous_state": self.previous_state,
            "transition_reason": reason.value,
            "relay_state": self.relay_state.value,
            "active_reservation_id": self.active_reservation_id,
            "reservation_time_left_sec": max(0, res_left),
            "auth_window_left_sec": max(0, auth_left),
            "fault_code": self.fault_code,
            "firmware_version": self.firmware_version,
            "uptime_millis": self.current_millis - self.boot_millis
        }
        self._publish("state", payload)

    def _publish_telemetry(self) -> None:
        """Publish high-frequency electrical telemetry."""
        session_sec = 0
        if self.state == ConnectorState.CHARGING and self.session_start_millis > 0:
            session_sec = (self.current_millis - self.session_start_millis) // 1000

        payload = {
            "message_id": f"telem-{self.station_id}-{self.connector_id}-{uuid.uuid4().hex[:8]}",
            "timestamp": int(time.time()),
            "station_id": self.station_id,
            "connector_id": self.connector_id,
            "state": self.state.value,
            "millis": self.current_millis,
            "voltage_v": round(self.voltage_v, 1),
            "current_a": round(self.current_a, 2),
            "power_kw": round(self.power_kw, 3),
            "energy_kwh": round(self.energy_kwh, 4),
            "voltage_raw": self.voltage_raw,
            "current_raw": self.current_raw,
            "raw": {
                "voltage_raw": self.voltage_raw,
                "current_raw": self.current_raw
            },
            "electrical": {
                "voltage_v": round(self.voltage_v, 1),
                "current_a": round(self.current_a, 2),
                "power_kw": round(self.power_kw, 3),
                "frequency_hz": 50.0
            },
            "energy": {
                "energy_kwh": round(self.energy_kwh, 4),
                "session_seconds": session_sec
            },
            "sd_status": {
                "card_mounted": self.sd_mounted,
                "last_log_success": self.sd_mounted and not self.sd_write_fail
            }
        }
        self._publish("telemetry", payload)

    # -------------------------------------------------------------------------
    # MicroSD File Storage Simulator
    # -------------------------------------------------------------------------
    def _init_sd_log(self) -> None:
        """Initialize /telemetry.csv with exact 11-column header."""
        header = "timestamp_utc,millis,station_id,connector_id,state,voltage_raw,current_raw,voltage_v,current_a,power_kw,energy_kwh\n"
        self.sd_log_rows = [header]

    def _log_to_sd(self) -> bool:
        """Append formatted telemetry row to in-memory SD file or buffer in RAM if unmounted."""
        iso_now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = (
            f"{iso_now},{self.current_millis},{self.station_id},{self.connector_id},"
            f"{self.state.value},{self.voltage_raw},{self.current_raw},"
            f"{self.voltage_v:.1f},{self.current_a:.2f},{self.power_kw:.3f},{self.energy_kwh:.4f}\n"
        )
        if not self.sd_mounted or self.sd_write_fail:
            # Buffer in RAM ring buffer (up to 10 records)
            self.sd_ram_buffer.append(row)
            if len(self.sd_ram_buffer) > 10:
                self.sd_ram_buffer.pop(0)
            return False

        self.sd_log_rows.append(row)
        return True

    def read_sd_log(self) -> List[str]:
        """Read all lines from in-memory /telemetry.csv."""
        return list(self.sd_log_rows)

    def unmount_sd(self) -> None:
        """Simulate physical ejection of MicroSD card."""
        self.sd_mounted = False

    def remount_sd(self) -> None:
        """Simulate re-insertion of MicroSD card and flush buffered records."""
        self.sd_mounted = True
        self.sd_write_fail = False
        # Flush RAM buffer to SD rows
        while self.sd_ram_buffer:
            self.sd_log_rows.append(self.sd_ram_buffer.pop(0))

    def inject_sd_failure(self, fail: bool = True) -> None:
        """Simulate write I/O sector error on MicroSD."""
        self.sd_write_fail = fail

    def clear_sd_log(self) -> None:
        """Reset MicroSD file."""
        self._init_sd_log()
        self.sd_ram_buffer.clear()

    # -------------------------------------------------------------------------
    # Sensor Calibration & ADC Rail Boundaries
    # -------------------------------------------------------------------------
    def set_sensor_values(
        self,
        voltage_v: Optional[float] = None,
        current_a: Optional[float] = None,
        voltage_raw: Optional[int] = None,
        current_raw: Optional[int] = None
    ) -> None:
        """Set analog potentiometer sensor values (either floating units or raw 12-bit ADC)."""
        if voltage_v is not None:
            self.voltage_v = float(voltage_v)
            self.voltage_raw = max(0, min(self.ADC_MAX_RAW, round((self.voltage_v / self.VOLTAGE_MAX_V) * self.ADC_MAX_RAW)))
        elif voltage_raw is not None:
            self.voltage_raw = max(0, min(self.ADC_MAX_RAW, int(voltage_raw)))
            self.voltage_v = round((self.voltage_raw / float(self.ADC_MAX_RAW)) * self.VOLTAGE_MAX_V, 1)

        if current_a is not None:
            self.current_a = float(current_a)
            self.current_raw = max(0, min(self.ADC_MAX_RAW, round((self.current_a / self.CURRENT_MAX_A) * self.ADC_MAX_RAW)))
        elif current_raw is not None:
            self.current_raw = max(0, min(self.ADC_MAX_RAW, int(current_raw)))
            self.current_a = round((self.current_raw / float(self.ADC_MAX_RAW)) * self.CURRENT_MAX_A, 2)

        self.power_kw = round((self.voltage_v * self.current_a) / 1000.0, 3)
        self.oled_display["voltage"] = f"{self.voltage_v:.1f}V"
        self.oled_display["current"] = f"{self.current_a:.2f}A"

        # Check instant trip (<50ms) on sensor change
        self._check_safety_interlocks()

    def _check_safety_interlocks(self) -> None:
        """Instant emergency trip detection."""
        if self.state == ConnectorState.FAULTED:
            return

        if self.current_a > self.OVERCURRENT_THRESHOLD_A or self.current_raw > self.OVERCURRENT_RAW_THRESHOLD:
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_OVERCURRENT, fault_code=FaultCode.OVERCURRENT.value)
        elif self.voltage_v > self.OVERVOLTAGE_THRESHOLD_V or self.voltage_raw > self.OVERVOLTAGE_RAW_THRESHOLD:
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_OVERVOLTAGE, fault_code=FaultCode.OVERVOLTAGE.value)
        elif self.state == ConnectorState.CHARGING and (self.voltage_raw == 0 or self.sensor_loss_injected):
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_SENSOR_DISCONNECT, fault_code=FaultCode.SENSOR_DISCONNECT.value)

    def inject_sensor_disconnect(self, disconnected: bool = True) -> None:
        """Inject complete line / ADC loss."""
        self.sensor_loss_injected = disconnected
        if disconnected and self.state == ConnectorState.CHARGING:
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_SENSOR_DISCONNECT, fault_code=FaultCode.SENSOR_DISCONNECT.value)

    # -------------------------------------------------------------------------
    # User Input Emulation (Keypad & RFID)
    # -------------------------------------------------------------------------
    def press_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Simulate single physical keypress on 3x4 matrix keypad."""
        if self.state == ConnectorState.FAULTED:
            return {"error": "Station is faulted", "state": self.state.value}

        if self.state != ConnectorState.RESERVED:
            return None

        if key in "0123456789":
            if len(self.pin_buffer) < self.max_pin_length:
                self.pin_buffer += key
                self.tft_screen["pin_masked"] = "* " * len(self.pin_buffer)
            return None
        elif key == "*":
            self.pin_buffer = ""
            self.tft_screen["pin_masked"] = ""
            return None
        elif key == "#":
            submitted_pin = self.pin_buffer
            self.pin_buffer = ""
            self.tft_screen["pin_masked"] = ""
            if len(submitted_pin) >= 4:
                return self.submit_keypad_pin(submitted_pin)
            return {"error": "PIN too short", "length": len(submitted_pin)}
        return None

    def submit_keypad_pin(self, pin: str) -> Dict[str, Any]:
        """Submit PIN directly and publish auth request message."""
        if self.state == ConnectorState.FAULTED:
            return {"error": "Station is faulted", "status": 403}

        if self.state != ConnectorState.RESERVED:
            return {"error": "Station is not reserved", "status": 408}

        payload = {
            "message_id": f"auth-{self.station_id}-{self.connector_id}-{uuid.uuid4().hex[:8]}",
            "timestamp": int(time.time()),
            "station_id": self.station_id,
            "connector_id": self.connector_id,
            "auth_type": "PIN",
            "credential": {"pin": str(pin)},
            "current_state": self.state.value
        }
        self._publish("auth", payload)
        return payload

    def scan_rfid(self, rfid_uid: str) -> Dict[str, Any]:
        """Scan RFID card and publish auth request message."""
        clean_uid = rfid_uid.replace(":", "").replace(" ", "").upper()
        if self.state == ConnectorState.FAULTED:
            return {"error": "Station is faulted", "status": 403}

        # Check offline whitelist fallback
        if self.offline_mode and clean_uid == self.authorized_uid and self.state == ConnectorState.RESERVED:
            # Grant access locally
            self.access_granted = True
            self.access_expiry_millis = self.current_millis + self.AUTH_WINDOW_DURATION_MS
            self._set_relay(True)
            self._publish_state(TransitionReason.AUTH_SUCCESS)
            return {"status": "ACCEPTED_OFFLINE", "uid": clean_uid}

        payload = {
            "message_id": f"auth-{self.station_id}-{self.connector_id}-{uuid.uuid4().hex[:8]}",
            "timestamp": int(time.time()),
            "station_id": self.station_id,
            "connector_id": self.connector_id,
            "auth_type": "RFID",
            "credential": {"rfid_uid": clean_uid},
            "current_state": self.state.value
        }
        self._publish("auth", payload)
        return payload

    # -------------------------------------------------------------------------
    # MQTT Inbound Message Dispatchers
    # -------------------------------------------------------------------------
    def handle_reserve_message(self, payload: Dict[str, Any]) -> None:
        """Handle incoming evsarthi/{station}/{connector}/reserve message."""
        action = payload.get("action")
        if action == "CREATE":
            if self.state != ConnectorState.AVAILABLE:
                # Connector busy or faulted - reject
                return

            self.active_reservation_id = payload.get("reservation_id")
            self.active_user_id = payload.get("user_id")
            self.authorized_pin = str(payload.get("pin", "2580"))
            self.authorized_uid = payload.get("rfid_uid", "DEADBEEF")
            duration_sec = payload.get("duration_seconds", 60)
            self.reservation_expiry_millis = self.current_millis + (duration_sec * 1000)
            self.pin_buffer = ""
            self._transition_to(ConnectorState.RESERVED, TransitionReason.RESERVATION_RECEIVED)

        elif action == "CANCEL":
            req_res_id = payload.get("reservation_id")
            if self.state == ConnectorState.RESERVED and (req_res_id is None or req_res_id == self.active_reservation_id):
                self._transition_to(ConnectorState.AVAILABLE, TransitionReason.RESERVATION_CANCELLED)

    def handle_cmd_message(self, payload: Dict[str, Any]) -> None:
        """Handle incoming evsarthi/{station}/{connector}/cmd message."""
        action = payload.get("action")
        status = payload.get("status")
        status_code = payload.get("status_code", 200 if status == "ACCEPTED" else 401)

        if action == "AUTH_DECISION":
            if self.state != ConnectorState.RESERVED:
                # Command arrived when connector is not in RESERVED (e.g. expired or available) - ignore
                return

            if status == "ACCEPTED" or status_code == 200:
                self.access_granted = True
                window_ms = payload.get("auth_window_duration_ms", self.AUTH_WINDOW_DURATION_MS)
                self.access_expiry_millis = self.current_millis + window_ms
                self.session_id = payload.get("session_id", f"SESS_{uuid.uuid4().hex[:8]}")
                self._set_relay(True)  # Energize relay, Green LED ON
                self.tft_screen = {"bg": "GREEN", "title": "ACCESS GRANTED", "text": "PLUG IN CABLE", "pin_masked": ""}
                # If current is ALREADY flowing, advance immediately to CHARGING
                if self.current_a > self.CABLE_DETECT_THRESHOLD_A or self.current_raw > self.CABLE_DETECT_RAW_THRESHOLD:
                    self.session_start_millis = self.current_millis
                    self._transition_to(ConnectorState.CHARGING, TransitionReason.CURRENT_DETECTED)
            else:
                self._set_relay(False)  # Red LED ON
                self.tft_screen = {"bg": "RED", "title": "ACCESS DENIED", "text": "INVALID CREDENTIALS", "pin_masked": ""}
                self.pin_buffer = ""

        elif action == "START_CHARGING":
            # Security Interlock: Prevent unauthenticated START_CHARGING from transitioning to CHARGING.
            is_authenticated = self.access_granted or payload.get("authenticated") is True or payload.get("authorized") is True
            if self.state == ConnectorState.RESERVED and is_authenticated:
                self.session_start_millis = self.current_millis
                self._transition_to(ConnectorState.CHARGING, TransitionReason.CURRENT_DETECTED)
            elif self.state == ConnectorState.AVAILABLE and (payload.get("authenticated") is True or payload.get("authorized") is True):
                self.session_start_millis = self.current_millis
                self._transition_to(ConnectorState.CHARGING, TransitionReason.CURRENT_DETECTED)
            else:
                return

        elif action == "STOP_CHARGING":
            if self.state == ConnectorState.CHARGING:
                self._transition_to(ConnectorState.AVAILABLE, TransitionReason.SESSION_COMPLETED)

        elif action == "RESET":
            if self.state == ConnectorState.FAULTED:
                if self.voltage_v <= self.OVERVOLTAGE_THRESHOLD_V and self.current_a <= self.OVERCURRENT_THRESHOLD_A:
                    self.fault_code = None
                    self._transition_to(ConnectorState.AVAILABLE, TransitionReason.FAULT_CLEARED)

    # -------------------------------------------------------------------------
    # Virtual Monotonic Clock & Periodic Loop
    # -------------------------------------------------------------------------
    def tick(self, delta_ms: int = 100) -> List[Tuple[str, Dict[str, Any]]]:
        """Advance simulated time by delta_ms and process all timers & events."""
        if delta_ms < 0:
            raise ValueError("delta_ms must be non-negative")

        self.current_millis += delta_ms

        # 1. Update Energy Accumulator in CHARGING
        if self.state == ConnectorState.CHARGING:
            delta_hours = delta_ms / 3_600_000.0
            self.energy_kwh += self.power_kw * delta_hours

            # Track current loss during charge
            if self.current_raw == 0 or self.current_a == 0:
                if self.zero_current_start_millis is None:
                    self.zero_current_start_millis = self.current_millis - delta_ms
                if self.current_millis - self.zero_current_start_millis >= 10000:
                    # Current zero for >= 10 seconds -> clean session termination
                    self._transition_to(ConnectorState.AVAILABLE, TransitionReason.SESSION_COMPLETED)
            else:
                self.zero_current_start_millis = None

        # 2. Check Hardware Safety Interlocks
        self._check_safety_interlocks()

        # 3. Check 8-Second Auth Grant Window
        if self.access_granted:
            if self.current_a > self.CABLE_DETECT_THRESHOLD_A or self.current_raw > self.CABLE_DETECT_RAW_THRESHOLD:
                # Cable inserted and current flowing!
                self.access_granted = False
                self.session_start_millis = self.current_millis
                self._transition_to(ConnectorState.CHARGING, TransitionReason.CURRENT_DETECTED)
            elif self.current_millis >= self.access_expiry_millis:
                # Cable was not plugged in within 8 seconds -> revert to AVAILABLE
                self.access_granted = False
                self._transition_to(ConnectorState.AVAILABLE, TransitionReason.AUTH_WINDOW_TIMEOUT)

        # 4. Check 60-Second Reservation Countdown
        if self.state == ConnectorState.RESERVED and not self.access_granted:
            if self.current_millis >= self.reservation_expiry_millis:
                self._transition_to(ConnectorState.AVAILABLE, TransitionReason.RESERVATION_TIMEOUT)

        # 5. Periodic Telemetry Emission
        interval = self.TELEMETRY_INTERVAL_CHARGING_MS if self.state == ConnectorState.CHARGING else self.TELEMETRY_INTERVAL_STANDBY_MS
        if self.current_millis - self.last_telemetry_millis >= interval:
            self.last_telemetry_millis = self.current_millis
            self._publish_telemetry()

        # 6. Periodic MicroSD Logging
        if self.current_millis - self.last_sd_log_millis >= self.SD_LOG_INTERVAL_MS:
            self.last_sd_log_millis = self.current_millis
            self._log_to_sd()

        # Drain outbox
        messages = list(self.outbox)
        self.outbox.clear()
        return messages

    def restart(self) -> None:
        """Simulate power cycle / brownout reboot."""
        self.boot_millis = self.current_millis
        self.active_reservation_id = None
        self.active_user_id = None
        self.authorized_pin = None
        self.reservation_expiry_millis = 0
        self.access_granted = False
        self.access_expiry_millis = 0
        self.session_id = None
        self.session_start_millis = 0
        self.energy_kwh = 0.0
        self.pin_buffer = ""
        self.fault_code = None
        self.zero_current_start_millis = None
        # Fail-safe initial state: Contactor isolated (GPIO 15 LOW, Red LED ON)
        self._set_relay(False)
        self.state = ConnectorState.AVAILABLE
        self.previous_state = "BOOTING"

        # Hardware Safety Interlock: Verify ADC sensor levels before completing boot
        if self.current_a > self.OVERCURRENT_THRESHOLD_A or self.current_raw > self.OVERCURRENT_RAW_THRESHOLD:
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_OVERCURRENT, fault_code=FaultCode.OVERCURRENT.value)
        elif self.voltage_v > self.OVERVOLTAGE_THRESHOLD_V or self.voltage_raw > self.OVERVOLTAGE_RAW_THRESHOLD:
            self._transition_to(ConnectorState.FAULTED, TransitionReason.FAULT_OVERVOLTAGE, fault_code=FaultCode.OVERVOLTAGE.value)
        else:
            self._set_relay(False)
            self._publish_state(TransitionReason.BOOT_COMPLETE)

    # -------------------------------------------------------------------------
    # Ergonomic Test State Accessors
    # -------------------------------------------------------------------------
    def get_state(self) -> str:
        return self.state.value

    def get_relay_state(self) -> str:
        return self.relay_state.value

    def is_relay_energized(self) -> bool:
        return self.relay_state == RelayState.ENERGIZED

    def is_red_led_on(self) -> bool:
        return self.red_led

    def is_green_led_on(self) -> bool:
        return self.green_led

    def get_reservation_time_left_sec(self) -> int:
        if self.state == ConnectorState.RESERVED and self.reservation_expiry_millis > self.current_millis:
            return (self.reservation_expiry_millis - self.current_millis + 999) // 1000
        return 0

    def get_auth_window_left_sec(self) -> int:
        if self.access_granted and self.access_expiry_millis > self.current_millis:
            return (self.access_expiry_millis - self.current_millis + 999) // 1000
        return 0

    def get_energy_kwh(self) -> float:
        return round(self.energy_kwh, 4)

    def get_power_kw(self) -> float:
        return round(self.power_kw, 3)


    @property
    def active_fault(self) -> Optional[FaultCode]:
        """Return active fault code as FaultCode enum (or None if not faulted)."""
        if self.fault_code is None:
            return None
        try:
            return FaultCode(self.fault_code)
        except ValueError:
            return None

    @active_fault.setter
    def active_fault(self, fault: Optional[Any]) -> None:
        if fault is None:
            self.fault_code = None
        elif isinstance(fault, FaultCode):
            self.fault_code = fault.value
        else:
            self.fault_code = str(fault)

    def force_charging(
        self,
        session_id: Optional[str] = None,
        voltage_v: float = 220.0,
        current_a: float = 16.0
    ) -> None:
        """Harness helper: cleanly transitions into CHARGING state for test fixtures."""
        self.session_id = session_id or f"SESS_TEST_{uuid.uuid4().hex[:6]}"
        self.session_start_millis = self.current_millis
        self.access_granted = True
        self._transition_to(ConnectorState.CHARGING, TransitionReason.CURRENT_DETECTED)
        self.set_sensor_values(voltage_v=voltage_v, current_a=current_a)
