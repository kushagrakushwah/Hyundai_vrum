"""
WebSocket Manager — handles all real-time connections
Car dashboards, kiosk simulators, and admin dashboards
"""
import json
from typing import Dict, List, Set
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        # user_id -> WebSocket
        self.car_connections: Dict[str, WebSocket] = {}
        # station_id -> WebSocket
        self.kiosk_connections: Dict[str, WebSocket] = {}
        # general dashboard connections
        self.dashboard_connections: Set[WebSocket] = set()

    # ─── Car Dashboard ────────────────────────────────────────────────────────

    async def connect_car(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.car_connections[user_id] = websocket
        print(f"  [WS] Car dashboard connected: {user_id}")
        await websocket.send_json({
            "type": "CONNECTED",
            "user_id": user_id,
            "message": "SmartReserve connected. AI monitoring active.",
        })

    def disconnect_car(self, user_id: str):
        self.car_connections.pop(user_id, None)
        print(f"  [WS] Car dashboard disconnected: {user_id}")

    async def broadcast_to_car(self, user_id: str, message: dict):
        ws = self.car_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"  [WS] Error sending to car {user_id}: {e}")
                self.disconnect_car(user_id)

    # ─── Charger Kiosk ────────────────────────────────────────────────────────

    async def connect_kiosk(self, websocket: WebSocket, station_id: str):
        await websocket.accept()
        self.kiosk_connections[station_id] = websocket
        print(f"  [WS] Kiosk connected: {station_id}")
        await websocket.send_json({
            "type": "OCPP_BOOT_NOTIFICATION",
            "station_id": station_id,
            "status": "Accepted",
            "current_time": __import__("datetime").datetime.utcnow().isoformat(),
            "heartbeat_interval": 30,
        })

    def disconnect_kiosk(self, station_id: str):
        self.kiosk_connections.pop(station_id, None)
        print(f"  [WS] Kiosk disconnected: {station_id}")

    async def broadcast_to_kiosk(self, station_id: str, message: dict):
        ws = self.kiosk_connections.get(station_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"  [WS] Error sending to kiosk {station_id}: {e}")
                self.disconnect_kiosk(station_id)

    # ─── Admin Dashboard ──────────────────────────────────────────────────────

    async def connect_dashboard(self, websocket: WebSocket):
        await websocket.accept()
        self.dashboard_connections.add(websocket)
        print("  [WS] Admin dashboard connected")

    def disconnect_dashboard(self, websocket: WebSocket):
        self.dashboard_connections.discard(websocket)

    async def broadcast_to_all(self, message: dict):
        """Broadcast to all connected clients."""
        dead = set()
        for ws in self.dashboard_connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.dashboard_connections -= dead

    @property
    def stats(self) -> dict:
        return {
            "car_connections": len(self.car_connections),
            "kiosk_connections": len(self.kiosk_connections),
            "dashboard_connections": len(self.dashboard_connections),
        }
