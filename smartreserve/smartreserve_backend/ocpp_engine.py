import asyncio
import time
from typing import Optional, Union, Dict, Any, List

class OCPPEngine:
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

    def __init__(self):
        self.ocpp_log = []
        
    def _log(self, action: str, station_id: str, details: dict):
        log_entry = {
            "timestamp": time.time(),
            "action": action,
            "station_id": station_id,
            "details": details
        }
        self.ocpp_log.append(log_entry)
        if len(self.ocpp_log) > 100:
            self.ocpp_log.pop(0)

    async def reserve_now(self, station_id: str, reservation_id: str, expiry_timestamp: float, connector_id: int = 1) -> dict:
        await asyncio.sleep(0.05)
        self._log("ReserveNow", station_id, {"reservation_id": reservation_id, "expiry": expiry_timestamp, "connector_id": connector_id})
        return {"status": "Accepted", "message": f"Reserved connector {connector_id} at {station_id}"}

    async def remote_start_transaction(self, station_id: str, id_tag: str, connector_id: int = 1) -> dict:
        await asyncio.sleep(0.05)
        self._log("RemoteStartTransaction", station_id, {"id_tag": id_tag, "connector_id": connector_id})
        return {"status": "Accepted"}

    async def unlock_connector(self, station_id: str, connector_id: int = 1) -> dict:
        await asyncio.sleep(0.05)
        self._log("UnlockConnector", station_id, {"connector_id": connector_id})
        return {"status": "Unlocked"}

    async def remote_stop_transaction(self, station_id: str, transaction_id: str) -> dict:
        await asyncio.sleep(0.05)
        self._log("RemoteStopTransaction", station_id, {"transaction_id": transaction_id})
        return {"status": "Accepted"}

    async def cancel_reservation(self, station_id: str, reservation_id: str) -> dict:
        await asyncio.sleep(0.05)
        self._log("CancelReservation", station_id, {"reservation_id": reservation_id})
        return {"status": "Accepted"}

    async def get_diagnostics(self, station_id: str) -> dict:
        await asyncio.sleep(0.05)
        self._log("GetDiagnostics", station_id, {})
        return {"status": "Uploaded", "message": "Diagnostics upload initiated"}

    def get_log(self) -> list:
        return self.ocpp_log

    # ── New OCPP 1.6J Message Translation Routines ──────────────────────────

    @classmethod
    def translate_state_to_status_notification(
        cls,
        state_payload: dict,
        station_id: str = "ST001"
    ) -> dict:
        """
        Translates an EV Sarthi MQTT state payload into an OCPP 1.6-J StatusNotification payload.
        Maps AVAILABLE->Available, RESERVED->Reserved, CHARGING->Charging, FAULTED->Faulted.
        Maps fault_code to OCPP ChargePointErrorCode.
        """
        raw_state = str(state_payload.get("state", "AVAILABLE")).upper()
        ocpp_status = cls.STATUS_MAP.get(raw_state, "Other")
        
        fault = state_payload.get("fault_code")
        error_code = cls.ERROR_CODE_MAP.get(fault, "OtherError" if raw_state == "FAULTED" else "NoError")

        timestamp_val = state_payload.get("timestamp")
        if isinstance(timestamp_val, (int, float)):
            iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp_val))
        else:
            iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

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
        Translates an EV Sarthi MQTT telemetry payload into an OCPP 1.6-J MeterValues payload.
        Extracts Voltage (V), Current.Import (A), Power.Active.Import (kW), and Energy (kWh).
        """
        connector_id = int(telemetry_payload.get("connector_id", 1))

        elec = telemetry_payload.get("electrical", {})
        energy_data = telemetry_payload.get("energy", {})

        v_val = float(telemetry_payload.get("voltage_v", elec.get("voltage_v", 0.0)))
        c_val = float(telemetry_payload.get("current_a", elec.get("current_a", 0.0)))
        p_val = float(telemetry_payload.get("power_kw", elec.get("power_kw", round((v_val * c_val) / 1000.0, 3))))
        e_val = float(telemetry_payload.get("energy_kwh", energy_data.get("energy_kwh", 0.0)))

        timestamp_val = telemetry_payload.get("timestamp")
        if isinstance(timestamp_val, (int, float)):
            iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp_val))
        else:
            iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

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

        tx_id = None
        if transaction_id is not None:
            try:
                tx_id = int(transaction_id)
            except (ValueError, TypeError):
                tx_id = transaction_id

        meter_values_payload = {
            "connectorId": connector_id,
            "meterValue": [
                {
                    "timestamp": iso_timestamp,
                    "sampledValue": sampled_values
                }
            ]
        }
        if tx_id is not None:
            meter_values_payload["transactionId"] = tx_id

        return meter_values_payload

    # ── New OCPP Engine Ingestion & Dispatch Methods ─────────────────────────

    async def status_notification(
        self,
        station_id: str,
        connector_id: int,
        status: str,
        error_code: str = "NoError",
        info: str = "",
        timestamp: Optional[str] = None,
        vendor_error_code: Optional[str] = None
    ) -> dict:
        """Processes and logs an OCPP 1.6-J StatusNotification call."""
        await asyncio.sleep(0.01)
        details = {
            "connector_id": connector_id,
            "status": status,
            "error_code": error_code,
            "info": info,
            "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        if vendor_error_code:
            details["vendor_error_code"] = vendor_error_code
        self._log("StatusNotification", station_id, details)
        return {"status": "Accepted"}

    async def meter_values(
        self,
        station_id: str,
        connector_id: int,
        meter_values: list,
        transaction_id: Optional[Union[int, str]] = None
    ) -> dict:
        """Processes and logs an OCPP 1.6-J MeterValues call."""
        await asyncio.sleep(0.01)
        details = {
            "connector_id": connector_id,
            "meter_value": meter_values,
        }
        if transaction_id is not None:
            details["transaction_id"] = transaction_id
        self._log("MeterValues", station_id, details)
        return {"status": "Accepted"}

    async def send_status_notification(
        self,
        station_id: str,
        state_payload: dict
    ) -> dict:
        """Translates MQTT state payload and records OCPP StatusNotification."""
        ocpp_req = self.translate_state_to_status_notification(state_payload, station_id)
        return await self.status_notification(
            station_id=station_id,
            connector_id=ocpp_req["connectorId"],
            status=ocpp_req["status"],
            error_code=ocpp_req["errorCode"],
            info=ocpp_req["info"],
            timestamp=ocpp_req["timestamp"],
            vendor_error_code=ocpp_req.get("vendorErrorCode")
        )

    async def send_meter_values(
        self,
        station_id: str,
        telemetry_payload: dict,
        transaction_id: Optional[Union[int, str]] = None
    ) -> dict:
        """Translates MQTT telemetry payload and records OCPP MeterValues."""
        ocpp_req = self.translate_telemetry_to_meter_values(telemetry_payload, station_id, transaction_id)
        return await self.meter_values(
            station_id=station_id,
            connector_id=ocpp_req["connectorId"],
            meter_values=ocpp_req["meterValue"],
            transaction_id=ocpp_req.get("transactionId")
        )
