"""
smartreserve_backend/ocpp_bridge.py
===================================
Production-grade SteVe OCPP 1.6-J CSMS & LF Energy EVerest Bridge for EV Sarthi.

Implements:
1. Strict OCPP 1.6-J JSON-RPC 2.0 Framing:
   - Call [2, uniqueId, action, payload]
   - CallResult [3, uniqueId, payload]
   - CallError [4, uniqueId, errorCode, errorDescription, errorDetails]
   - Standard OCPP 1.6-J CallError codes
2. Core OCPP 1.6 Actions:
   - BootNotification, Heartbeat, StatusNotification, ReserveNow, CancelReservation,
     Authorize, StartTransaction, MeterValues, StopTransaction, RemoteStartTransaction,
     RemoteStopTransaction, UnlockConnector, Reset.
3. LF Energy EVerest Architectural Alignment:
   - EvseBoardSupport, Auth, EvseManager, System/UI, OCPP models.
   - Comprehensive get_everest_status() taxonomy.
4. Dual-Mode Operation:
   - VirtualCSMS: 100% offline, deterministic in-memory SteVe engine for hermetic testing.
   - Live WebSocket Client: websockets.connect(csms_url, subprotocols=["ocpp1.6"]) with auto-reconnect.
5. Backwards Compatibility:
   - Preserves/re-exports translate_state_to_status_notification and translate_telemetry_to_meter_values.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import websockets
except ImportError:
    websockets = None

try:
    from ocpp_engine import OCPPEngine
except ImportError:
    OCPPEngine = None

logger = logging.getLogger("ocpp_bridge")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


# ══════════════════════════════════════════════════════════════════════════════
# 1. OCPP 1.6-J Enums, Error Codes & Framing Exceptions
# ══════════════════════════════════════════════════════════════════════════════

class OCPPMessageType(IntEnum):
    CALL = 2
    CALL_RESULT = 3
    CALL_ERROR = 4


class OCPPChargePointStatus(str, Enum):
    AVAILABLE = "Available"
    PREPARING = "Preparing"
    CHARGING = "Charging"
    SUSPENDED_EVSE = "SuspendedEVSE"
    SUSPENDED_EV = "SuspendedEV"
    FINISHING = "Finishing"
    RESERVED = "Reserved"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"


class OCPPChargePointErrorCode(str, Enum):
    NO_ERROR = "NoError"
    OVER_CURRENT_FAILURE = "OverCurrentFailure"
    OVER_VOLTAGE = "OverVoltage"
    UNDER_VOLTAGE = "UnderVoltage"
    CONNECTOR_LOCK_FAILURE = "ConnectorLockFailure"
    OTHER_ERROR = "OtherError"


class OCPPCallErrorCode(str, Enum):
    NOT_IMPLEMENTED = "NotImplemented"
    NOT_SUPPORTED = "NotSupported"
    INTERNAL_ERROR = "InternalError"
    PROTOCOL_ERROR = "ProtocolError"
    SECURITY_ERROR = "SecurityError"
    FORMATION_VIOLATION = "FormationViolation"
    PROPERTY_CONSTRAINT_VIOLATION = "PropertyConstraintViolation"
    OCCURRENCE_CONSTRAINT_VIOLATION = "OccurrenceConstraintViolation"
    TYPE_CONSTRAINT_VIOLATION = "TypeConstraintViolation"
    GENERIC_ERROR = "GenericError"


class OCPPCallError(Exception):
    """Raised when an OCPP Call receives a CallError frame or validation failure."""
    def __init__(
        self,
        error_code: str,
        error_description: str,
        error_details: Optional[dict] = None,
        unique_id: Optional[str] = None
    ):
        self.error_code = error_code
        self.error_description = error_description
        self.error_details = error_details or {}
        self.unique_id = unique_id
        super().__init__(f"OCPPCallError[{error_code}]: {error_description}")


def current_iso_z(timestamp: Optional[float] = None) -> str:
    """Returns strict ISO-8601 UTC timestamp string ending with 'Z'."""
    ts = timestamp if timestamp is not None else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# ══════════════════════════════════════════════════════════════════════════════
# 2. OCPP 1.6-J JSON-RPC 2.0 Frame Serialization & Parsing
# ══════════════════════════════════════════════════════════════════════════════

def serialize_call(unique_id: str, action: str, payload: dict) -> str:
    """Constructs an OCPP 1.6-J CALL frame: [2, uniqueId, action, payload]."""
    if not isinstance(unique_id, str) or not unique_id:
        raise OCPPCallError(OCPPCallErrorCode.FORMATION_VIOLATION, "UniqueId must be a non-empty string")
    if not isinstance(action, str) or not action:
        raise OCPPCallError(OCPPCallErrorCode.FORMATION_VIOLATION, "Action must be a non-empty string")
    if not isinstance(payload, dict):
        raise OCPPCallError(OCPPCallErrorCode.FORMATION_VIOLATION, "Payload must be a JSON object")
    return json.dumps([OCPPMessageType.CALL, unique_id, action, payload], separators=(",", ":"))


def serialize_call_result(unique_id: str, payload: dict) -> str:
    """Constructs an OCPP 1.6-J CALLRESULT frame: [3, uniqueId, payload]."""
    if not isinstance(unique_id, str) or not unique_id:
        raise OCPPCallError(OCPPCallErrorCode.FORMATION_VIOLATION, "UniqueId must be a non-empty string")
    if not isinstance(payload, dict):
        raise OCPPCallError(OCPPCallErrorCode.FORMATION_VIOLATION, "Payload must be a JSON object")
    return json.dumps([OCPPMessageType.CALL_RESULT, unique_id, payload], separators=(",", ":"))


def serialize_call_error(
    unique_id: str,
    error_code: str,
    error_description: str,
    error_details: Optional[dict] = None
) -> str:
    """Constructs an OCPP 1.6-J CALLERROR frame: [4, uniqueId, errorCode, errorDescription, errorDetails]."""
    uid = unique_id if isinstance(unique_id, str) and unique_id else ""
    code = error_code if isinstance(error_code, str) and error_code else OCPPCallErrorCode.GENERIC_ERROR
    desc = error_description if isinstance(error_description, str) else str(error_description)
    details = error_details if isinstance(error_details, dict) else {}
    return json.dumps([OCPPMessageType.CALL_ERROR, uid, code, desc, details], separators=(",", ":"))


def parse_frame(frame_data: Union[str, bytes, list]) -> Tuple[int, str, Any, Any, Any]:
    """
    Parses and strictly validates an OCPP 1.6-J JSON frame.
    Returns:
      (OCPPMessageType.CALL, unique_id, action, payload, None)
      (OCPPMessageType.CALL_RESULT, unique_id, payload, None, None)
      (OCPPMessageType.CALL_ERROR, unique_id, error_code, error_description, error_details)
    Raises OCPPCallError on formation or protocol violations.
    """
    if isinstance(frame_data, (str, bytes)):
        try:
            parsed = json.loads(frame_data)
        except Exception as e:
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"Malformed JSON frame: {e}"
            )
    elif isinstance(frame_data, list):
        parsed = frame_data
    else:
        raise OCPPCallError(
            OCPPCallErrorCode.FORMATION_VIOLATION,
            f"Frame must be JSON array, got {type(frame_data).__name__}"
        )

    if not isinstance(parsed, list):
        raise OCPPCallError(
            OCPPCallErrorCode.FORMATION_VIOLATION,
            "OCPP frame must be a JSON array"
        )

    if len(parsed) < 3:
        raise OCPPCallError(
            OCPPCallErrorCode.FORMATION_VIOLATION,
            f"Frame array length {len(parsed)} is less than required minimum 3"
        )

    msg_type = parsed[0]
    unique_id = parsed[1]

    if not isinstance(unique_id, str):
        raise OCPPCallError(
            OCPPCallErrorCode.FORMATION_VIOLATION,
            f"UniqueId must be a string, got {type(unique_id).__name__}"
        )

    if msg_type == OCPPMessageType.CALL:
        if len(parsed) != 4:
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"Call frame must have exactly 4 elements, got {len(parsed)}",
                unique_id=unique_id
            )
        action = parsed[2]
        payload = parsed[3]
        if not isinstance(action, str):
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"Call action must be a string, got {type(action).__name__}",
                unique_id=unique_id
            )
        if not isinstance(payload, dict):
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"Call payload must be a JSON object, got {type(payload).__name__}",
                unique_id=unique_id
            )
        return (OCPPMessageType.CALL, unique_id, action, payload, None)

    elif msg_type == OCPPMessageType.CALL_RESULT:
        if len(parsed) != 3:
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"CallResult frame must have exactly 3 elements, got {len(parsed)}",
                unique_id=unique_id
            )
        payload = parsed[2]
        if not isinstance(payload, dict):
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"CallResult payload must be a JSON object, got {type(payload).__name__}",
                unique_id=unique_id
            )
        return (OCPPMessageType.CALL_RESULT, unique_id, payload, None, None)

    elif msg_type == OCPPMessageType.CALL_ERROR:
        if len(parsed) < 4 or len(parsed) > 5:
            raise OCPPCallError(
                OCPPCallErrorCode.FORMATION_VIOLATION,
                f"CallError frame must have 4 or 5 elements, got {len(parsed)}",
                unique_id=unique_id
            )
        error_code = str(parsed[2])
        error_desc = str(parsed[3])
        error_details = parsed[4] if len(parsed) == 5 and isinstance(parsed[4], dict) else {}
        return (OCPPMessageType.CALL_ERROR, unique_id, error_code, error_desc, error_details)

    else:
        raise OCPPCallError(
            OCPPCallErrorCode.PROTOCOL_ERROR,
            f"Invalid OCPP MessageTypeId: {msg_type}. Expected 2, 3, or 4",
            unique_id=unique_id
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. LF Energy EVerest Architectural Domain Models
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EVerestEvseBoardSupport:
    """
    LF Energy EVerest board_support_common interface:
    Maps ESP32 physical hardware (ADC voltage GPIO 35, current GPIO 34,
    fail-safe Relay GPIO 15, and hardware trip protection <50ms).
    """
    station_id: str
    connector_id: str
    relay_state: str = "DE_ENERGIZED"
    voltage_v: float = 0.0
    current_a: float = 0.0
    power_kw: float = 0.0
    energy_kwh: float = 0.0
    is_tripped: bool = False
    fault_code: Optional[str] = None
    last_updated: float = field(default_factory=time.time)

    def update_telemetry(self, payload: dict) -> None:
        """Ingests live telemetry from MQTT and updates electrical measurements."""
        elec = payload.get("electrical", {})
        energy_data = payload.get("energy", {})

        self.voltage_v = float(payload.get("voltage_v", elec.get("voltage_v", self.voltage_v)))
        self.current_a = float(payload.get("current_a", elec.get("current_a", self.current_a)))
        calc_kw = round((self.voltage_v * self.current_a) / 1000.0, 3)
        self.power_kw = float(payload.get("power_kw", elec.get("power_kw", calc_kw)))
        self.energy_kwh = float(payload.get("energy_kwh", energy_data.get("energy_kwh", self.energy_kwh)))
        self.last_updated = time.time()

    def update_state(self, state: str, relay_state: str, fault_code: Optional[str] = None) -> None:
        """Updates contactor relay state and hardware fault status."""
        self.relay_state = str(relay_state).upper()
        self.fault_code = fault_code
        self.is_tripped = (str(state).upper() == "FAULTED")
        self.last_updated = time.time()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EVerestAuth:
    """
    LF Energy EVerest auth_token_provider & auth_token_validator interfaces:
    Maps 3x4 Matrix Keypad PIN and MFRC522 RFID credentials.
    """
    station_id: str
    connector_id: str
    last_auth_type: Optional[str] = None
    last_credential: Optional[str] = None
    last_decision: Optional[str] = None
    auth_window_active: bool = False
    auth_window_expiry: float = 0.0

    def record_auth_event(
        self,
        auth_type: str,
        credential: str,
        decision: str,
        window_ms: int = 8000
    ) -> None:
        """Records user credential attempt, authorization decision, and grant window."""
        self.last_auth_type = auth_type
        self.last_credential = credential
        self.last_decision = decision.upper()
        if self.last_decision == "ACCEPTED":
            self.auth_window_active = True
            self.auth_window_expiry = time.time() + (window_ms / 1000.0)
        else:
            self.auth_window_active = False
            self.auth_window_expiry = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EVerestEvseManager:
    """
    LF Energy EVerest evse_manager interface:
    Orchestrates the 4-state lifecycle (AVAILABLE, RESERVED, CHARGING, FAULTED),
    60-second reservation timer, and active transaction billing tracking.
    """
    station_id: str
    connector_id: str
    state: str = "AVAILABLE"
    active_reservation_id: Optional[str] = None
    reservation_expires_at: Optional[float] = None
    active_transaction_id: Optional[int] = None
    session_start_time: Optional[float] = None
    meter_start_kwh: float = 0.0

    def start_reservation(self, res_id: str, duration_s: int = 60) -> None:
        self.state = "RESERVED"
        self.active_reservation_id = str(res_id)
        self.reservation_expires_at = time.time() + duration_s

    def cancel_reservation(self) -> None:
        self.active_reservation_id = None
        self.reservation_expires_at = None
        if self.state == "RESERVED":
            self.state = "AVAILABLE"

    def start_transaction(self, tx_id: int, meter_start: float = 0.0) -> None:
        self.state = "CHARGING"
        self.active_transaction_id = int(tx_id)
        self.session_start_time = time.time()
        self.meter_start_kwh = float(meter_start)
        self.active_reservation_id = None
        self.reservation_expires_at = None

    def stop_transaction(self) -> None:
        self.active_transaction_id = None
        self.session_start_time = None
        if self.state == "CHARGING":
            self.state = "AVAILABLE"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EVerestSystemUI:
    """
    LF Energy EVerest system_ui interface:
    Maps local UI telemetry (SSD1306 OLED, ILI9341 TFT) and MicroSD /telemetry.csv.
    """
    station_id: str
    connector_id: str
    oled_display: dict = field(default_factory=dict)
    tft_screen: str = "STANDBY"
    sd_log_count: int = 0

    def update_oled(self, data: dict) -> None:
        self.oled_display.update(data)

    def update_tft(self, screen: str) -> None:
        self.tft_screen = screen

    def record_sd_write(self) -> None:
        self.sd_log_count += 1

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# 4. In-Memory Virtual CSMS (Mock SteVe Server for Hermetic Testing)
# ══════════════════════════════════════════════════════════════════════════════

class VirtualCSMS:
    """
    In-memory mock SteVe CSMS server providing 100% deterministic, offline
    OCPP 1.6-J JSON-RPC execution for automated testing without Docker.
    """
    def __init__(self):
        # SteVe ocpp_tag registry
        self.id_tag_registry: Dict[str, dict] = {
            "DEADBEEF": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"},
            "A1B2C3D4": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"},
            "CAFEFACE": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"},
            "0456789A": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"},
            "PIN_1234": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"},
            "BLOCKED_TAG": {"status": "Blocked"},
            "EXPIRED_TAG": {"status": "Expired"},
            "UNKNOWN_TAG": {"status": "Invalid"},
        }
        # SteVe tables
        self.registered_charge_boxes: Dict[str, dict] = {}
        self.reservations: Dict[int, dict] = {}
        self.transactions: Dict[int, dict] = {}
        self.connector_statuses: Dict[Tuple[str, int], dict] = {}
        self.meter_values_log: List[dict] = []
        self.call_log: List[dict] = []
        self._next_tx_id: int = 1001

        # Registered client bridge references for remote server commands
        self._charge_point_bridges: Dict[str, Any] = {}

    def register_charge_point(self, charge_box_id: str, bridge: Any) -> None:
        """Associates an active charge point bridge with this Virtual CSMS."""
        self._charge_point_bridges[charge_box_id] = bridge
        self.registered_charge_boxes[charge_box_id] = {
            "registered_at": time.time(),
            "last_seen": time.time()
        }

    def register_tag(self, id_tag: str, status: str = "Accepted", expiry_date: Optional[str] = None) -> None:
        """Seeds or updates an idTag in SteVe tag whitelist."""
        info = {"status": status}
        if expiry_date:
            info["expiryDate"] = expiry_date
        self.id_tag_registry[id_tag] = info

    def get_call_log(self, action: Optional[str] = None) -> List[dict]:
        """Returns chronological list of all OCPP calls."""
        if action is None:
            return list(self.call_log)
        return [c for c in self.call_log if c.get("action") == action]

    def get_meter_values(self, transaction_id: Optional[int] = None) -> List[dict]:
        """Returns recorded meter values filtered by transaction ID."""
        if transaction_id is None:
            return list(self.meter_values_log)
        return [mv for mv in self.meter_values_log if mv.get("transactionId") == transaction_id]

    def get_transaction(self, transaction_id: int) -> Optional[dict]:
        """Retrieves active or completed transaction record."""
        return self.transactions.get(transaction_id)

    def get_connector_status(self, charge_box_id: str, connector_id: int = 1) -> Optional[dict]:
        """Retrieves cached status notification for connector."""
        return self.connector_statuses.get((charge_box_id, connector_id))

    # ── Inbound Client Call Processing ────────────────────────────────────────

    async def process_call(
        self,
        charge_box_id: str,
        action: str,
        payload: dict,
        unique_id: str
    ) -> Tuple[int, dict]:
        """
        Processes an incoming Call from a Charge Point and returns
        (OCPPMessageType.CALL_RESULT, result_payload) or
        (OCPPMessageType.CALL_ERROR, error_payload).
        """
        self.call_log.append({
            "direction": "inbound",
            "charge_box_id": charge_box_id,
            "unique_id": unique_id,
            "action": action,
            "payload": payload,
            "timestamp": time.time()
        })

        if action == "BootNotification":
            self.registered_charge_boxes[charge_box_id] = {
                "vendor": payload.get("chargePointVendor"),
                "model": payload.get("chargePointModel"),
                "firmware": payload.get("firmwareVersion"),
                "last_seen": time.time()
            }
            return (
                OCPPMessageType.CALL_RESULT,
                {
                    "status": "Accepted",
                    "currentTime": current_iso_z(),
                    "interval": 300
                }
            )

        elif action == "Heartbeat":
            if charge_box_id in self.registered_charge_boxes:
                self.registered_charge_boxes[charge_box_id]["last_seen"] = time.time()
            return (
                OCPPMessageType.CALL_RESULT,
                {"currentTime": current_iso_z()}
            )

        elif action == "StatusNotification":
            connector_id = int(payload.get("connectorId", 1))
            self.connector_statuses[(charge_box_id, connector_id)] = {
                "status": payload.get("status"),
                "errorCode": payload.get("errorCode"),
                "info": payload.get("info", ""),
                "vendorErrorCode": payload.get("vendorErrorCode", ""),
                "timestamp": payload.get("timestamp", current_iso_z()),
                "received_at": time.time()
            }
            return (OCPPMessageType.CALL_RESULT, {})

        elif action == "Authorize":
            id_tag = str(payload.get("idTag", ""))
            tag_info = self.id_tag_registry.get(id_tag, {"status": "Invalid"})
            return (
                OCPPMessageType.CALL_RESULT,
                {"idTagInfo": tag_info}
            )

        elif action == "StartTransaction":
            connector_id = int(payload.get("connectorId", 1))
            id_tag = str(payload.get("idTag", ""))
            meter_start = payload.get("meterStart", 0)
            res_id = payload.get("reservationId")

            tx_id = self._next_tx_id
            self._next_tx_id += 1

            self.transactions[tx_id] = {
                "transactionId": tx_id,
                "charge_box_id": charge_box_id,
                "connectorId": connector_id,
                "idTag": id_tag,
                "meterStart": meter_start,
                "timestamp": payload.get("timestamp", current_iso_z()),
                "reservationId": res_id,
                "status": "Active",
                "start_time": time.time()
            }
            return (
                OCPPMessageType.CALL_RESULT,
                {
                    "transactionId": tx_id,
                    "idTagInfo": {"status": "Accepted"}
                }
            )

        elif action == "MeterValues":
            connector_id = int(payload.get("connectorId", 1))
            tx_id = payload.get("transactionId")
            meter_value = payload.get("meterValue", [])

            # Validate that every sampledValue.value is a string per OCPP 1.6 spec
            for mv in meter_value:
                for sv in mv.get("sampledValue", []):
                    if not isinstance(sv.get("value"), str):
                        return (
                            OCPPMessageType.CALL_ERROR,
                            {
                                "errorCode": OCPPCallErrorCode.FORMATION_VIOLATION.value,
                                "errorDescription": "sampledValue.value must be a string",
                                "errorDetails": {"field": "sampledValue.value", "type": type(sv.get("value")).__name__}
                            }
                        )

            self.meter_values_log.append({
                "charge_box_id": charge_box_id,
                "connectorId": connector_id,
                "transactionId": tx_id,
                "meterValue": meter_value,
                "received_at": time.time()
            })
            return (OCPPMessageType.CALL_RESULT, {})

        elif action == "StopTransaction":
            tx_id = payload.get("transactionId")
            meter_stop = payload.get("meterStop", 0)
            reason = payload.get("reason", "Local")

            if tx_id in self.transactions:
                self.transactions[tx_id].update({
                    "meterStop": meter_stop,
                    "stop_timestamp": payload.get("timestamp", current_iso_z()),
                    "reason": reason,
                    "status": "Stopped",
                    "stop_time": time.time()
                })

            return (
                OCPPMessageType.CALL_RESULT,
                {"idTagInfo": {"status": "Accepted"}}
            )

        else:
            return (
                OCPPMessageType.CALL_ERROR,
                {
                    "errorCode": OCPPCallErrorCode.NOT_IMPLEMENTED.value,
                    "errorDescription": f"Action '{action}' is not implemented by VirtualCSMS",
                    "errorDetails": {"action": action}
                }
            )

    async def handle_raw_frame(self, charge_box_id: str, raw_frame_str: str) -> str:
        """Processes a raw JSON frame string and returns a raw JSON response string."""
        try:
            msg_type, uid, action_or_payload, payload_or_err, _ = parse_frame(raw_frame_str)
        except OCPPCallError as e:
            return serialize_call_error(e.unique_id or "", e.error_code, e.error_description, e.error_details)

        if msg_type == OCPPMessageType.CALL:
            resp_type, resp_payload = await self.process_call(
                charge_box_id=charge_box_id,
                action=action_or_payload,
                payload=payload_or_err,
                unique_id=uid
            )
            if resp_type == OCPPMessageType.CALL_RESULT:
                return serialize_call_result(uid, resp_payload)
            else:
                return serialize_call_error(
                    uid,
                    resp_payload.get("errorCode", OCPPCallErrorCode.GENERIC_ERROR.value),
                    resp_payload.get("errorDescription", "Call Error"),
                    resp_payload.get("errorDetails", {})
                )
        return serialize_call_error(uid, OCPPCallErrorCode.PROTOCOL_ERROR.value, "Unexpected frame type to CSMS")

    # ── Outbound Server-to-Client Remote Command Dispatchers ──────────────────

    async def send_reserve_now(
        self,
        charge_box_id: str,
        connector_id: int,
        expiry_date: str,
        id_tag: str,
        reservation_id: int,
        parent_id_tag: Optional[str] = None
    ) -> dict:
        """Dispatches ReserveNow call to the specified charge point bridge."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {
            "connectorId": int(connector_id),
            "expiryDate": expiry_date,
            "idTag": str(id_tag),
            "reservationId": int(reservation_id)
        }
        if parent_id_tag:
            payload["parentIdTag"] = parent_id_tag

        uid = f"csms_res_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "ReserveNow",
            "payload": payload,
            "timestamp": time.time()
        })

        result = await bridge.handle_csms_call("ReserveNow", payload, uid)
        if result.get("status") == "Accepted":
            self.reservations[reservation_id] = payload
        return result

    async def send_cancel_reservation(
        self,
        charge_box_id: str,
        reservation_id: int
    ) -> dict:
        """Dispatches CancelReservation call to the specified charge point bridge."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {"reservationId": int(reservation_id)}
        uid = f"csms_can_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "CancelReservation",
            "payload": payload,
            "timestamp": time.time()
        })

        result = await bridge.handle_csms_call("CancelReservation", payload, uid)
        if result.get("status") == "Accepted":
            self.reservations.pop(reservation_id, None)
        return result

    async def send_remote_start_transaction(
        self,
        charge_box_id: str,
        id_tag: str,
        connector_id: int = 1
    ) -> dict:
        """Dispatches RemoteStartTransaction call."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {"connectorId": int(connector_id), "idTag": str(id_tag)}
        uid = f"csms_rem_start_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "RemoteStartTransaction",
            "payload": payload,
            "timestamp": time.time()
        })
        return await bridge.handle_csms_call("RemoteStartTransaction", payload, uid)

    async def send_remote_stop_transaction(
        self,
        charge_box_id: str,
        transaction_id: int
    ) -> dict:
        """Dispatches RemoteStopTransaction call."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {"transactionId": int(transaction_id)}
        uid = f"csms_rem_stop_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "RemoteStopTransaction",
            "payload": payload,
            "timestamp": time.time()
        })
        return await bridge.handle_csms_call("RemoteStopTransaction", payload, uid)

    async def send_unlock_connector(
        self,
        charge_box_id: str,
        connector_id: int = 1
    ) -> dict:
        """Dispatches UnlockConnector call."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {"connectorId": int(connector_id)}
        uid = f"csms_unlock_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "UnlockConnector",
            "payload": payload,
            "timestamp": time.time()
        })
        return await bridge.handle_csms_call("UnlockConnector", payload, uid)

    async def send_reset(
        self,
        charge_box_id: str,
        reset_type: str = "Soft"
    ) -> dict:
        """Dispatches Reset call."""
        bridge = self._charge_point_bridges.get(charge_box_id)
        if not bridge:
            raise RuntimeError(f"Charge box '{charge_box_id}' is not connected to VirtualCSMS")

        payload = {"type": reset_type}
        uid = f"csms_reset_{uuid.uuid4().hex[:8]}"
        self.call_log.append({
            "direction": "outbound",
            "charge_box_id": charge_box_id,
            "unique_id": uid,
            "action": "Reset",
            "payload": payload,
            "timestamp": time.time()
        })
        return await bridge.handle_csms_call("Reset", payload, uid)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Dual-Mode OCPP 1.6-J Bridge & EVerest Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class OCPPBridge:
    """
    Dual-mode SteVe OCPP 1.6-J CSMS & LF Energy EVerest Bridge.
    Connects EV Sarthi edge IoT hardware / MQTT broker with SteVe CSMS.
    """
    STATUS_MAP = {
        "AVAILABLE": "Available",
        "RESERVED": "Reserved",
        "CHARGING": "Charging",
        "FAULTED": "Faulted",
    }

    ERROR_CODE_MAP = {
        "OVERCURRENT": "OverCurrentFailure",
        "OVERVOLTAGE": "OverVoltage",
        "SENSOR_DISCONNECT": "OtherError",
        "SD_WRITE_FAIL": "OtherError",
        None: "NoError",
    }

    def __init__(
        self,
        station_id: str = "ST001",
        connector_id: str = "1",
        charge_box_id: Optional[str] = None,
        mode: str = "virtual",           # "virtual" | "live"
        csms_url: Optional[str] = None,  # ws://localhost:8180/...
        mqtt_svc: Any = None,
        database: Any = None,
        virtual_csms: Optional[VirtualCSMS] = None
    ):
        self.station_id = station_id
        self.connector_id = str(connector_id)
        self.charge_box_id = charge_box_id or f"EV_SARTHI_{station_id}"
        self.mode = mode.lower()
        self.csms_url = csms_url
        self.mqtt_svc = mqtt_svc
        self.db = database
        self.virtual_csms = virtual_csms or (VirtualCSMS() if self.mode == "virtual" else None)

        # LF Energy EVerest Domain Models
        self.everest_board = EVerestEvseBoardSupport(self.station_id, self.connector_id)
        self.everest_auth = EVerestAuth(self.station_id, self.connector_id)
        self.everest_manager = EVerestEvseManager(self.station_id, self.connector_id)
        self.everest_ui = EVerestSystemUI(self.station_id, self.connector_id)

        # Protocol & Transport State
        self._connected: bool = False
        self._boot_accepted: bool = False
        self._heartbeat_interval: int = 300
        self._pending_calls: Dict[str, asyncio.Future] = {}
        self._call_log: List[dict] = []
        self._ws: Any = None
        self._background_tasks: List[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    # ── Lifecycle Management ──────────────────────────────────────────────────

    async def start(
        self,
        mqtt_svc: Any = None,
        database: Any = None,
        virtual_csms: Optional[VirtualCSMS] = None
    ) -> None:
        """Initializes the bridge, registers MQTT hooks, and establishes CSMS connection."""
        if mqtt_svc:
            self.mqtt_svc = mqtt_svc
        if database:
            self.db = database
        if virtual_csms:
            self.virtual_csms = virtual_csms

        self._stop_event.clear()

        # 1. Wire Inbound MQTT Event Listeners
        if self.mqtt_svc:
            self._register_mqtt_listeners()

        # 2. Establish CSMS Connection (Virtual or Live)
        if self.mode == "virtual":
            if not self.virtual_csms:
                self.virtual_csms = VirtualCSMS()
            self.virtual_csms.register_charge_point(self.charge_box_id, self)
            self._connected = True
            logger.info(f"[OCPPBridge] Connected to VirtualCSMS for charge box '{self.charge_box_id}'")
        elif self.mode == "live":
            if not self.csms_url:
                raise ValueError("csms_url must be specified when mode is 'live'")
            task = asyncio.create_task(self._live_websocket_loop())
            self._background_tasks.append(task)
            # Wait briefly for initial connection
            for _ in range(20):
                if self._connected:
                    break
                await asyncio.sleep(0.05)

        # 3. Perform Initial BootNotification & StatusNotification
        try:
            boot_resp = await self.send_boot_notification()
            if boot_resp.get("status") == "Accepted":
                self._boot_accepted = True
                self._heartbeat_interval = int(boot_resp.get("interval", 300))
            await self.send_status_notification(
                status=OCPPChargePointStatus.AVAILABLE.value,
                error_code=OCPPChargePointErrorCode.NO_ERROR.value,
                info="Station initialized and ready"
            )
        except Exception as e:
            logger.warning(f"[OCPPBridge] Initial Boot/Status handshake warning: {e}")

    async def stop(self) -> None:
        """Gracefully shuts down bridge, closes connections and cancels background tasks."""
        self._stop_event.set()
        self._connected = False
        self._boot_accepted = False

        # Cancel pending call futures
        for uid, fut in list(self._pending_calls.items()):
            if not fut.done():
                fut.set_exception(asyncio.CancelledError("OCPPBridge is shutting down"))
        self._pending_calls.clear()

        # Close live WebSocket if active
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        logger.info(f"[OCPPBridge] Stopped bridge for '{self.charge_box_id}'")

    def is_connected(self) -> bool:
        """Returns True if the CSMS connection is active and healthy."""
        return self._connected

    def _register_mqtt_listeners(self) -> None:
        """Binds bridge handlers to MQTT topics via add_message_listener or subscribe."""
        topics = [
            (f"evsarthi/{self.station_id}/{self.connector_id}/state", self.handle_mqtt_state),
            (f"evsarthi/{self.station_id}/{self.connector_id}/telemetry", self.handle_mqtt_telemetry),
            (f"evsarthi/{self.station_id}/{self.connector_id}/auth", self.handle_mqtt_auth),
        ]
        if hasattr(self.mqtt_svc, "add_message_listener"):
            for top, cb in topics:
                self.mqtt_svc.add_message_listener(top, cb)
        elif hasattr(self.mqtt_svc, "subscribe"):
            for top, cb in topics:
                res = self.mqtt_svc.subscribe(top, cb)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)

    # ── Live WebSocket Loop & Reconnection ────────────────────────────────────

    async def _live_websocket_loop(self) -> None:
        """Maintains live WebSocket connection to external SteVe CSMS with auto-reconnect."""
        if not websockets:
            logger.error("[OCPPBridge] websockets library not installed. Live mode disabled.")
            return

        backoff = 2.0
        while not self._stop_event.is_set():
            try:
                logger.info(f"[OCPPBridge] Connecting to SteVe CSMS at {self.csms_url}...")
                async with websockets.connect(
                    self.csms_url,
                    subprotocols=["ocpp1.6"],
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 2.0
                    logger.info("[OCPPBridge] Live WebSocket connected with subprotocol 'ocpp1.6'")

                    # Receive loop
                    async for raw_msg in ws:
                        await self._handle_incoming_raw_frame(raw_msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.warning(f"[OCPPBridge] Live CSMS connection lost: {e}. Reconnecting in {backoff:.1f}s...")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 1.5, 60.0)

        self._connected = False

    async def _handle_incoming_raw_frame(self, raw_msg: Union[str, bytes]) -> None:
        """Routes incoming frame from live WebSocket connection."""
        try:
            msg_type, uid, p1, p2, p3 = parse_frame(raw_msg)
        except OCPPCallError as e:
            logger.error(f"[OCPPBridge] Frame parse error: {e}")
            if e.unique_id and self._ws:
                err_frame = serialize_call_error(e.unique_id, e.error_code, e.error_description, e.error_details)
                await self._ws.send(err_frame)
            return

        if msg_type == OCPPMessageType.CALL:
            action = p1
            payload = p2
            resp_payload = await self.handle_csms_call(action, payload, uid)
            if self._ws:
                resp_frame = serialize_call_result(uid, resp_payload)
                await self._ws.send(resp_frame)

        elif msg_type == OCPPMessageType.CALL_RESULT:
            payload = p1
            fut = self._pending_calls.pop(uid, None)
            if fut and not fut.done():
                fut.set_result(payload)

        elif msg_type == OCPPMessageType.CALL_ERROR:
            error_code, error_desc, error_details = p1, p2, p3
            fut = self._pending_calls.pop(uid, None)
            if fut and not fut.done():
                fut.set_exception(OCPPCallError(error_code, error_desc, error_details, unique_id=uid))

    # ── Outbound OCPP 1.6-J Call Dispatcher ───────────────────────────────────

    async def _send_call(self, action: str, payload: dict, timeout: float = 10.0) -> dict:
        """
        Constructs and transmits an OCPP Call frame to the CSMS.
        In virtual mode: calls VirtualCSMS.process_call synchronously in-memory.
        In live mode: transmits frame via WebSocket and awaits CallResult/CallError.
        """
        unique_id = f"bridge_{action[:4].lower()}_{uuid.uuid4().hex[:8]}"

        self._call_log.append({
            "direction": "outbound",
            "action": action,
            "unique_id": unique_id,
            "payload": payload,
            "timestamp": time.time()
        })

        if self.mode == "virtual":
            if not self.virtual_csms:
                raise RuntimeError("VirtualCSMS not initialized in virtual mode")
            resp_type, resp_payload = await self.virtual_csms.process_call(
                charge_box_id=self.charge_box_id,
                action=action,
                payload=payload,
                unique_id=unique_id
            )
            if resp_type == OCPPMessageType.CALL_RESULT:
                return resp_payload
            else:
                code = resp_payload.get("errorCode", OCPPCallErrorCode.GENERIC_ERROR.value)
                desc = resp_payload.get("errorDescription", "Call Error")
                details = resp_payload.get("errorDetails", {})
                raise OCPPCallError(code, desc, details, unique_id=unique_id)

        elif self.mode == "live":
            if not self._ws or not self._connected:
                raise ConnectionError(f"Cannot send {action}: WebSocket not connected to CSMS")
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._pending_calls[unique_id] = fut

            frame = serialize_call(unique_id, action, payload)
            await self._ws.send(frame)
            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending_calls.pop(unique_id, None)
                raise TimeoutError(f"Timed out after {timeout}s waiting for {action} CallResult")
        else:
            raise ValueError(f"Unknown bridge mode: '{self.mode}'")

    # ── Core Outbound Actions ─────────────────────────────────────────────────

    async def send_boot_notification(
        self,
        charge_point_vendor: str = "EV_Sarthi",
        charge_point_model: str = "ESP32-DevKitC-v4",
        firmware_version: str = "1.0.0",
        serial_number: str = "EVS-ESP32-001"
    ) -> dict:
        """Sends BootNotification Call to register hardware with CSMS."""
        payload = {
            "chargePointVendor": charge_point_vendor,
            "chargePointModel": charge_point_model,
            "firmwareVersion": firmware_version,
            "chargePointSerialNumber": serial_number
        }
        res = await self._send_call("BootNotification", payload)
        if res.get("status") == "Accepted":
            self._boot_accepted = True
            self._heartbeat_interval = int(res.get("interval", 300))
        return res

    async def send_heartbeat(self) -> dict:
        """Sends periodic Heartbeat Call to confirm connection liveness."""
        return await self._send_call("Heartbeat", {})

    async def send_status_notification(
        self,
        connector_id: Optional[int] = None,
        status: Optional[str] = None,
        error_code: Optional[str] = None,
        info: str = "",
        vendor_error_code: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> dict:
        """Sends StatusNotification Call reporting connector state and error codes."""
        conn_num = int(connector_id if connector_id is not None else self.connector_id)
        stat = status or self.STATUS_MAP.get(self.everest_manager.state, "Available")
        err = error_code or (
            self.ERROR_CODE_MAP.get(self.everest_board.fault_code, "NoError")
            if stat != "Faulted" else "OtherError"
        )
        ts = timestamp or current_iso_z()

        payload = {
            "connectorId": conn_num,
            "errorCode": err,
            "status": stat,
            "timestamp": ts,
            "info": info or (self.everest_board.fault_code or ""),
            "vendorId": "EV_SARTHI"
        }
        if vendor_error_code:
            payload["vendorErrorCode"] = str(vendor_error_code)

        return await self._send_call("StatusNotification", payload)

    async def send_authorize(self, id_tag: str) -> dict:
        """Sends Authorize Call to validate user RFID card UID or PIN with CSMS."""
        return await self._send_call("Authorize", {"idTag": str(id_tag)})

    async def send_start_transaction(
        self,
        id_tag: str,
        connector_id: Optional[int] = None,
        meter_start: int = 0,
        reservation_id: Optional[int] = None
    ) -> dict:
        """Sends StartTransaction Call upon authorized cable insertion."""
        conn_num = int(connector_id if connector_id is not None else self.connector_id)
        payload = {
            "connectorId": conn_num,
            "idTag": str(id_tag),
            "meterStart": int(meter_start),
            "timestamp": current_iso_z()
        }
        if reservation_id is not None:
            payload["reservationId"] = int(reservation_id)

        res = await self._send_call("StartTransaction", payload)
        tx_id = res.get("transactionId")
        if tx_id:
            self.everest_manager.start_transaction(int(tx_id), float(meter_start))
        return res

    async def send_meter_values(
        self,
        telemetry_payload: Optional[dict] = None,
        connector_id: Optional[int] = None,
        transaction_id: Optional[int] = None
    ) -> dict:
        """
        Sends MeterValues Call translating electrical telemetry into string sampled values.
        """
        conn_num = int(connector_id if connector_id is not None else self.connector_id)
        tx_num = transaction_id if transaction_id is not None else self.everest_manager.active_transaction_id

        if telemetry_payload:
            self.everest_board.update_telemetry(telemetry_payload)
            req = self.translate_telemetry_to_meter_values(
                telemetry_payload,
                station_id=self.station_id,
                transaction_id=tx_num
            )
            return await self._send_call("MeterValues", req)

        # Fallback to current board support cache
        v = self.everest_board.voltage_v
        c = self.everest_board.current_a
        p = self.everest_board.power_kw
        e = self.everest_board.energy_kwh

        payload = {
            "connectorId": conn_num,
            "meterValue": [
                {
                    "timestamp": current_iso_z(),
                    "sampledValue": [
                        {
                            "value": f"{v:.1f}",
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Voltage",
                            "phase": "L1",
                            "location": "Outlet",
                            "unit": "V"
                        },
                        {
                            "value": f"{c:.2f}",
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Current.Import",
                            "phase": "L1",
                            "location": "Outlet",
                            "unit": "A"
                        },
                        {
                            "value": f"{p:.3f}",
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Power.Active.Import",
                            "location": "Outlet",
                            "unit": "kW"
                        },
                        {
                            "value": f"{e:.4f}",
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Energy.Active.Import.Register",
                            "location": "Outlet",
                            "unit": "kWh"
                        }
                    ]
                }
            ]
        }
        if tx_num is not None:
            payload["transactionId"] = int(tx_num)

        return await self._send_call("MeterValues", payload)

    async def send_stop_transaction(
        self,
        transaction_id: Optional[int] = None,
        meter_stop: Optional[int] = None,
        reason: str = "Local",
        id_tag: Optional[str] = None
    ) -> dict:
        """Sends StopTransaction Call to terminate active charging session."""
        tx_id = transaction_id if transaction_id is not None else self.everest_manager.active_transaction_id
        if tx_id is None:
            logger.warning("[OCPPBridge] send_stop_transaction called with no active transaction")
            return {"status": "NoActiveTransaction"}

        stop_reading = (
            int(meter_stop) if meter_stop is not None
            else int(self.everest_board.energy_kwh * 1000.0)
        )
        tag = id_tag or self.everest_auth.last_credential or "UNKNOWN"

        payload = {
            "transactionId": int(tx_id),
            "meterStop": stop_reading,
            "timestamp": current_iso_z(),
            "reason": reason,
            "idTag": str(tag)
        }

        res = await self._send_call("StopTransaction", payload)
        self.everest_manager.stop_transaction()
        self.everest_board.relay_state = "DE_ENERGIZED"
        return res

    # ── Inbound MQTT Message Handlers (Hardware -> Bridge -> CSMS) ───────────

    async def handle_mqtt_state(self, topic: str, payload: dict) -> None:
        """Processes evsarthi/{station}/{connector}/state message."""
        if not isinstance(payload, dict):
            return

        raw_state = str(payload.get("state", "AVAILABLE")).upper()
        relay = str(payload.get("relay_state", "DE_ENERGIZED")).upper()
        fault = payload.get("fault_code")
        reason = payload.get("transition_reason", "")

        # 1. Update EVerest models
        self.everest_board.update_state(raw_state, relay, fault)
        self.everest_manager.state = raw_state

        # 2. Map to OCPP StatusNotification
        ocpp_status = self.STATUS_MAP.get(raw_state, "Available")
        error_code = self.ERROR_CODE_MAP.get(fault, "OtherError" if raw_state == "FAULTED" else "NoError")

        # 3. Emergency trip handling: stop transaction if faulted while charging
        if raw_state == "FAULTED" and self.everest_manager.active_transaction_id:
            logger.warning(f"[OCPPBridge] Hardware trip detected while charging! Stopping transaction.")
            try:
                await self.send_stop_transaction(
                    transaction_id=self.everest_manager.active_transaction_id,
                    reason="EmergencyStop"
                )
            except Exception as e:
                logger.error(f"[OCPPBridge] Error stopping transaction on fault trip: {e}")

        # 4. EV Disconnect / session termination handling: stop active transaction if returning to AVAILABLE
        if raw_state in ["AVAILABLE", "FINISHING"] and self.everest_manager.active_transaction_id:
            logger.info(
                f"[OCPPBridge] EV disconnected / session end detected. Stopping active transaction {self.everest_manager.active_transaction_id}"
            )
            try:
                await self.send_stop_transaction(
                    transaction_id=self.everest_manager.active_transaction_id,
                    reason="EVDisconnected"
                )
            except Exception as e:
                logger.error(f"[OCPPBridge] Error stopping transaction on disconnect: {e}")

        # 5. Flush reservation state on return to AVAILABLE (e.g. 60s timeout or session completion)
        if raw_state == "AVAILABLE":
            self.everest_manager.cancel_reservation()

        # 6. Dispatch StatusNotification Call to CSMS
        try:
            await self.send_status_notification(
                status=ocpp_status,
                error_code=error_code,
                info=reason,
                vendor_error_code=fault
            )
        except Exception as e:
            logger.error(f"[OCPPBridge] Error sending StatusNotification for state {raw_state}: {e}")

    async def handle_mqtt_telemetry(self, topic: str, payload: dict) -> None:
        """Processes evsarthi/{station}/{connector}/telemetry message."""
        if not isinstance(payload, dict):
            return

        # 1. Update EVerest BoardSupport & UI models
        self.everest_board.update_telemetry(payload)
        self.everest_ui.record_sd_write()
        self.everest_ui.update_oled({
            "v": self.everest_board.voltage_v,
            "a": self.everest_board.current_a,
            "kw": self.everest_board.power_kw,
            "kwh": self.everest_board.energy_kwh
        })

        # 2. Dispatch MeterValues Call to CSMS
        try:
            await self.send_meter_values(
                telemetry_payload=payload,
                transaction_id=self.everest_manager.active_transaction_id
            )
        except Exception as e:
            logger.error(f"[OCPPBridge] Error dispatching MeterValues: {e}")

    async def handle_mqtt_auth(self, topic: str, payload: dict) -> None:
        """Processes evsarthi/{station}/{connector}/auth message."""
        if not isinstance(payload, dict):
            return

        auth_type = str(payload.get("auth_type", "RFID")).upper()
        credential_raw = payload.get("credential", {})
        if isinstance(credential_raw, dict):
            id_tag = credential_raw.get("rfid_uid") or credential_raw.get("pin") or ""
        else:
            id_tag = str(credential_raw)

        if not id_tag:
            logger.warning(f"[OCPPBridge] Inbound auth event with empty credential on {topic}")
            return

        # 0. Safety Guard: Check if connector or hardware is in FAULTED state
        if (
            self.everest_manager.state == "FAULTED"
            or self.everest_board.is_tripped
            or str(payload.get("current_state", "")).upper() == "FAULTED"
        ):
            logger.warning(
                f"[OCPPBridge] Suppressing auth grant for '{id_tag}' because connector is in FAULTED state"
            )
            auth_decision = "DENIED"
            status_code = 403
            relay_action = "DE_ENERGIZED"
            self.everest_auth.record_auth_event(auth_type, id_tag, auth_decision)
            if self.mqtt_svc:
                if hasattr(self.mqtt_svc, "publish_cmd"):
                    await self.mqtt_svc.publish_cmd(
                        station_id=self.station_id,
                        connector_id=self.connector_id,
                        action="AUTH_DECISION",
                        status=auth_decision,
                        status_code=status_code,
                        auth_window_duration_ms=0,
                        relay_action=relay_action
                    )
                elif hasattr(self.mqtt_svc, "publish"):
                    cmd_topic = f"evsarthi/{self.station_id}/{self.connector_id}/cmd"
                    cmd_payload = {
                        "action": "AUTH_DECISION",
                        "status": auth_decision,
                        "status_code": status_code,
                        "auth_window_duration_ms": 0,
                        "relay_action": relay_action
                    }
                    await self.mqtt_svc.publish(cmd_topic, cmd_payload)
            return

        # 1. Dispatch Authorize Call to SteVe CSMS
        auth_decision = "DENIED"
        status_code = 401
        relay_action = "DE_ENERGIZED"
        try:
            auth_res = await self.send_authorize(id_tag)
            tag_info = auth_res.get("idTagInfo", {})
            status = tag_info.get("status")

            if status == "Accepted":
                auth_decision = "ACCEPTED"
                status_code = 200
                relay_action = "ENERGIZE"
        except Exception as e:
            logger.error(f"[OCPPBridge] Error calling CSMS Authorize for '{id_tag}': {e}")

        # 2. Record event in EVerest Auth
        self.everest_auth.record_auth_event(auth_type, id_tag, auth_decision)

        # 3. Publish control decision on MQTT /cmd
        if self.mqtt_svc:
            if hasattr(self.mqtt_svc, "publish_cmd"):
                await self.mqtt_svc.publish_cmd(
                    station_id=self.station_id,
                    connector_id=self.connector_id,
                    action="AUTH_DECISION",
                    status=auth_decision,
                    status_code=status_code,
                    auth_window_duration_ms=30000,
                    relay_action=relay_action
                )
            elif hasattr(self.mqtt_svc, "publish"):
                cmd_topic = f"evsarthi/{self.station_id}/{self.connector_id}/cmd"
                cmd_payload = {
                    "action": "AUTH_DECISION",
                    "status": auth_decision,
                    "status_code": status_code,
                    "auth_window_duration_ms": 30000,
                    "relay_action": relay_action
                }
                await self.mqtt_svc.publish(cmd_topic, cmd_payload)

        # 4. If Accepted, initiate StartTransaction
        if auth_decision == "ACCEPTED":
            try:
                await self.send_start_transaction(
                    id_tag=id_tag,
                    connector_id=int(self.connector_id),
                    meter_start=int(self.everest_board.energy_kwh * 1000.0)
                )
            except Exception as e:
                logger.error(f"[OCPPBridge] Error starting transaction on auth grant: {e}")

    # ── Inbound CSMS Call Handlers (CSMS -> Bridge -> Hardware) ───────────────

    async def handle_csms_call(self, action: str, payload: dict, unique_id: str) -> dict:
        """Dispatches an incoming Call from the CSMS to the local handler."""
        if action == "ReserveNow":
            return await self.handle_reserve_now(payload)
        elif action == "CancelReservation":
            return await self.handle_cancel_reservation(payload)
        elif action == "RemoteStartTransaction":
            return await self.handle_remote_start_transaction(payload)
        elif action == "RemoteStopTransaction":
            return await self.handle_remote_stop_transaction(payload)
        elif action == "UnlockConnector":
            return await self.handle_unlock_connector(payload)
        elif action == "Reset":
            return await self.handle_reset(payload)
        else:
            raise OCPPCallError(
                OCPPCallErrorCode.NOT_IMPLEMENTED,
                f"Action '{action}' is not supported by Charge Point",
                unique_id=unique_id
            )

    async def handle_reserve_now(self, payload: dict) -> dict:
        """Handles CSMS ReserveNow: checks connector status, updates DB, and dispatches MQTT."""
        conn_id = payload.get("connectorId")
        if str(conn_id) not in [str(self.connector_id), "1"]:
            return {"status": "Rejected"}

        curr_state = self.everest_manager.state
        if curr_state == "FAULTED":
            return {"status": "Faulted"}
        elif curr_state in ["CHARGING", "RESERVED"]:
            return {"status": "Occupied"}

        res_id = payload.get("reservationId")
        id_tag = payload.get("idTag", "")
        exp_date = payload.get("expiryDate")

        # 1. Update EVerest EvseManager
        self.everest_manager.start_reservation(str(res_id), duration_s=60)

        # 2. Derive 4-digit PIN for keypad access from reservation ID
        pin = str(res_id)[-4:].zfill(4) if isinstance(res_id, (int, str)) else "1234"

        # 3. Publish MQTT reservation command to hardware
        if self.mqtt_svc:
            if hasattr(self.mqtt_svc, "publish_reserve"):
                await self.mqtt_svc.publish_reserve(
                    station_id=self.station_id,
                    connector_id=self.connector_id,
                    action="CREATE",
                    reservation_id=str(res_id),
                    pin=pin,
                    duration_seconds=60,
                    expires_at=int(time.time() + 60),
                    rfid_uid=id_tag
                )
            elif hasattr(self.mqtt_svc, "publish"):
                r_topic = f"evsarthi/{self.station_id}/{self.connector_id}/reserve"
                r_payload = {
                    "action": "CREATE",
                    "reservation_id": str(res_id),
                    "pin": pin,
                    "duration_seconds": 60,
                    "expires_at": int(time.time() + 60),
                    "rfid_uid": id_tag
                }
                await self.mqtt_svc.publish(r_topic, r_payload)

        # 4. Persist in database if available
        if self.db and hasattr(self.db, "save_reservation"):
            try:
                res_dict = {
                    "station_id": self.station_id,
                    "connector_id": self.connector_id,
                    "reservation_id": str(res_id),
                    "user_id": f"user_{id_tag}",
                    "user_name": "SteVe Reserved User",
                    "pin": pin,
                    "duration_minutes": 30,
                    "rfid_uid": id_tag,
                    "created_at": time.time(),
                    "expires_at": time.time() + 1800,
                }
                await self.db.save_reservation(res_dict)
            except Exception as e:
                logger.warning(f"[OCPPBridge] Database reservation save: {e}")

        return {"status": "Accepted"}

    async def handle_cancel_reservation(self, payload: dict) -> dict:
        """Handles CSMS CancelReservation."""
        res_id = payload.get("reservationId")
        if (
            not res_id
            or not self.everest_manager.active_reservation_id
            or str(res_id) != str(self.everest_manager.active_reservation_id)
            or self.everest_manager.state != "RESERVED"
        ):
            return {"status": "Rejected"}

        # 1. Update EVerest EvseManager
        self.everest_manager.cancel_reservation()

        # 2. Dispatch MQTT cancel to hardware
        if self.mqtt_svc:
            if hasattr(self.mqtt_svc, "publish_reserve"):
                await self.mqtt_svc.publish_reserve(
                    station_id=self.station_id,
                    connector_id=self.connector_id,
                    action="CANCEL",
                    reservation_id=str(res_id),
                    pin=""
                )
            elif hasattr(self.mqtt_svc, "publish"):
                r_topic = f"evsarthi/{self.station_id}/{self.connector_id}/reserve"
                await self.mqtt_svc.publish(r_topic, {
                    "action": "CANCEL",
                    "reservation_id": str(res_id)
                })

        # 3. Clean up database reservation
        if self.db and hasattr(self.db, "delete_reservation"):
            try:
                await self.db.delete_reservation(self.station_id, self.connector_id)
            except Exception as e:
                logger.warning(f"[OCPPBridge] Database reservation delete: {e}")

        return {"status": "Accepted"}

    async def handle_remote_start_transaction(self, payload: dict) -> dict:
        """Handles CSMS RemoteStartTransaction."""
        if not isinstance(payload, dict):
            return {"status": "Rejected"}

        # 1. Validate mandatory idTag per OCPP 1.6-J Section 4.6
        id_tag = payload.get("idTag")
        if not id_tag or not isinstance(id_tag, str) or not id_tag.strip():
            return {"status": "Rejected"}

        # 2. Validate connectorId matches this bridge connector
        raw_conn_id = payload.get("connectorId")
        if raw_conn_id is not None:
            try:
                conn_id = int(raw_conn_id)
            except (ValueError, TypeError):
                return {"status": "Rejected"}

            try:
                bridge_conn = int(self.connector_id)
            except (ValueError, TypeError):
                bridge_conn = 1

            if conn_id not in [bridge_conn, 0]:
                return {"status": "Rejected"}
            target_conn = bridge_conn if conn_id == 0 else conn_id
        else:
            try:
                target_conn = int(self.connector_id)
            except (ValueError, TypeError):
                target_conn = 1

        # 3. State guard: cannot start if faulted, already charging, or tripped
        if (
            self.everest_manager.state in ["CHARGING", "FAULTED"]
            or self.everest_board.is_tripped
        ):
            return {"status": "Rejected"}

        # 4. Reservation check: if reserved, idTag must match reservation
        res_id = None
        if self.everest_manager.state == "RESERVED":
            if (
                self.everest_auth.last_credential
                and str(self.everest_auth.last_credential) != str(id_tag)
            ):
                return {"status": "Rejected"}
            if self.everest_manager.active_reservation_id:
                try:
                    res_id = int(self.everest_manager.active_reservation_id)
                except (ValueError, TypeError):
                    res_id = None

        # 5. Initiate StartTransaction with CSMS before energizing hardware relay
        try:
            start_res = await self.send_start_transaction(
                id_tag=str(id_tag),
                connector_id=target_conn,
                meter_start=int(self.everest_board.energy_kwh * 1000.0),
                reservation_id=res_id
            )
        except Exception as e:
            logger.error(f"[OCPPBridge] Error starting transaction on RemoteStart: {e}")
            return {"status": "Rejected"}

        # Verify transaction actually started
        tx_id = start_res.get("transactionId") if isinstance(start_res, dict) else None
        tag_info = start_res.get("idTagInfo", {}) if isinstance(start_res, dict) else {}
        if not tx_id or (tag_info and tag_info.get("status") not in ["Accepted", None]):
            return {"status": "Rejected"}

        # Record authenticated remote start in EVerest Auth
        self.everest_auth.record_auth_event("CSMS_REMOTE", str(id_tag), "ACCEPTED")

        # 6. Publish command on MQTT to energize relay and transition hardware to charging
        if self.mqtt_svc:
            if hasattr(self.mqtt_svc, "publish_cmd"):
                await self.mqtt_svc.publish_cmd(
                    station_id=self.station_id,
                    connector_id=self.connector_id,
                    action="AUTH_DECISION",
                    status="ACCEPTED",
                    status_code=200,
                    auth_window_duration_ms=30000,
                    relay_action="ENERGIZE"
                )
            elif hasattr(self.mqtt_svc, "publish"):
                cmd_topic = f"evsarthi/{self.station_id}/{self.connector_id}/cmd"
                await self.mqtt_svc.publish(cmd_topic, {
                    "action": "AUTH_DECISION",
                    "status": "ACCEPTED",
                    "status_code": 200,
                    "auth_window_duration_ms": 30000,
                    "relay_action": "ENERGIZE"
                })

        return {"status": "Accepted"}

    async def handle_remote_stop_transaction(self, payload: dict) -> dict:
        """Handles CSMS RemoteStopTransaction."""
        tx_id = payload.get("transactionId")
        if (
            not tx_id
            or self.everest_manager.active_transaction_id is None
            or str(self.everest_manager.active_transaction_id) != str(tx_id)
        ):
            return {"status": "Rejected"}

        # De-energize relay
        if self.mqtt_svc:
            if hasattr(self.mqtt_svc, "publish_cmd"):
                await self.mqtt_svc.publish_cmd(
                    station_id=self.station_id,
                    connector_id=self.connector_id,
                    action="AUTH_DECISION",
                    status="DENIED",
                    status_code=200,
                    relay_action="DE_ENERGIZE"
                )
            elif hasattr(self.mqtt_svc, "publish"):
                cmd_topic = f"evsarthi/{self.station_id}/{self.connector_id}/cmd"
                await self.mqtt_svc.publish(cmd_topic, {
                    "action": "AUTH_DECISION",
                    "status": "DENIED",
                    "status_code": 200,
                    "relay_action": "DE_ENERGIZE"
                })

        await self.send_stop_transaction(transaction_id=tx_id, reason="Remote")
        return {"status": "Accepted"}

    async def handle_unlock_connector(self, payload: dict) -> dict:
        """Handles CSMS UnlockConnector."""
        conn_id = payload.get("connectorId", 1)
        if str(conn_id) not in [str(self.connector_id), "1"]:
            return {"status": "NotSupported"}
        return {"status": "Unlocked"}

    async def handle_reset(self, payload: dict) -> dict:
        """Handles CSMS Reset (Soft or Hard reboot)."""
        reset_type = payload.get("type", "Soft")
        self.everest_manager.state = "AVAILABLE"
        self.everest_manager.active_reservation_id = None
        self.everest_manager.active_transaction_id = None
        self.everest_board.relay_state = "DE_ENERGIZED"
        logger.info(f"[OCPPBridge] Executed {reset_type} reset")
        return {"status": "Accepted"}

    # ── EVerest Taxonomy & State Introspection ────────────────────────────────

    def get_everest_status(self) -> dict:
        """Returns consolidated LF Energy EVerest module taxonomy dictionary."""
        return {
            "evse_board_support": self.everest_board.to_dict(),
            "auth": self.everest_auth.to_dict(),
            "evse_manager": self.everest_manager.to_dict(),
            "system_ui": self.everest_ui.to_dict(),
            "ocpp": {
                "charge_box_id": self.charge_box_id,
                "mode": self.mode,
                "connected": self._connected,
                "boot_accepted": self._boot_accepted,
                "heartbeat_interval": self._heartbeat_interval,
                "pending_calls": len(self._pending_calls),
                "active_transaction_id": self.everest_manager.active_transaction_id
            }
        }

    # ── Backwards Compatibility Translators ───────────────────────────────────

    @classmethod
    def translate_state_to_status_notification(
        cls,
        state_payload: dict,
        station_id: str = "ST001"
    ) -> dict:
        """
        Translates EV Sarthi MQTT state payload into OCPP 1.6-J StatusNotification.
        Maintains 100% backwards compatibility with OCPPEngine for existing tests.
        """
        if OCPPEngine:
            return OCPPEngine.translate_state_to_status_notification(state_payload, station_id)

        raw_state = str(state_payload.get("state", "AVAILABLE")).upper()
        ocpp_status = cls.STATUS_MAP.get(raw_state, "Other")
        fault = state_payload.get("fault_code")
        error_code = cls.ERROR_CODE_MAP.get(fault, "OtherError" if raw_state == "FAULTED" else "NoError")

        ts_val = state_payload.get("timestamp")
        iso_timestamp = current_iso_z(ts_val) if isinstance(ts_val, (int, float)) else current_iso_z()
        connector_id = int(state_payload.get("connector_id", 1))

        return {
            "connectorId": connector_id,
            "errorCode": error_code,
            "status": ocpp_status,
            "timestamp": iso_timestamp,
            "info": state_payload.get("transition_reason", ""),
            "vendorId": "EV_SARTHI",
            "vendorErrorCode": str(fault) if fault else ""
        }

    @classmethod
    def translate_telemetry_to_meter_values(
        cls,
        telemetry_payload: dict,
        station_id: str = "ST001",
        transaction_id: Optional[Union[int, str]] = None
    ) -> dict:
        """
        Translates EV Sarthi MQTT telemetry payload into OCPP 1.6-J MeterValues.
        Maintains 100% backwards compatibility with OCPPEngine for existing tests.
        """
        if OCPPEngine:
            return OCPPEngine.translate_telemetry_to_meter_values(telemetry_payload, station_id, transaction_id)

        connector_id = int(telemetry_payload.get("connector_id", 1))
        elec = telemetry_payload.get("electrical", {})
        energy_data = telemetry_payload.get("energy", {})

        v_val = float(telemetry_payload.get("voltage_v", elec.get("voltage_v", 0.0)))
        c_val = float(telemetry_payload.get("current_a", elec.get("current_a", 0.0)))
        p_val = float(telemetry_payload.get("power_kw", elec.get("power_kw", round((v_val * c_val) / 1000.0, 3))))
        e_val = float(telemetry_payload.get("energy_kwh", energy_data.get("energy_kwh", 0.0)))

        ts_val = telemetry_payload.get("timestamp")
        iso_timestamp = current_iso_z(ts_val) if isinstance(ts_val, (int, float)) else current_iso_z()

        sampled_values = [
            {
                "value": f"{v_val:.1f}",
                "context": "Sample.Periodic",
                "format": "Raw",
                "measurand": "Voltage",
                "phase": "L1",
                "location": "Outlet",
                "unit": "V"
            },
            {
                "value": f"{c_val:.2f}",
                "context": "Sample.Periodic",
                "format": "Raw",
                "measurand": "Current.Import",
                "phase": "L1",
                "location": "Outlet",
                "unit": "A"
            },
            {
                "value": f"{p_val:.3f}",
                "context": "Sample.Periodic",
                "format": "Raw",
                "measurand": "Power.Active.Import",
                "location": "Outlet",
                "unit": "kW"
            },
            {
                "value": f"{e_val:.4f}",
                "context": "Sample.Periodic",
                "format": "Raw",
                "measurand": "Energy.Active.Import.Register",
                "location": "Outlet",
                "unit": "kWh"
            }
        ]

        payload = {
            "connectorId": connector_id,
            "meterValue": [
                {
                    "timestamp": iso_timestamp,
                    "sampledValue": sampled_values
                }
            ]
        }
        if transaction_id is not None:
            try:
                payload["transactionId"] = int(transaction_id)
            except (ValueError, TypeError):
                payload["transactionId"] = transaction_id

        return payload


# Global bridge singleton for optional FastAPI module import
ocpp_bridge = OCPPBridge()
