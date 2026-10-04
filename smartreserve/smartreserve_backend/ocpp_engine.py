import asyncio
import time

class OCPPEngine:
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
