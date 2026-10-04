"""
Hyundai SmartReserve — FastAPI Backend
Production-level server for EV charging reservation system
"""
import asyncio
import csv
import json
import os
import random
import string
import time
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from websocket_manager import WebSocketManager
from stations_db import StationsDB

app = FastAPI(title="Hyundai SmartReserve API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
frontend_path = os.path.join(os.path.dirname(__file__), "..", "smartreserve_frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

ws_manager = WebSocketManager()
db = StationsDB()

# In-memory reservation store
reservations: dict = {}
charging_sessions: dict = {}


# ─── Models ───────────────────────────────────────────────────────────────────

class ReserveRequest(BaseModel):
    station_id: str
    duration_minutes: int = 30
    user_id: str = "hyundai_driver_001"
    vehicle_soc: float = 18.0

class VerifyOTPRequest(BaseModel):
    station_id: str
    pin: str
    kiosk_id: Optional[str] = None

class ChargingUpdateRequest(BaseModel):
    station_id: str
    soc: float
    kw: float
    kwh: float


# ─── Utility ──────────────────────────────────────────────────────────────────

def generate_pin() -> str:
    return "".join(random.choices(string.digits, k=6))

def get_congestion_score(station_id: str) -> dict:
    """Simulate GNN congestion prediction. In production, loads roland_final_weights."""
    seed = sum(ord(c) for c in station_id)
    random.seed(seed)
    score = random.uniform(0, 1)
    random.seed()  # reset
    if score < 0.33:
        return {"level": "LOW", "queue_min": 0, "queue_max": 5, "confidence": "HIGH"}
    elif score < 0.66:
        return {"level": "MEDIUM", "queue_min": 10, "queue_max": 25, "confidence": "MEDIUM"}
    else:
        return {"level": "HIGH", "queue_min": 30, "queue_max": 60, "confidence": "LOW"}


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "Hyundai SmartReserve API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "stations_loaded": len(db.stations),
        "active_reservations": len(reservations),
        "active_sessions": len(charging_sessions),
    }

@app.get("/api/stations")
async def get_stations(
    district: Optional[str] = None,
    connector_type: Optional[str] = None,
    min_power_kw: Optional[float] = None,
    limit: int = 200,
):
    """Return stations with live status and AI congestion predictions."""
    stations = db.get_stations(district=district, limit=limit)
    result = []
    for s in stations:
        sid = s["station_id"]
        reservation = reservations.get(sid)
        is_reserved = (
            reservation is not None
            and reservation["expires_at"] > time.time()
        )
        is_charging = sid in charging_sessions

        if is_charging:
            status = "CHARGING"
        elif is_reserved:
            status = "RESERVED"
        else:
            # Randomly mark ~15% as unavailable (broken/occupied)
            seed_val = sum(ord(c) for c in sid) % 100
            status = "UNAVAILABLE" if seed_val < 15 else "AVAILABLE"

        congestion = get_congestion_score(sid)
        result.append({
            **s,
            "status": status,
            "congestion": congestion,
            "is_reserved": is_reserved,
            "is_charging": is_charging,
        })
    return {"stations": result, "total": len(result), "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/stations/{station_id}")
async def get_station(station_id: str):
    station = db.get_station(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    reservation = reservations.get(station_id)
    is_reserved = reservation and reservation["expires_at"] > time.time()
    congestion = get_congestion_score(station_id)
    return {**station, "congestion": congestion, "is_reserved": bool(is_reserved)}

@app.post("/api/reserve")
async def reserve_station(req: ReserveRequest):
    """Lock a charger slot for 30 minutes via OCPP ReserveNow."""
    station = db.get_station(req.station_id)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    existing = reservations.get(req.station_id)
    if existing and existing["expires_at"] > time.time():
        raise HTTPException(status_code=409, detail="Station already reserved")

    pin = generate_pin()
    expires_at = time.time() + (req.duration_minutes * 60)
    reservation_id = f"RES_{req.station_id}_{int(time.time())}"

    reservations[req.station_id] = {
        "reservation_id": reservation_id,
        "station_id": req.station_id,
        "user_id": req.user_id,
        "pin": pin,
        "duration_minutes": req.duration_minutes,
        "created_at": time.time(),
        "expires_at": expires_at,
        "vehicle_soc": req.vehicle_soc,
    }

    # Push OCPP ReserveNow command to kiosk via WebSocket
    ocpp_cmd = {
        "type": "OCPP_RESERVE_NOW",
        "station_id": req.station_id,
        "reservation_id": reservation_id,
        "expiry": expires_at,
        "pin_hash": pin,  # In production: hash this
        "timestamp": datetime.utcnow().isoformat(),
    }
    await ws_manager.broadcast_to_kiosk(req.station_id, ocpp_cmd)

    # Notify car dashboard of confirmation
    car_update = {
        "type": "RESERVATION_CONFIRMED",
        "station_id": req.station_id,
        "reservation_id": reservation_id,
        "pin": pin,
        "expires_at": expires_at,
        "station_name": station.get("name", ""),
        "station_address": station.get("address", ""),
        "power_kw": station.get("power_kw", 60),
        "eta_minutes": 18,
    }
    await ws_manager.broadcast_to_car(req.user_id, car_update)

    return {
        "success": True,
        "reservation_id": reservation_id,
        "pin": pin,
        "expires_at": expires_at,
        "station": station,
        "message": f"Charger locked for {req.duration_minutes} minutes. Your PIN: {pin}",
    }

@app.post("/api/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    """Verify PIN and unlock charger relay."""
    reservation = reservations.get(req.station_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="No active reservation for this station")

    if reservation["expires_at"] < time.time():
        # No-show: deduct fee, release slot
        del reservations[req.station_id]
        raise HTTPException(status_code=410, detail="Reservation expired. No-show fee applied.")

    if reservation["pin"] != req.pin:
        await ws_manager.broadcast_to_kiosk(req.station_id, {
            "type": "ACCESS_DENIED",
            "station_id": req.station_id,
            "reason": "Wrong PIN",
        })
        raise HTTPException(status_code=401, detail="Wrong PIN. Connector stays locked.")

    # PIN correct — send unlock command via WebSocket
    session_id = f"CHG_{req.station_id}_{int(time.time())}"
    charging_sessions[req.station_id] = {
        "session_id": session_id,
        "station_id": req.station_id,
        "user_id": reservation["user_id"],
        "start_soc": reservation["vehicle_soc"],
        "current_soc": reservation["vehicle_soc"],
        "start_time": time.time(),
        "kw": 60.0,
        "kwh": 0.0,
    }

    unlock_cmd = {
        "type": "OCPP_UNLOCK_RELAY",
        "station_id": req.station_id,
        "session_id": session_id,
        "power_kw": 60.0,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await ws_manager.broadcast_to_kiosk(req.station_id, unlock_cmd)

    # Start sending live meter values to car dashboard
    asyncio.create_task(stream_meter_values(req.station_id, session_id, reservation["user_id"]))

    del reservations[req.station_id]
    return {
        "success": True,
        "session_id": session_id,
        "message": "Identity verified. Relay closed. 60 kW DC charging started.",
    }

@app.post("/api/cancel-reservation/{station_id}")
async def cancel_reservation(station_id: str, user_id: str = "hyundai_driver_001"):
    reservation = reservations.get(station_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="No active reservation")
    if reservation["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not your reservation")
    del reservations[station_id]
    await ws_manager.broadcast_to_kiosk(station_id, {
        "type": "OCPP_CANCEL_RESERVATION",
        "station_id": station_id,
    })
    return {"success": True, "message": "Reservation cancelled. Full refund processed."}

@app.get("/api/session/{station_id}")
async def get_session(station_id: str):
    session = charging_sessions.get(station_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active charging session")
    return session

@app.get("/api/reservations")
async def list_reservations():
    now = time.time()
    active = {k: v for k, v in reservations.items() if v["expires_at"] > now}
    return {"active_reservations": list(active.values()), "count": len(active)}


# ─── WebSocket Endpoints ───────────────────────────────────────────────────────

@app.websocket("/ws/car/{user_id}")
async def car_websocket(websocket: WebSocket, user_id: str):
    await ws_manager.connect_car(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "PING":
                await websocket.send_json({"type": "PONG", "timestamp": time.time()})
    except WebSocketDisconnect:
        ws_manager.disconnect_car(user_id)

@app.websocket("/ws/kiosk/{station_id}")
async def kiosk_websocket(websocket: WebSocket, station_id: str):
    await ws_manager.connect_kiosk(websocket, station_id)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            # Kiosk sending meter values back to cloud
            if msg.get("type") == "METER_VALUES":
                sid = msg.get("station_id", station_id)
                if sid in charging_sessions:
                    charging_sessions[sid]["current_soc"] = msg.get("soc", 0)
                    charging_sessions[sid]["kwh"] = msg.get("kwh", 0)
    except WebSocketDisconnect:
        ws_manager.disconnect_kiosk(station_id)

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """General broadcast channel for the demo dashboard."""
    await ws_manager.connect_dashboard(websocket)
    try:
        while True:
            await asyncio.sleep(5)
            await websocket.send_json({
                "type": "HEARTBEAT",
                "active_reservations": len(reservations),
                "active_sessions": len(charging_sessions),
                "timestamp": datetime.utcnow().isoformat(),
            })
    except WebSocketDisconnect:
        ws_manager.disconnect_dashboard(websocket)


# ─── Background Tasks ─────────────────────────────────────────────────────────

async def stream_meter_values(station_id: str, session_id: str, user_id: str):
    """Simulate live charging meter values pushed to car dashboard."""
    session = charging_sessions.get(station_id)
    if not session:
        return

    soc = session["start_soc"]
    kwh = 0.0
    tick = 0

    while station_id in charging_sessions and soc < 100:
        await asyncio.sleep(2)
        tick += 1

        # Taper curve: fast to 80%, slower after
        if soc < 80:
            kw = 60.0 - (soc * 0.2)
        else:
            kw = max(5.0, 60.0 - (soc * 0.65))

        soc_delta = (kw / 40.0) * (2 / 3600) * 100  # rough SoC gain per tick
        soc = min(100, soc + soc_delta)
        kwh += kw * (2 / 3600)

        charging_sessions[station_id]["current_soc"] = round(soc, 1)
        charging_sessions[station_id]["kw"] = round(kw, 1)
        charging_sessions[station_id]["kwh"] = round(kwh, 2)

        meter_msg = {
            "type": "METER_VALUES",
            "station_id": station_id,
            "session_id": session_id,
            "soc": round(soc, 1),
            "kw": round(kw, 1),
            "kwh": round(kwh, 2),
            "elapsed_minutes": round(tick * 2 / 60, 1),
            "timestamp": datetime.utcnow().isoformat(),
        }
        await ws_manager.broadcast_to_car(user_id, meter_msg)
        await ws_manager.broadcast_to_kiosk(station_id, meter_msg)

        if soc >= 80 and tick % 30 == 0:
            # Notify finishing state at 80%
            await ws_manager.broadcast_to_kiosk(station_id, {
                "type": "CHARGING_FINISHING",
                "station_id": station_id,
                "soc": round(soc, 1),
            })

    # Session ended
    if station_id in charging_sessions:
        del charging_sessions[station_id]

    await ws_manager.broadcast_to_car(user_id, {
        "type": "CHARGING_COMPLETE",
        "station_id": station_id,
        "final_soc": round(soc, 1),
        "total_kwh": round(kwh, 2),
        "message": f"Charging complete at {round(soc, 1)}% SoC. Please unplug.",
    })
    await ws_manager.broadcast_to_kiosk(station_id, {
        "type": "CHARGING_COMPLETE",
        "station_id": station_id,
        "final_soc": round(soc, 1),
    })

async def cleanup_expired_reservations():
    """Periodically release expired reservations."""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        expired = [sid for sid, r in reservations.items() if r["expires_at"] < now]
        for sid in expired:
            await ws_manager.broadcast_to_kiosk(sid, {
                "type": "OCPP_RESERVATION_EXPIRED",
                "station_id": sid,
                "reason": "Timer expired. No-show fee applied.",
            })
            del reservations[sid]

@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_expired_reservations())
    print("✅ Hyundai SmartReserve API started")
    print(f"   Stations loaded: {len(db.stations)}")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
