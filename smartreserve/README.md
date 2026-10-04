# Hyundai SmartReserve 🔌

**AI-Powered In-Cabin EV Charging Co-Pilot**  
Hyundai CoE Mobility Innovation Ideathon 2026 · Track B · Category 2

---

## What This Is

SmartReserve is a full-stack prototype that shows all 934 Telangana EV charging stations on your Hyundai car screen, predicts congestion using a Stanford ROLAND Graph Neural Network, and lets you physically lock a charger for 30 minutes — all without touching your phone.

### The three things we built:
1. **In-Cabin Dashboard** — Hyundai-styled dark UI with live map, AI badges, and a Reserve button
2. **Cloud AI Brain** — FastAPI backend + OCPP 1.6 engine + GNN predictions
3. **Charger Kiosk Simulator** — Digital twin of a real charger with full state machine

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Launch everything
```bash
python run_demo.py
```

### 3. Open two browser windows
- **Left window:**  `http://localhost:3000/car`   ← In-cabin dashboard
- **Right window:** `http://localhost:3000/kiosk?station=TG0001`  ← Kiosk

---

## Project Structure

```
smartreserve/
├── run_demo.py                  ← One command starts everything
├── requirements.txt
│
├── smartreserve_backend/
│   ├── main.py                  ← FastAPI server (all endpoints + WebSockets)
│   ├── websocket_manager.py     ← Real-time connection hub
│   ├── ocpp_engine.py           ← OCPP 1.6 protocol commands
│   └── stations_db.py           ← 934 station data loader/generator
│
├── smartreserve_frontend/
│   ├── index.html               ← Demo hub landing page
│   ├── server.py                ← Static file server (port 3000)
│   ├── car_dashboard/
│   │   └── index.html           ← In-cabin UI (Leaflet map + WebSocket)
│   └── kiosk_simulator/
│       └── index.html           ← Charger kiosk (OCPP state machine)
│
└── demo_data/
    └── nodes_master.csv         ← Auto-generated if not present
```

---

## API Reference

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stations` | All stations with live status + GNN scores |
| GET | `/api/stations/{id}` | Single station detail |
| POST | `/api/reserve` | Lock a charger (returns PIN) |
| POST | `/api/verify-otp` | Verify PIN and unlock relay |
| POST | `/api/cancel-reservation/{id}` | Cancel with full refund |
| GET | `/api/session/{id}` | Live charging session data |
| GET | `/health` | System health check |
| GET | `/docs` | Interactive API docs (Swagger UI) |

### WebSocket Channels

| URL | Direction | Purpose |
|-----|-----------|---------|
| `/ws/car/{user_id}` | Bidirectional | Car dashboard ↔ Cloud |
| `/ws/kiosk/{station_id}` | Bidirectional | Charger kiosk ↔ Cloud |

### WebSocket Message Types (Cloud → Car)

```json
{ "type": "RESERVATION_CONFIRMED", "pin": "482910", "expires_at": 1750000000 }
{ "type": "METER_VALUES", "soc": 42.5, "kw": 58.2, "kwh": 3.4 }
{ "type": "CHARGING_COMPLETE", "final_soc": 80.0, "total_kwh": 18.5 }
```

### WebSocket Message Types (Cloud → Kiosk)

```json
{ "type": "OCPP_RESERVE_NOW", "reservation_id": "RES_TG0001_123", "pin_hash": "482910" }
{ "type": "OCPP_UNLOCK_RELAY", "session_id": "CHG_TG0001_456" }
{ "type": "OCPP_CANCEL_RESERVATION", "station_id": "TG0001" }
{ "type": "OCPP_RESERVATION_EXPIRED", "reason": "Timer expired. No-show fee applied." }
```

---

## Full Demo Script (90 seconds)

1. **Open two windows** — `/car` on left, `/kiosk?station=TG0001` on right
2. **Point to battery** — 18% SoC, 41 km range, AI alert showing
3. **Show AI badge** — Click a green pin, see GNN congestion prediction
4. **Click Reserve** — Watch kiosk instantly go BLUE (locked)
5. **Show PIN** — 6-digit PIN appears on car screen
6. **Wrong PIN** → kiosk flashes RED, "Access Denied, 0 kW"
7. **Correct PIN** → relay closes, 60 kW charging starts, SoC rises live on both screens

---

## Charger State Machine

```
System Starts
     ↓
  Available  ←──────────────────────────────────┐
     ↓ (ReserveNow)                              │
  Reserved ←──────── (timer expires) ──→ fee    │
     │                                           │
     ├─ wrong PIN → AccessDenied → try again     │
     │                                           │
     └─ correct PIN → Unlocked                   │
                          ↓                      │
                       Charging                  │
                          ↓                      │
                       Finishing ─────────────── ┘
```

---

## Judge Q&A Ready

**"How does the physical charger lock?"**  
Every modern DC fast charger in India runs OCPP 1.6 by law (BEE 2024 guidelines). We act as the CSMS and send `ReserveNow` over WebSocket. The charger's firmware handles the relay lockout — we don't modify any hardware.

**"78% accuracy — can we trust it?"**  
We display predictions as ranges with confidence labels: "0–5 min (High Confidence)" or "20–40 min (Low Confidence)". Always honest with the driver, never a hard promise.

**"Why is this in-vehicle, not just an app?"**  
The car detects battery SoC and triggers SmartReserve automatically. It runs on the Hyundai AVNT infotainment screen via the ccNC platform. The driver never looks at their phone.

---

## Key Dates

- **Preliminary Winner Announcement:** October 30, 2026
- **Final Round:** December 16, 2026 · Delhi  
- **Prize:** ₹1 Lakh finalist + ₹3 Lakh Grand Prize

---

*Built for Hyundai CoE Mobility Innovation Ideathon 2026*  
*Real data: Bureau of Energy Efficiency + Parivahan Sewa*  
*Model: Stanford ROLAND GNN · 78% R² · 934 stations · 36 months*
