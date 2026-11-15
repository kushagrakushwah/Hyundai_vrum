# Project: EV Sarthi IoT Hardware & Ecosystem Integration

## Architecture
The EV Sarthi integration bridges edge IoT hardware (ESP32 DevKit C v4) simulated in Wokwi with the Hyundai SmartReserve FastAPI backend, Mosquitto MQTT broker, and SteVe OCPP 1.6J / LF Energy EVerest CSMS ecosystem.

```
+-----------------------------------------------------------------------------------------+
|                                    SYSTEM ARCHITECTURE                                  |
+-----------------------------------------------------------------------------------------+
|                                                                                         |
|  +--------------------+        MQTT Topics        +----------------------------------+  |
|  |  EV Sarthi ESP32   | <=======================> |       FastAPI IoT Backend        |  |
|  |  Wokwi Simulation  | (reserve, state, telemetry|  - MQTT Service (aiomqtt/paho)   |  |
|  |  - Sensors/Displays|  auth, cmd)               |  - DB: reservations, sessions    |  |
|  |  - Keypad/RFID     |                           |    telemetry (aiosqlite)         |  |
|  |  - Relay / LEDs    |                           |  - WebSocket Manager (Car/Kiosk) |  |
|  +--------------------+                           +----------------------------------+  |
|            |                                             |                 |            |
|            |                                             | REST / WS       | OCPP 1.6J  |
|            v                                             v                 v            |
|  +--------------------+                           +------------+    +---------------+   |
|  | Local MicroSD      |                           |  Frontend  |    |  SteVe CSMS   |   |
|  | /telemetry.csv     |                           |  Dashboards|    |  Central      |   |
|  +--------------------+                           +------------+    +---------------+   |
|                                                                                         |
+-----------------------------------------------------------------------------------------+
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Wokwi Simulation & PlatformIO Config | Complete `diagram.json`, `wokwi.toml`, `libraries.txt`, `platformio.ini` in `ev_sarthi_hardware/` | M1 | Survey 1, R1 |
| 2 | Shared SPI Bus Arbitration | Mutual exclusion for ILI9341 (CS 14), MFRC522 (CS 27), MicroSD (CS 5) on SCK 18, MISO 19, MOSI 23 | M1 | Survey 1, R1 |
| 3 | Sensor Sampling & Calibration | 12-bit ADC voltage (GPIO 35) & current (GPIO 34) sampling, conversion formulas (V, A, kW, kWh) | M1 | Survey 1, R1 |
| 4 | Dual Display UI Drivers | SSD1306 I2C OLED (diagnostic/telemetry) and ILI9341 SPI TFT (user splash, PIN masking, status) | M1 | Survey 1, R2 |
| 5 | Dual User Authentication Peripherals | 3x4 Keypad PIN entry ('*', '#') and MFRC522 13.56MHz contactless RFID reader scanning | M1 | Survey 1, R2 |
| 6 | Fail-Safe Relay & Status LEDs | GPIO 15 driving relay coil: fail-safe LOW/Red LED (NC), energized HIGH/Green LED (NO) | M1 | Survey 1, R2 |
| 7 | Firmware 4-State Machine | AVAILABLE, RESERVED, CHARGING, FAULTED lifecycle implementation | M1 | Survey 1, R2 |
| 8 | 60-Second Reservation Countdown | Non-blocking countdown timer with auto-revert to AVAILABLE on timeout | M1 | Survey 1, R2 |
| 9 | 8-Second Auth Grant Window | Non-blocking window for cable insertion on access grant; safe fallback if current < 0.8A | M1 | Survey 1, R2 |
| 10 | MicroSD CSV Telemetry Logging | Append-only 11-column persistent logging to `/telemetry.csv` with flush-on-write | M1 | Survey 1, R2 |
| 11 | Hardware Fault Trip Protection | Instant emergency trip (<50ms) to FAULTED on overcurrent (>29.7A) or overvoltage (>227V) | M1 | Survey 3, R2 |
| 12 | Firmware WiFi & MQTT Client | PubSubClient integration on `Wokwi-GUEST` subscribing to `/reserve`, `/cmd`, publishing `/state`, `/telemetry`, `/auth` | M1 | Survey 1, R3 |
| 13 | Mosquitto MQTT Broker Configuration | Eclipse Mosquitto config with TCP (1883) and WebSockets (9001), Docker Compose service | M2 | Survey 2, R3 |
| 14 | FastAPI Async MQTT Service | Background MQTT service in FastAPI (`smartreserve_backend/mqtt_service.py`) for pub/sub | M2 | Survey 2, R3 |
| 15 | Multi-Connector Database Schemas | Expanded SQLite tables with `(station_id, connector_id)` composite keys and `connector_telemetry` | M2 | Survey 2, R3 |
| 16 | MQTT Telemetry Ingestion Pipeline | Ingests `evsarthi/.../telemetry`, updates DB cache, and streams to Car/Kiosk WebSockets | M2 | Survey 2, R3 |
| 17 | MQTT Reservation Lifecycle Handler | `POST /api/reserve` dispatches `/reserve`; state transitions update backend DB and WebSockets | M2 | Survey 2, R3 |
| 18 | MQTT Authentication & Command Handler | Ingests `/auth`, verifies PIN/RFID against reservation/fleet DB, dispatches `/cmd` | M2 | Survey 2, R3 |
| 19 | SteVe OCPP 1.6J Bridge | Translates EV Sarthi events to OCPP 1.6-J JSON (StatusNotification, ReserveNow, Authorize, MeterValues, StopTransaction) | M3 | Survey 2, R3 |
| 20 | EVerest Architectural Alignment | Maps EV Sarthi components to LF Energy EVerest modules (EvseBoardSupport, Auth, EvseManager) | M3 | Survey 2, R3 |
| 21 | Mock Hardware Test Simulator | High-fidelity virtual ESP32 simulator (`MockEVSarthiClient`) for automated headless testing | E2E-Test | Survey 3, R4 |
| 22 | Tier 1 Automated Feature Tests | Unit and nominal integration tests for MQTT schemas, reservation, auth, telemetry, relay | E2E-Test | Survey 3, R4 |
| 23 | Tier 2 Automated Boundary Tests | Boundary tests for 60s timer, 8s auth window, late PIN, keypad overflow, ADC rails | E2E-Test | Survey 3, R4 |
| 24 | Tier 3 Automated Cross-Feature Tests | Concurrent multi-connector, cancellation rollback, broker reconnect, SteVe sync, AVA voice loop | E2E-Test | Survey 3, R4 |
| 25 | Tier 4 Automated Fault Injection Tests | Overcurrent trip (<50ms), overvoltage trip, sensor loss, SD unmount, power recovery | E2E-Test | Survey 3, R4 |
| 26 | 100% E2E Test Suite Pass | Execution and passage of all Tier 1-4 tests against integrated stack | M4 | Survey 3, R4 |
| 27 | Adversarial Coverage Hardening | Tier 5 adversarial stress testing, code coverage audit, and edge case hardening | M4 | Project Pattern |

## Milestones
| # | Name | Scope | Dependencies | Status | Sub-Orchestrator Conv ID | Key Outputs |
|---|------|-------|-------------|--------|--------------------------|-------------|
| **E2E-Test** | E2E Testing Suite | Virtual ESP32 simulator, mock broker harness, Tier 1-4 test suites, publishes `TEST_READY.md` | none | **DONE** | 231c6887-f2f8-4777-9ca7-fff70b1430fb | `TEST_READY.md` published. `tests/conftest.py`, `tests/mock_esp32_hardware.py`, 182/182 passing tests across Tiers 1-4. Gate: PASS (Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |
| **M1** | Hardware & Firmware Integration | Integrate Wokwi simulation and ESP32 firmware in `ev_sarthi_hardware/`, SPI arbitration, displays, keypad/RFID, relay/LEDs, 4-state FSM, 60s/8s timers, SD logger, MQTT client | none | **DONE** | 06bcf3f8-eb0b-4293-b572-f5113ec8050b | `ev_sarthi_hardware/` (diagram.json, wokwi.toml, libraries.txt, platformio.ini, src/*, sketch.ino). Gate: PASS (Auditor CLEAN, Reviewers APPROVE, Challengers CONFIRM) |
| **M2** | Backend & MQTT Integration | Mosquitto broker setup, FastAPI MQTT service, multi-connector DB schemas, bidirectional pub/sub handlers, REST/WS bindings | M1 interface contracts | **DONE** | 4c8beb00-8901-49aa-b0c5-d569191142bb | `mosquitto/config/mosquitto.conf`, `smartreserve_backend/mqtt_service.py`, `database.py`, `main.py`. Gate: PASS (Auditor CLEAN, Reviewers APPROVE, Challengers CONFIRM, 182/182 tests) |
| **M3** | SteVe OCPP & EVerest Bridge | Bridge layer connecting EV Sarthi MQTT topics with SteVe OCPP 1.6J CSMS and EVerest modules | M2 | **DONE** | 4ab5ee52-0785-4ffc-a387-64f5ec52eb38 | `smartreserve_backend/ocpp_bridge.py` (1,893 lines), full EVerest taxonomy (`get_everest_status()`), `tests/test_ocpp_bridge.py`. Gate: PASS (Auditor CLEAN, Reviewers APPROVE, Challengers CONFIRMED, 316/316 tests pass) |
| **M4** | Final Integration & Hardening | Pass 100% E2E test suite (Tiers 1-4) and complete Tier 5 adversarial coverage hardening | M1, M2, M3, E2E-Test | **DONE** | 37e42167-a936-4689-94f8-96c1af135833 | `tests/test_tier5_adversarial.py` (70 tests), `smartreserve_backend/mqtt_service.py` NaN/Inf hardening. Gate: PASS (Auditor CLEAN, Reviewers APPROVE, Challengers CONFIRMED, 386/386 repository tests pass) |

## Interface Contracts
### Hardware (ESP32) ↔ MQTT Broker ↔ Backend (FastAPI)
- **Topics**:
  - `evsarthi/{station}/{connector}/reserve`: Backend -> ESP32 (`action`: CREATE | CANCEL, `reservation_id`, `pin`, `duration_seconds`: 60, `expires_at`, `rfid_uid`).
  - `evsarthi/{station}/{connector}/state`: ESP32 -> Backend (`state`: AVAILABLE | RESERVED | CHARGING | FAULTED, `relay_state`: ENERGIZED | DE_ENERGIZED, `transition_reason`, `uptime_millis`). Retained = `true`.
  - `evsarthi/{station}/{connector}/telemetry`: ESP32 -> Backend (`voltage_v`, `current_a`, `power_kw`, `energy_kwh`, `voltage_raw`, `current_raw`, `millis`).
  - `evsarthi/{station}/{connector}/auth`: ESP32 -> Backend (`auth_type`: PIN | RFID, `credential`: {`pin`, `rfid_uid`}, `current_state`).
  - `evsarthi/{station}/{connector}/cmd`: Backend -> ESP32 (`action`: AUTH_DECISION, `status`: ACCEPTED | DENIED, `status_code`: 200 | 401, `auth_window_duration_ms`: 8000, `relay_action`: ENERGIZE | DE_ENERGIZE).

### Backend (FastAPI) ↔ SteVe OCPP 1.6J CSMS
- `StatusNotification`: Map `AVAILABLE` -> `Available`, `RESERVED` -> `Reserved`, `CHARGING` -> `Charging`, `FAULTED` -> `Faulted`.
- `ReserveNow`: SteVe dispatches `ReserveNow` -> Backend publishes `/reserve`.
- `Authorize` & `StartTransaction`: MQTT `/auth` triggers `Authorize` -> On Accepted, backend dispatches `/cmd` (ACCEPTED) and sends `StartTransaction`.
- `MeterValues`: MQTT `/telemetry` sampled values mapped to OCPP `MeterValues`.
- `StopTransaction`: Charging stop or timeout sends `StopTransaction`.

## Code Layout
```
c:\Users\kusha\OneDrive\Music\Documents\Projects\EV_Hyundai\
├── ev_sarthi_hardware/                  # M1: Firmware and Wokwi simulation [DONE]
│   ├── diagram.json                     # Complete 13-component hardware wiring
│   ├── wokwi.toml                       # Wokwi simulation runner configuration
│   ├── libraries.txt                    # Wokwi library dependencies
│   ├── platformio.ini                   # PlatformIO project configuration
│   ├── sketch.ino                       # Unified standalone sketch for Wokwi CLI / Web
│   └── src/
│       ├── main.cpp                     # Production ESP32 firmware with WiFi/MQTT
│       ├── config.h                     # Pinouts, SPI CS definitions, calibration
│       ├── state_machine.h / .cpp       # 4-state FSM (AVAILABLE, RESERVED, CHARGING, FAULTED)
│       ├── peripherals.h / .cpp         # OLED, TFT, Keypad, RFID, Relay, ADC, SD
│       └── mqtt_handler.h / .cpp        # PubSubClient topics, serialization/deserialization
│
├── mosquitto/                           # M2: MQTT Broker configuration [DONE]
│   └── config/
│       └── mosquitto.conf               # Ports 1883 (TCP) and 9001 (WebSockets)
│
├── smartreserve/
│   └── smartreserve_backend/
│       ├── mqtt_service.py              # M2: Async FastAPI MQTT client and topic handlers [DONE]
│       ├── ocpp_bridge.py               # M3: SteVe OCPP 1.6J CSMS and EVerest bridge [IN_PROGRESS]
│       ├── database.py                  # M2: SQLite multi-connector schema & migrations [DONE]
│       ├── main.py                      # M2: App startup, REST routes, MQTT integration [DONE]
│       └── websocket_manager.py         # M2: Live dashboard streaming [DONE]
│
└── tests/                               # E2E-Test Track: Automated Multi-Tier Verification [DONE]
    ├── conftest.py                      # Test fixtures, virtual broker, async clients
    ├── mock_esp32_hardware.py           # Virtual ESP32 state machine and MQTT simulator
    ├── test_tier1_features.py           # Tier 1: Feature Coverage (35 tests)
    ├── test_tier2_boundaries.py         # Tier 2: Boundary & Corner Cases (30 tests)
    ├── test_tier3_combined.py           # Tier 3: Cross-Feature Interactions (25 tests)
    ├── test_tier4_faults.py             # Tier 4: Real-World Scenarios & Fault Injection (25 tests)
    └── test_tier5_adversarial.py        # M4: Phase 2 Adversarial coverage hardening
```
