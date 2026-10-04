# Hyundai SmartReserve (Hyundai_vrum) 🚗⚡

[![Hyundai CoE Ideathon](https://img.shields.io/badge/Hyundai_CoE_Ideathon-2026-002C5F?style=for-the-badge&logo=hyundai&logoColor=white)](https://hcoe-ideathon.com/)
[![Track B](https://img.shields.io/badge/Track_B-Technology_%26_Prototype-0284C7?style=for-the-badge)](https://hcoe-ideathon.com/)
[![Category 2](https://img.shields.io/badge/Category_2-AI_Powered_%26_Digital_In--Vehicle_Experience-059669?style=for-the-badge)](https://hcoe-ideathon.com/)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.3.0-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org)
[![OCPP](https://img.shields.io/badge/Protocol-OCPP_1.6J-F59E0B?style=for-the-badge)](https://openchargealliance.org/)

> **AI-Powered In-Cabin EV Charging Co-Pilot with Hardware-Enforced 30-Minute Remote Lockout**  
> *Developed for the Hyundai Centre of Excellence (Hyundai CoE) Mobility Innovation Ideathon 2026.*

---

## 🏗️ Core System Architecture (100% Pure Software — Zero Hardware)

![Core Horizontal Architecture](architecture_horizontal.png)

---

## 📌 Executive Summary

India's EV market is rapidly growing, with over 60,000 electric vehicles registered in Telangana alone. However, Indian drivers face severe **charging uncertainty and range anxiety**:
1. Existing apps (ChargeZone, Statiq, EVselect) show static station listings, but **never show if a specific connector is free in real time**.
2. Drivers are forced to pull out their phone while driving (illegal and dangerous).
3. Even when a charger is vacant, another driver or an ICE vehicle can arrive first and **take the plug**.

**Hyundai SmartReserve** transforms this experience into a native **In-Cabin EV Charging Co-Pilot** running directly inside Hyundai's **ccNC / AVNT infotainment system**:
- **Proactive In-Cabin Alert:** Detects low battery ($<20\%$ SoC) and automatically queries a statewide Graph Neural Network (GNN) to recommend the least congested charging hub along the route.
- **1-Tap 30-Minute Exclusive Lock:** The driver taps one button on the car screen. The cloud sends a standardized **OCPP 1.6 `ReserveNow`** command to the physical charger, turning the kiosk **BLUE (LOCKED)** and opening the high-voltage power contactor relay (0 kW power).
- **Secure PIN / QR Authorization:** Generates an exclusive 6-digit PIN (`482910`). Unauthorized users attempting to plug in are blocked (`Access Denied`). When the Hyundai driver arrives and enters the PIN, the contactor closes and 60 kW DC fast charging begins immediately!

---

## 📊 Datasets Used for Training & Spatial Modeling

The predictive engine and spatial graphs were built and trained using **official Indian Government repositories and multi-modal geospatial data sources**:

| Dataset Name | Primary Source / Authority | Granularity & Scale | Features Extracted |
| :--- | :--- | :--- | :--- |
| **1. National EV Charging Stations Registry** | **Bureau of Energy Efficiency (BEE)**, Ministry of Power, Govt. of India (Order #23369 via Dataful) | **934 Physical Station Nodes** across all 33 Districts of Telangana | Exact GPS coordinates (Lat/Lng), Charger Power Ratings (kW), AC/DC Connector Types (CCS2, CHAdeMO, Type 2), Number of Ports, and CPO Ownership (Tata Power, IOCL, BPCL, HPCL, Private). |
| **2. Telangana EV & Hybrid Vehicle Registrations** | **PARIVAHAN SEWA**, Ministry of Road Transport and Highways (MoRTH), Govt. of India (Dataset #23540 via Dataful) | **Monthly RTO-Level Time Series** (2014 – 2026) | Monthly EV adoption volume, vehicle manufacturer breakdown, fuel type (BEV vs. Hybrid), and district electrification velocity ($\Delta \text{EV}$). |
| **3. Geospatial Points-of-Interest (POI)** | **OpenStreetMap (OSM) Overpass API** | Radius count around all 934 nodes | Nearby amenities, shopping malls, fuel stations, tourism hubs, healthcare centers, and educational institutions. |
| **4. District Demographics & Population Density** | **Census of India & Telangana State Portal** | District & Mandal Level | Yearly population density across all 33 districts of Telangana. |
| **5. 36 Monthly Dynamic Graph Snapshots** | Aggregated & Engineered Dynamic Spatio-Temporal Sequence | **Jan 2023 – Dec 2025 (36 Months)** | 52-dimensional dynamic node feature vectors tracking network commissioning (from 355 active nodes in Jan 2023 to 934 nodes in Dec 2025), historical demand lags ($t-1, t-2, t-3$), and cyclical seasonality ($\sin/\cos$ month encodings). |

---

## 🧠 AI Model: Stanford ROLAND Spatio-Temporal GNN

The demand forecasting engine is based on the **Stanford ROLAND (Recurrent Online Learning on Dynamic Graphs)** framework:

```
        District Subgraphs (934 Nodes)
                       │
                       ▼
       ┌───────────────────────────────┐
       │ Spatial GNN Encoder           │  <-- ResidualEdgeConv (Topological message passing)
       └──────────────┬────────────────┘
                      │ Xt
                      ▼
       ┌───────────────────────────────┐
       │ Temporal Recurrent Cell       │  <-- GRUUpdater with Active Node Masking
       └──────────────┬────────────────┘
                      │ Ht
                      ▼
       ┌───────────────────────────────┐
       │ Multi-Target Regression Head  │  <-- MLP (Predicts Units [kWh] & Peak Load [kW])
       └──────────────┬────────────────┘
```

- **Active Node Masking:** Station commissioning dates are strictly respected. Dynamic features for uncommissioned stations remain at the scaled zero representation (`-mean / std`), preventing training bias.
- **Topological Edge Partitioning:** Stations form district-wise complete subgraphs to capture localized network competition while eliminating cross-border spatial leakage.
- **Zero-Masked Spatial Coordinates:** Coordinates are zero-masked (`x[:, 0:2] = 0.0`) to force the network to learn pure topological dynamics rather than overfitting to absolute GPS locations.
- **Evaluation Performance:**
  - **Test $R^2$ Score:** **$78.50\% - 80.02\%$** on statewide holdout test snapshots (Months 32–36).
  - **Checkpoints Included:** [`roland_final_weights_80_02.pt`](EVCS_Demand_Forecasting/final_model/roland_final_weights_80_02.pt) and [`roland_final_submission_80_02.pt`](EVCS_Demand_Forecasting/final_model/roland_final_submission_80_02.pt).

---

## 💻 Prototype Components (100% Pure Software)

The codebase contains a full-stack, 3-tier software architecture:

1. **In-Cabin Dashboard (`smartreserve/smartreserve_frontend/car_dashboard/index.html`):**
   - Styled after Hyundai's **ccNC / AVNT 12.3-inch dual-curved display**.
   - Real interactive **Leaflet.js** map with 934 Telangana charging stations.
   - Live telemetry: battery SoC gauge, remaining range, GNN congestion badges (*"0 min queue predicted"*).
   - One-touch reservation drawer, countdown timer, dynamic 6-digit PIN display, and live Canvas charging charts.
2. **Charger Kiosk Simulator (`smartreserve/smartreserve_frontend/kiosk_simulator/index.html`):**
   - Digital Twin of a roadside 60 kW DC fast-charging post.
   - Implements full state machine: `AVAILABLE` (Green) $\rightarrow$ `RESERVED` (Blue) $\rightarrow$ `ACCESS DENIED` (Red shake) $\rightarrow$ `CHARGING` (Cyan) $\rightarrow$ `FINISHING` (Amber).
   - Simulates physical contactor relay states: `Relay: OPEN (0 kW Power)` vs `Relay: CLOSED (60 kW DC Flow)`.
   - On-screen touch keypad, anti-tamper lockouts, anti-hogging idle fee countdowns, and a live scrolling terminal displaying incoming/outgoing **OCPP 1.6 call frames**.
3. **Cloud & AI Brain (`smartreserve/smartreserve_backend/main.py`):**
   - High-performance asynchronous **FastAPI + WebSockets** backend.
   - Synchronizes vehicle screens and charging kiosks in **$<100$ milliseconds**.
   - Protocol-compliant **OCPP 1.6 JSON engine** (`ReserveNow`, `Authorize`, `RemoteStartTransaction`, `MeterValues`).
   - GNN predictor with deterministic fallback for resilient live demonstrations.

---

## 🚀 Quick Start Guide

### 1. Clone the Repository
```bash
git clone https://github.com/kushagrakushwah/Hyundai_vrum.git
cd Hyundai_vrum
```

### 2. Install Dependencies
```bash
pip install -r smartreserve/requirements.txt
```

### 3. Launch the Prototype
```bash
python smartreserve/run_demo.py
```

### 4. Open in Your Browser
Open two browser windows side by side:
- **Left Window (Car Cockpit):** [`http://localhost:3000/car`](http://localhost:3000/car)
- **Right Window (Charger Kiosk):** [`http://localhost:3000/kiosk?station=TG0001`](http://localhost:3000/kiosk?station=TG0001)
- **Demo Hub & Health:** [`http://localhost:3000`](http://localhost:3000) · [`http://localhost:8000/docs`](http://localhost:8000/docs)

---

## 🎬 The 90-Second Demo Script (For Judges)

1. **Setup:** Place `/car` on the left and `/kiosk` on the right.
2. **Alert:** Point out the car battery at 18% SoC. The GNN has already recommended Gachibowli Hub with zero queue time.
3. **Reserve:** Click **"Reserve 30-Min Exclusive Slot"** on the car screen.
4. **Lock:** Watch the kiosk screen on the right immediately turn **BLUE** (`RESERVED — POWER LOCKED: 0 kW`).
5. **Unauthorized Attempt:** Type `111111` on the kiosk keypad. The screen flashes **RED** (`ACCESS DENIED`). Power remains locked.
6. **Authorized Unlock:** Type the driver's secret PIN (`482910`) from the car dashboard. The relay clicks shut, the kiosk turns **GREEN**, and 60 kW DC charging commences with live battery curves on both screens!

---

## 📄 Key Project Documents

- **Master Plan PDF (25 Pages):** [`Hyundai_SmartReserve_FullPlan.pdf`](Hyundai_SmartReserve_FullPlan.pdf)
- **Master Plan Interactive HTML:** [`Hyundai_SmartReserve_FullPlan.html`](Hyundai_SmartReserve_FullPlan.html)
- **High-Res Architecture Diagram:** [`architecture_horizontal.png`](architecture_horizontal.png)
- **Standalone Architecture PDF:** [`architecture_horizontal.pdf`](architecture_horizontal.pdf)

---

## 👥 Authors & Acknowledgments

- **Participant:** Kushagra Kushwah
- **Competition:** Hyundai CoE Mobility Innovation Ideathon 2026 (Hyundai Motor India Engineering)
- **Track & Category:** Track B (Technology & Prototype) · Category 2 (AI-Powered & Digital In-Vehicle Experience)
