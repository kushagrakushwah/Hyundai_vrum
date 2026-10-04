"""
OCPP 1.6 Engine — Open Charge Point Protocol command builder
Implements the official OCPP 1.6 JSON message format
"""
import time
import uuid
from datetime import datetime, timezone
from typing import Optional


def ocpp_call(action: str, payload: dict) -> list:
    """Build OCPP [2, UniqueId, Action, Payload] Call frame."""
    return [2, str(uuid.uuid4()), action, payload]


def ocpp_callresult(unique_id: str, payload: dict) -> list:
    """Build OCPP [3, UniqueId, Payload] CallResult frame."""
    return [3, unique_id, payload]


def ocpp_callerror(unique_id: str, error_code: str, description: str) -> list:
    """Build OCPP [4, UniqueId, ErrorCode, ErrorDescription, ErrorDetails] frame."""
    return [4, unique_id, error_code, description, {}]


# ─── OCPP 1.6 Commands ────────────────────────────────────────────────────────

def reserve_now(
    connector_id: int,
    expiry_date: str,
    id_tag: str,
    reservation_id: int,
    parent_id_tag: Optional[str] = None,
) -> list:
    """
    OCPP 1.6 ReserveNow command.
    Tells charger to lock a connector until expiry or correct idTag presented.
    """
    payload = {
        "connectorId": connector_id,
        "expiryDate": expiry_date,
        "idTag": id_tag,
        "reservationId": reservation_id,
    }
    if parent_id_tag:
        payload["parentIdTag"] = parent_id_tag
    return ocpp_call("ReserveNow", payload)


def cancel_reservation(reservation_id: int) -> list:
    """OCPP 1.6 CancelReservation command."""
    return ocpp_call("CancelReservation", {"reservationId": reservation_id})


def remote_start_transaction(id_tag: str, connector_id: int = 1) -> list:
    """OCPP 1.6 RemoteStartTransaction — triggers charging after unlock."""
    return ocpp_call("RemoteStartTransaction", {
        "connectorId": connector_id,
        "idTag": id_tag,
    })


def remote_stop_transaction(transaction_id: int) -> list:
    """OCPP 1.6 RemoteStopTransaction."""
    return ocpp_call("RemoteStopTransaction", {
        "transactionId": transaction_id,
    })


def change_availability(connector_id: int, availability_type: str) -> list:
    """OCPP 1.6 ChangeAvailability — Operative or Inoperative."""
    return ocpp_call("ChangeAvailability", {
        "connectorId": connector_id,
        "type": availability_type,  # "Operative" | "Inoperative"
    })


def get_configuration(keys: Optional[list] = None) -> list:
    """OCPP 1.6 GetConfiguration."""
    payload = {}
    if keys:
        payload["key"] = keys
    return ocpp_call("GetConfiguration", payload)


def trigger_message(requested_message: str, connector_id: Optional[int] = None) -> list:
    """OCPP 1.6 TriggerMessage — ask charger to send a specific message."""
    payload = {"requestedMessage": requested_message}
    if connector_id is not None:
        payload["connectorId"] = connector_id
    return ocpp_call("TriggerMessage", payload)


# ─── Charger State Machine ────────────────────────────────────────────────────

class ChargerState:
    AVAILABLE = "Available"
    RESERVED = "Reserved"
    CHARGING = "Charging"
    FINISHING = "Finishing"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"
    PREPARING = "Preparing"
    SUSPENDED_EV = "SuspendedEV"
    SUSPENDED_EVSE = "SuspendedEVSE"


class ConnectorStatus:
    """Maps OCPP status to display state."""
    STATUS_MAP = {
        ChargerState.AVAILABLE: {
            "led": "green",
            "message": "60 kW DC Available — Plug in or tap RFID",
            "power": "Ready",
            "relay": False,
        },
        ChargerState.RESERVED: {
            "led": "blue",
            "message": "RESERVED for Hyundai Driver — Enter 6-digit PIN",
            "power": "0 kW",
            "relay": False,
        },
        ChargerState.CHARGING: {
            "led": "green-solid",
            "message": "Charging Active",
            "power": "60 kW",
            "relay": True,
        },
        ChargerState.FINISHING: {
            "led": "amber",
            "message": "Charging complete at 80%. Please unplug to free connector.",
            "power": "Tapering",
            "relay": True,
        },
        ChargerState.UNAVAILABLE: {
            "led": "grey",
            "message": "Station Unavailable — Contact support",
            "power": "0 kW",
            "relay": False,
        },
        ChargerState.FAULTED: {
            "led": "red-blink",
            "message": "⚠ Fault Detected — Reservation cancelled, full refund issued",
            "power": "0 kW",
            "relay": False,
        },
    }

    @classmethod
    def get(cls, state: str) -> dict:
        return cls.STATUS_MAP.get(state, cls.STATUS_MAP[ChargerState.UNAVAILABLE])
