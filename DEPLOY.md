# 🚀 Deploying Hyundai SmartReserve to Render & Cloud IoT Ecosystem

This guide explains how to deploy the **Hyundai SmartReserve** cloud backend to **Render** and bridge it end-to-end with the **EV Sarthi Wokwi hardware simulation** using Cloud MQTT.

---

## 🌐 System Architecture in Cloud

```
+------------------------------------+        +-----------------------------------+
|  Render Cloud Web Service          |        |  Wokwi Hardware Simulation        |
|  https://hyundai-vrum.onrender.com |        |  https://wokwi.com/projects/...   |
|  - FastAPI REST API & WebSockets   |        |  - ESP32 DevKit C v4              |
|  - Car Dashboard & Kiosk Static UI |        |  - Dual Displays (TFT + OLED)     |
|  - Gemini LLM & Voice Assistant    |        |  - Keypad & RFID Auth             |
|  - MQTT Service Client             |        |  - Power Contactor Relay & ADC    |
+-----------------+------------------+        +-----------------+-----------------+
                  |                                             |
                  | Pub/Sub                                     | Pub/Sub
                  v                                             v
        +-------------------------------------------------------------+
        |                 Cloud MQTT Broker                           |
        |  (broker.hivemq.com:1883 or HiveMQ Cloud MQTTS:8883)        |
        |  Topics: evsarthi/{station_id}/{connector_id}/*             |
        +-------------------------------------------------------------+
```

---

## 📦 Option 1: Automatic Deployment on Render

### Step 1: Push Repository to GitHub
Ensure all latest commits are pushed to your GitHub repository:
```bash
git push origin kushagent
```

### Step 2: Configure Service in Render Dashboard
1. Log in to [Render.com](https://render.com) and click **New + ➔ Web Service**.
2. Connect your repository: `kushagrakushwah/Hyundai_vrum` (Branch: `kushagent` or `main`).
3. Set the following build parameters:
   * **Name:** `hyundai-smartreserve`
   * **Region:** Singapore / Frankfurt / Oregon (nearest to your users)
   * **Root Directory:** *(leave blank)*
   * **Runtime:** `Python 3`
   * **Build Command:**
     ```bash
     pip install -r smartreserve/requirements.txt
     ```
   * **Start Command:**
     ```bash
     cd smartreserve/smartreserve_backend && uvicorn main:app --host 0.0.0.0 --port $PORT
     ```
   * **Auto-Deploy:** `Yes`

### Step 3: Configure Environment Variables
In the Render Service dashboard under **Environment**, add the following keys:

| Environment Variable | Required | Default / Value | Description |
|---|---|---|---|
| `PORT` | Auto | *(Auto-assigned by Render)* | Port for Uvicorn web service |
| `PYTHON_VERSION` | Yes | `3.11.0` | Python runtime version |
| `MQTT_BROKER_HOST` | **Recommended** | `broker.hivemq.com` | Cloud MQTT broker host for hardware sync |
| `MQTT_BROKER_PORT` | **Recommended** | `1883` | Cloud MQTT port |
| `GEMINI_API_KEY` | **Recommended** | `AIzaSy...` | Enables intelligent conversational responses in AVA voice assistant |
| `OCM_API_KEY` | Optional | `1f545914-8daa-4fa6-9d7b-4a6819f2f7cc` | Open Charge Map live telemetry API key |
| `RAZORPAY_KEY_ID` | Optional | `rzp_test_placeholder` | Razorpay payment escrow key |
| `RAZORPAY_KEY_SECRET` | Optional | `placeholder` | Razorpay secret |

Click **Save Changes** and trigger **Manual Deploy ➔ Clear build cache & deploy**.

---

## 🔌 Linking Hardware (Wokwi) to Render

Render web services only expose public HTTP/HTTPS ports (80/443), not raw TCP port 1883. Because both the Render FastAPI service and the Wokwi ESP32 connect outbound to a shared Cloud MQTT Broker, they communicate seamlessly across the internet!

### In Wokwi:
1. Open the [EV Sarthi Wokwi Project](https://wokwi.com/projects/480737603461683201).
2. Ensure `ev_sarthi_hardware/src/config.h` (or top of `sketch.ino`) has:
   ```cpp
   #define MQTT_BROKER_DEFAULT "broker.hivemq.com"
   #define MQTT_PORT_DEFAULT   1883
   ```
3. Click **Play (▶)** in Wokwi.
4. Open your Render dashboard: `https://hyundai-vrum.onrender.com/car`.
5. Tap **Reserve Slot** on the car dashboard.
6. **Watch Wokwi instantly react**: The TFT turns blue (`RESERVED`), the OLED starts the 60s countdown, and entering the PIN on the 4x4 keypad unlocks the contactor relay and green LED!

---

## 🐳 Option 2: Local Deployment with Docker Compose

To run the entire ecosystem locally (including Eclipse Mosquitto on ports 1883 & 9001):

```bash
# Clone and enter directory
git clone https://github.com/kushagrakushwah/Hyundai_vrum.git
cd Hyundai_vrum

# Start Mosquitto + FastAPI Backend together
docker compose up --build
```

### Local URLs:
* **In-Cabin Dashboard:** [http://localhost:8000/car](http://localhost:8000/car)
* **Kiosk Simulator:** [http://localhost:8000/kiosk?station=TG0001](http://localhost:8000/kiosk?station=TG0001)
* **Interactive API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **Health Endpoint:** [http://localhost:8000/health](http://localhost:8000/health)
* **Mosquitto MQTT TCP:** `localhost:1883`
* **Mosquitto WebSockets:** `ws://localhost:9001`

---

## 🔍 Verification & Health Checks

Verify your deployment status at any time:
```bash
# Health check (returns SQLite, stations, reservations, and payment status)
curl https://your-app.onrender.com/health

# Live statistics endpoint
curl https://your-app.onrender.com/api/stats
```
Expected output:
```json
{
  "status": "healthy",
  "stations_loaded": 934,
  "active_reservations": 0,
  "database": "SQLite (persistent)",
  "ocpp": "OCPP 1.6J Mock Engine"
}
```
