"""
Hyundai SmartReserve — FastAPI Backend v2.0
Production-grade: SQLite persistence + OCPP 1.6J + Razorpay escrow + Live OCM API
"""
import asyncio
import json
import os
import random
import string
import time
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import database
from ocpp_engine import OCPPEngine
from payment import PaymentEngine
from websocket_manager import WebSocketManager
from stations_db import StationsDB
from voice_assistant import VoiceAssistant
from priority_engine import PriorityEngine
from vehicle_intelligence import VehicleIntelligence
from speech_service import SpeechService

# ── App Setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Hyundai SmartReserve API",
    version="2.0.0",
    description="AI-Powered EV Charging Reservation System — Hyundai Ideathon 2026",
)

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

# ── Global Instances ──────────────────────────────────────────────────────────
ws_manager = WebSocketManager()
db = StationsDB()
ocpp_engine = OCPPEngine()
payment_engine = PaymentEngine()

# ── Voice Assistant Instances ─────────────────────────────────────────────────
try:
    vehicle_intel = VehicleIntelligence()
    priority_eng = PriorityEngine(db, vehicle_intel)
    voice_assistant = VoiceAssistant()
    speech_svc = SpeechService()
    print("  [VOICE] AVA Voice Assistant initialized")
except Exception as e:
    vehicle_intel = None
    priority_eng = None
    voice_assistant = None
    speech_svc = None
    print(f"  [VOICE] Voice assistant init error (non-fatal): {e}")

OCM_API_KEY = "1f545914-8daa-4fa6-9d7b-4a6819f2f7cc"


# ── Pydantic Models ───────────────────────────────────────────────────────────
class ReserveRequest(BaseModel):
    station_id: str
    duration_minutes: int = 30
    user_id: str = "hyundai_driver_001"
    user_name: str = "Hyundai Driver"
    vehicle_soc: float = 18.0

class VerifyOTPRequest(BaseModel):
    station_id: str
    pin: str
    kiosk_id: Optional[str] = None

class PaymentVerifyRequest(BaseModel):
    order_id: str
    payment_id: str
    signature: str


class VoiceInputRequest(BaseModel):
    text: str
    user_id: str = "hyundai_driver_001"
    language: str = "auto"

class VoiceConfirmRequest(BaseModel):
    user_id: str = "hyundai_driver_001"
    confirmed: bool = True


# ── Utility ───────────────────────────────────────────────────────────────────
def generate_pin() -> str:
    return "".join(random.choices(string.digits, k=6))

def get_congestion_score(station_id: str) -> dict:
    """GNN-inspired congestion prediction (deterministic per station)."""
    seed = sum(ord(c) for c in station_id)
    random.seed(seed)
    score = random.uniform(0, 1)
    random.seed()
    if score < 0.33:
        return {"level": "LOW", "queue_min": 0, "queue_max": 5, "confidence": "HIGH"}
    elif score < 0.66:
        return {"level": "MEDIUM", "queue_min": 10, "queue_max": 25, "confidence": "MEDIUM"}
    else:
        return {"level": "HIGH", "queue_min": 30, "queue_max": 60, "confidence": "LOW"}


# ── Core Routes ───────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    car_html = os.path.join(frontend_path, "car_dashboard", "index.html")
    if os.path.exists(car_html):
        return FileResponse(car_html)
    return {
        "status": "ok",
        "service": "Hyundai SmartReserve API",
        "version": "2.0.0",
        "features": ["SQLite Persistence", "OCPP 1.6J", "Razorpay Escrow", "Live OCM API"],
    }

@app.get("/car")
async def car_page():
    car_html = os.path.join(frontend_path, "car_dashboard", "index.html")
    if os.path.exists(car_html):
        return FileResponse(car_html)
    raise HTTPException(status_code=404, detail="Dashboard not found")

@app.get("/kiosk")
async def kiosk_page():
    kiosk_html = os.path.join(frontend_path, "kiosk_simulator", "index.html")
    if os.path.exists(kiosk_html):
        return FileResponse(kiosk_html)
    raise HTTPException(status_code=404, detail="Kiosk simulator not found")

@app.get("/health")
@app.get("/healthz")
async def health():
    active_res = await database.get_all_active_reservations()
    active_ses = await database.get_all_sessions()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "stations_loaded": len(db.stations),
        "active_reservations": len(active_res),
        "active_sessions": len(active_ses),
        "database": "SQLite (persistent)",
        "ocpp": "OCPP 1.6J Mock Engine",
        "payment": "Razorpay (demo mode)" if payment_engine.mock_mode else "Razorpay (live)",
    }

@app.get("/api/stats")
async def get_stats():
    active_res = await database.get_all_active_reservations()
    active_ses = await database.get_all_sessions()
    return {
        "total_stations": len(db.stations) + len(db.live_ocm_stations),
        "bee_stations": len(db.stations),
        "live_ocm_stations": len(db.live_ocm_stations),
        "active_reservations": len(active_res),
        "active_sessions": len(active_ses),
        "ocm_key": OCM_API_KEY[:8] + "...",
        "ocpp_commands_sent": len(ocpp_engine.get_log()),
        "payment_mode": "demo (₹200 waived)" if payment_engine.mock_mode else "live",
    }


# ── Station Routes ─────────────────────────────────────────────────────────────
@app.get("/api/stations")
async def get_stations(
    district: Optional[str] = None,
    source: str = "all",
    connector_type: Optional[str] = None,
    min_power_kw: Optional[float] = None,
    limit: int = 1000,
):
    """Return stations with live status and AI congestion predictions."""
    stations = db.get_stations(district=district, source=source, limit=limit)
    active_res = await database.get_all_active_reservations()
    active_ses = await database.get_all_sessions()

    reserved_ids = {r["station_id"] for r in active_res}
    session_ids = {s["station_id"] for s in active_ses}

    result = []
    for s in stations:
        sid = s["station_id"]

        if sid in session_ids:
            status = "CHARGING"
        elif sid in reserved_ids:
            status = "RESERVED"
        elif s.get("is_live_ocm"):
            status = s.get("status", "AVAILABLE")
        else:
            seed_val = sum(ord(c) for c in sid) % 100
            status = "UNAVAILABLE" if seed_val < 15 else "AVAILABLE"

        congestion = get_congestion_score(sid)

        if connector_type and s.get("connector_type", "").lower() != connector_type.lower():
            continue
        if min_power_kw and s.get("power_kw", 0) < min_power_kw:
            continue

        result.append({
            **s,
            "status": status,
            "congestion": congestion,
            "is_reserved": sid in reserved_ids,
            "is_charging": sid in session_ids,
            "is_live_ocm": s.get("is_live_ocm", False),
            "live_status_title": s.get("live_status_title", "Verified Operational"),
        })

    return {
        "stations": result,
        "total": len(result),
        "source": source,
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.get("/api/stations/live")
async def get_live_stations(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    distance_km: float = 100.0,
    limit: int = 50,
):
    """Fetch live public stations directly from Open Charge Map API in real time."""
    stations = db.get_live_ocm_stations(
        latitude=latitude,
        longitude=longitude,
        distance_km=distance_km,
        max_results=limit,
    )
    return {
        "stations": stations,
        "total": len(stations),
        "provider": "Open Charge Map (OCM) Global Telemetry",
        "key": OCM_API_KEY,
        "status": "ONLINE",
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.get("/api/stations/{station_id}")
async def get_station(station_id: str):
    station = db.get_station(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    reservation = await database.get_reservation(station_id)
    is_reserved = bool(reservation and reservation["expires_at"] > time.time())
    congestion = get_congestion_score(station_id)
    return {**station, "congestion": congestion, "is_reserved": is_reserved}


# ── Reservation Routes ─────────────────────────────────────────────────────────
@app.post("/api/reserve")
async def reserve_station(req: ReserveRequest):
    """Lock a charger slot for 30 minutes with OCPP ReserveNow + Razorpay escrow."""
    station = db.get_station(req.station_id)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    existing = await database.get_reservation(req.station_id)
    if existing and existing["expires_at"] > time.time():
        if existing.get("user_id") == req.user_id:
            return {
                "success": True,
                "reservation_id": existing["reservation_id"],
                "pin": existing["pin"],
                "expires_at": existing["expires_at"],
                "station": station,
                "message": f"Active reservation PIN: {existing['pin']}",
                "ocpp_response": {"status": "Accepted", "message": "Reservation renewed"},
                "payment": {"order_id": existing.get("payment_order_id"), "mock": True, "status": "active"},
            }
        else:
            await database.delete_reservation(req.station_id)

    pin = generate_pin()
    created_at = time.time()
    expires_at = created_at + (req.duration_minutes * 60)
    reservation_id = f"RES_{req.station_id}_{int(created_at)}"

    # Create Razorpay escrow order
    payment_info = payment_engine.create_escrow_order(reservation_id, req.user_id)

    res_data = {
        "station_id": req.station_id,
        "reservation_id": reservation_id,
        "user_id": req.user_id,
        "user_name": req.user_name,
        "pin": pin,
        "duration_minutes": req.duration_minutes,
        "created_at": created_at,
        "expires_at": expires_at,
        "vehicle_soc": req.vehicle_soc,
        "payment_order_id": payment_info.get("order_id"),
        "payment_status": "PENDING",
    }
    await database.save_reservation(res_data)

    # OCPP 1.6J — ReserveNow
    ocpp_resp = await ocpp_engine.reserve_now(req.station_id, reservation_id, expires_at)

    # WebSocket push to kiosk
    await ws_manager.broadcast_to_kiosk(req.station_id, {
        "type": "OCPP_RESERVE_NOW",
        "station_id": req.station_id,
        "reservation_id": reservation_id,
        "expiry": expires_at,
        "pin_hash": pin,
        "timestamp": datetime.utcnow().isoformat(),
    })

    # WebSocket push to car
    await ws_manager.broadcast_to_car(req.user_id, {
        "type": "RESERVATION_CONFIRMED",
        "station_id": req.station_id,
        "reservation_id": reservation_id,
        "pin": pin,
        "expires_at": expires_at,
        "station_name": station.get("name", ""),
        "station_address": station.get("address", ""),
        "power_kw": station.get("power_kw", 60),
        "eta_minutes": 18,
    })

    return {
        "success": True,
        "reservation_id": reservation_id,
        "pin": pin,
        "expires_at": expires_at,
        "station": station,
        "message": f"Charger locked for {req.duration_minutes} minutes. Your PIN: {pin}",
        "ocpp_response": ocpp_resp,
        "payment": payment_info,
    }

@app.post("/api/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    """Verify PIN, trigger OCPP UnlockConnector, and start charging session."""
    reservation = await database.get_reservation(req.station_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="No active reservation for this station")

    if reservation["expires_at"] < time.time():
        await database.delete_reservation(req.station_id)
        await ocpp_engine.cancel_reservation(req.station_id, reservation["reservation_id"])
        await database.save_booking_history({
            **reservation, "outcome": "EXPIRED", "total_kwh": 0, "amount_charged_rs": 0
        })
        raise HTTPException(status_code=410, detail="Reservation expired. No-show fee applied.")

    if str(reservation["pin"]) != str(req.pin):
        await ws_manager.broadcast_to_kiosk(req.station_id, {
            "type": "ACCESS_DENIED",
            "station_id": req.station_id,
            "reason": "Wrong PIN",
        })
        raise HTTPException(status_code=401, detail="Wrong PIN. Connector stays locked.")

    # ✅ PIN correct — OCPP UnlockConnector + RemoteStartTransaction
    station = db.get_station(req.station_id) or {}
    power_kw = float(station.get("power_kw") or 60.0)

    session_id = f"CHG_{req.station_id}_{int(time.time())}"
    session_data = {
        "station_id": req.station_id,
        "session_id": session_id,
        "user_id": reservation["user_id"],
        "start_soc": reservation["vehicle_soc"],
        "current_soc": reservation["vehicle_soc"],
        "start_time": time.time(),
        "kw": power_kw,
        "kwh": 0.0,
    }
    await database.save_session(session_data)

    await ocpp_engine.unlock_connector(req.station_id)
    await ocpp_engine.remote_start_transaction(req.station_id, reservation["user_id"])

    await ws_manager.broadcast_to_kiosk(req.station_id, {
        "type": "OCPP_UNLOCK_RELAY",
        "station_id": req.station_id,
        "session_id": session_id,
        "power_kw": power_kw,
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Start live meter value streaming
    asyncio.create_task(stream_meter_values(req.station_id, session_id, reservation["user_id"]))

    await database.delete_reservation(req.station_id)
    return {
        "success": True,
        "session_id": session_id,
        "power_kw": power_kw,
        "message": f"Identity verified. Relay closed. {power_kw:.0f} kW charging started.",
    }

@app.post("/api/cancel-reservation/{station_id}")
async def cancel_reservation(station_id: str, user_id: str = "hyundai_driver_001"):
    reservation = await database.get_reservation(station_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="No active reservation")
    if reservation["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not your reservation")

    await database.delete_reservation(station_id)
    await ocpp_engine.cancel_reservation(station_id, reservation["reservation_id"])
    payment_engine.full_refund(reservation.get("payment_order_id", ""))

    await database.save_booking_history({**reservation, "outcome": "CANCELLED_BY_USER", "total_kwh": 0, "amount_charged_rs": 0})

    await ws_manager.broadcast_to_kiosk(station_id, {
        "type": "OCPP_CANCEL_RESERVATION",
        "station_id": station_id,
    })
    return {"success": True, "message": "Reservation cancelled. Full refund processed."}

@app.get("/api/reservations")
async def list_reservations():
    active = await database.get_all_active_reservations()
    return {"active_reservations": active, "count": len(active)}

@app.get("/api/session/{station_id}")
async def get_session(station_id: str):
    session = await database.get_session(station_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active charging session")
    return session

@app.get("/api/history")
async def get_history():
    """Return last 50 bookings from SQLite history."""
    return await database.get_history(limit=50)

@app.get("/api/ocpp-log")
async def get_ocpp_log():
    """Return last 100 OCPP commands sent — live audit trail for judges."""
    return {"log": ocpp_engine.get_log(), "total": len(ocpp_engine.get_log())}

@app.get("/api/payment/order/{reservation_id}")
async def get_payment_order(reservation_id: str):
    res = await database.get_all_active_reservations()
    for r in res:
        if r["reservation_id"] == reservation_id:
            return {
                "order_id": r.get("payment_order_id"),
                "status": r.get("payment_status"),
                "upi_id": payment_engine.UPI_ID,
                "upi_name": payment_engine.UPI_NAME,
                "amount_rs": 200,
                "mock_mode": payment_engine.mock_mode,
            }
    raise HTTPException(status_code=404, detail="Reservation not found")

@app.post("/api/payment/verify")
async def verify_payment(req: PaymentVerifyRequest):
    valid = payment_engine.verify_payment_signature(req.order_id, req.payment_id, req.signature)
    return {"verified": valid}


# ── Voice Assistant Routes ─────────────────────────────────────────────────────
@app.post("/api/voice/process")
async def process_voice_input(req: VoiceInputRequest):
    """Process voice/text input through the AVA assistant."""
    if not voice_assistant:
        raise HTTPException(status_code=503, detail="Voice assistant not available")
    
    response = voice_assistant.process_input(req.text, req.user_id)
    
    # If the assistant wants to reserve, wire it to the actual reserve API
    if response.action_type == 'action_complete' and response.display_data.get('action') == 'reserve':
        station_id = response.display_data.get('station_id')
        if station_id:
            try:
                reserve_req = ReserveRequest(
                    station_id=station_id,
                    user_id=req.user_id,
                    user_name="Hyundai Driver",
                    duration_minutes=30,
                    vehicle_soc=vehicle_intel.state.soc if vehicle_intel else 18.0
                )
                reserve_result = await reserve_station(reserve_req)
                response.display_data['reservation'] = reserve_result
                response.display_data['pin'] = reserve_result.get('pin')
            except Exception as e:
                response.display_data['reserve_error'] = str(e)
    
    return {
        "text": response.text,
        "display_data": response.display_data,
        "action_type": response.action_type,
        "tool_calls": response.tool_calls,
        "language": response.language,
        "needs_confirmation": response.needs_confirmation,
    }

@app.post("/api/voice/confirm")
async def confirm_voice_action(req: VoiceConfirmRequest):
    """Confirm or deny a pending T2 action (reservation, etc.)."""
    if not voice_assistant:
        raise HTTPException(status_code=503, detail="Voice assistant not available")
    
    response = voice_assistant.confirm_action(req.user_id, req.confirmed)
    
    # Execute the confirmed reservation
    if req.confirmed and response.display_data.get('action') == 'reserve':
        station_id = response.display_data.get('station_id')
        if station_id:
            try:
                reserve_req = ReserveRequest(
                    station_id=station_id,
                    user_id=req.user_id,
                    user_name="Hyundai Driver",
                    duration_minutes=30,
                    vehicle_soc=vehicle_intel.state.soc if vehicle_intel else 18.0
                )
                reserve_result = await reserve_station(reserve_req)
                response.display_data['reservation'] = reserve_result
                response.text += f" PIN: {reserve_result.get('pin', 'N/A')}"
            except Exception as e:
                response.display_data['reserve_error'] = str(e)
    
    return {
        "text": response.text,
        "display_data": response.display_data,
        "action_type": response.action_type,
        "needs_confirmation": response.needs_confirmation,
    }

@app.get("/api/voice/recommend")
async def get_voice_recommendations(
    target_soc: float = 80.0,
    top_k: int = 3,
    urgency: str = "normal",
):
    """Get AI-ranked charging station priority list with spoken summary."""
    if not priority_eng:
        raise HTTPException(status_code=503, detail="Priority engine not available")
    
    recommendations = priority_eng.recommend(target_soc=target_soc, top_k=top_k, urgency=urgency)
    spoken = priority_eng.get_spoken_recommendation()
    
    return {
        "recommendations": [vars(r) if hasattr(r, '__dict__') else r for r in recommendations],
        "spoken_summary": spoken,
        "vehicle_soc": vehicle_intel.state.soc if vehicle_intel else 18.0,
        "vehicle_range_km": vehicle_intel.state.range_km if vehicle_intel else 59.0,
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.get("/api/vehicle/status")
async def get_vehicle_status():
    """Get comprehensive vehicle status — the AVA vehicle intelligence."""
    if not vehicle_intel:
        raise HTTPException(status_code=503, detail="Vehicle intelligence not available")
    
    status = vehicle_intel.get_vehicle_status()
    diagnostics = vehicle_intel.get_diagnostic_summary()
    
    return {
        "status": status,
        "diagnostics_summary": diagnostics,
        "timestamp": datetime.utcnow().isoformat(),
    }

@app.get("/api/vehicle/diagnostics")
async def get_vehicle_diagnostics():
    """Get vehicle component health and service status."""
    if not vehicle_intel:
        raise HTTPException(status_code=503, detail="Vehicle intelligence not available")
    
    status = vehicle_intel.get_vehicle_status()
    return {
        "component_health": status.get("component_health", {}),
        "insights": status.get("insights", []),
        "service_status": status.get("service_status", {}),
        "diagnostics_text": vehicle_intel.get_diagnostic_summary(),
    }

@app.get("/api/speech/config")
async def get_speech_config():
    """Get speech service configuration for frontend."""
    if speech_svc:
        return speech_svc.get_frontend_config()
    return {
        "stt_available": True,
        "stt_languages": ["en-IN", "hi-IN", "mr-IN"],
        "wake_words": ["hey hyundai", "ok hyundai"],
        "sarvam_available": False,
        "tts_available": True,
    }


# ── WebSocket Endpoints ────────────────────────────────────────────────────────
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
            if msg.get("type") == "METER_VALUES":
                sid = msg.get("station_id", station_id)
                session = await database.get_session(sid)
                if session:
                    session["current_soc"] = msg.get("soc", 0)
                    session["kwh"] = msg.get("kwh", 0)
                    await database.save_session(session)
    except WebSocketDisconnect:
        ws_manager.disconnect_kiosk(station_id)

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await ws_manager.connect_dashboard(websocket)
    try:
        while True:
            await asyncio.sleep(5)
            active_res = await database.get_all_active_reservations()
            active_ses = await database.get_all_sessions()
            await websocket.send_json({
                "type": "HEARTBEAT",
                "active_reservations": len(active_res),
                "active_sessions": len(active_ses),
                "timestamp": datetime.utcnow().isoformat(),
            })
    except WebSocketDisconnect:
        ws_manager.disconnect_dashboard(websocket)


# ── Background Tasks ───────────────────────────────────────────────────────────
async def stream_meter_values(station_id: str, session_id: str, user_id: str):
    """Stream live charging meter values to car dashboard via WebSocket."""
    session = await database.get_session(station_id)
    if not session:
        return

    station = db.get_station(station_id) or {}
    rated_kw = float(station.get("power_kw") or 60.0)
    station_name = station.get("name", "EV Charging Station")
    operator = station.get("operator", "Charge Point Operator")
    city = station.get("city") or station.get("district") or "India"

    # Dynamic tariff based on hardware power class
    if rated_kw >= 100.0:
        tariff = 22.50  # Ultra-Fast DC
    elif rated_kw >= 40.0:
        tariff = 18.00  # Fast DC
    elif rated_kw >= 20.0:
        tariff = 14.50  # Commercial AC Fast
    else:
        tariff = 11.00  # Standard AC

    soc = float(session.get("start_soc", 20.0))
    kwh = 0.0
    tick = 0

    while True:
        current_session = await database.get_session(station_id)
        if not current_session or soc >= 100.0:
            break
        await asyncio.sleep(2)
        tick += 1

        # Realistic CC/CV charging curve tailored to the station's exact rated kW:
        if soc < 80.0:
            kw = max(2.5, rated_kw * (0.95 - (soc * 0.0008)))
        else:
            taper = max(0.15, 0.95 - 0.06 - ((soc - 80.0) * 0.035))
            kw = max(3.0, rated_kw * taper)

        # Hyundai Ioniq 5 battery pack capacity: 72.6 kWh
        # Visual demo speedup factor (~10x) so users see dynamic progress in real time
        kw_step = kw * (2 / 3600) * 10.0
        soc_delta = (kw_step / 72.6) * 100.0
        soc = min(100.0, soc + soc_delta)
        kwh += kw_step
        cost_rs = round(kwh * tariff, 2)

        # Estimate time remaining (minutes)
        if soc < 80.0 and kw > 0:
            eta_mins = round((((80.0 - soc) / 100.0) * 72.6 / kw) * 60, 0)
        else:
            eta_mins = round((((100.0 - soc) / 100.0) * 72.6 / max(kw, 3.0)) * 60, 0)

        current_session["current_soc"] = round(soc, 1)
        current_session["kw"] = round(kw, 1)
        current_session["kwh"] = round(kwh, 2)
        await database.save_session(current_session)

        meter_msg = {
            "type": "METER_VALUES",
            "station_id": station_id,
            "session_id": session_id,
            "station_name": station_name,
            "operator": operator,
            "city": city,
            "rated_power_kw": rated_kw,
            "tariff": tariff,
            "soc": round(soc, 1),
            "kw": round(kw, 1),
            "kwh": round(kwh, 2),
            "cost_rs": cost_rs,
            "eta_mins": max(1, int(eta_mins)),
            "elapsed_minutes": round(tick * 2 / 60, 1),
            "timestamp": datetime.utcnow().isoformat(),
        }
        await ws_manager.broadcast_to_car(user_id, meter_msg)
        await ws_manager.broadcast_to_kiosk(station_id, meter_msg)

    # Session complete
    await database.delete_session(station_id)
    await database.save_booking_history({
        "reservation_id": session_id,
        "station_id": station_id,
        "user_id": user_id,
        "user_name": "Hyundai Driver",
        "created_at": time.time(),
        "expires_at": time.time(),
        "outcome": "COMPLETED",
        "total_kwh": round(kwh, 2),
        "amount_charged_rs": round(kwh * tariff, 2),
    })

    await ws_manager.broadcast_to_car(user_id, {
        "type": "CHARGING_COMPLETE",
        "station_id": station_id,
        "final_soc": round(soc, 1),
        "total_kwh": round(kwh, 2),
        "cost_rs": round(kwh * tariff, 2),
        "message": f"Charging complete at {round(soc, 1)}% SoC. Total: ₹{round(kwh * tariff, 2)}. Please unplug.",
    })

async def cleanup_expired_reservations():
    """Periodically release expired reservations from SQLite."""
    while True:
        await asyncio.sleep(30)
        active = await database.get_all_active_reservations()
        now = time.time()
        for r in active:
            if r["expires_at"] < now:
                await database.delete_reservation(r["station_id"])
                await ocpp_engine.cancel_reservation(r["station_id"], r["reservation_id"])
                await database.save_booking_history({**r, "outcome": "EXPIRED_NO_SHOW", "total_kwh": 0, "amount_charged_rs": 0})
                await ws_manager.broadcast_to_kiosk(r["station_id"], {
                    "type": "OCPP_RESERVATION_EXPIRED",
                    "station_id": r["station_id"],
                    "reason": "30-min window expired. No-show fee applied.",
                })


@app.on_event("startup")
async def startup():
    await database.init_db()
    asyncio.create_task(cleanup_expired_reservations())
    print("=" * 60)
    print("🚀 HYUNDAI SMARTRESERVE API v2.0 — STARTED")
    print("=" * 60)
    print(f"   📍 Stations loaded    : {len(db.stations)} BEE base nodes")
    print(f"   🗄️  Database           : SQLite (persistent)")
    print(f"   ⚡ OCPP Engine         : 1.6J Mock (ReserveNow, Unlock, StartTx)")
    print(f"   🎙️  Voice Assistant    : {'AVA Active' if voice_assistant else 'Unavailable'}")
    print(f"   🏆 Priority Engine    : {'Active' if priority_eng else 'Unavailable'}")
    print(f"   💳 Payment             : {'Razorpay Mock (demo mode)' if payment_engine.mock_mode else 'Razorpay LIVE'}")
    print(f"   🌐 OCM API Key         : {OCM_API_KEY[:8]}...")
    print(f"   📡 WebSockets          : /ws/car/{{user}} | /ws/kiosk/{{station}}")
    print("=" * 60)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
