# E2E Test Infra: EV Sarthi IoT & Ecosystem Integration

## Test Philosophy
- Opaque-box, requirement-driven. No dependency on implementation design.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial + Real-World Fault Injection & Workload Testing.
- Headless execution capability with high-fidelity ESP32 hardware state machine simulator and async MQTT / FastAPI test fixtures.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | Wokwi Simulation & PlatformIO Config | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 2 | Shared SPI Bus Arbitration | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 3 | Sensor Sampling & Calibration (ADC) | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 4 | Dual Display UI Drivers (OLED/TFT) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 5 | Dual User Authentication (Keypad/RFID) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 6 | Fail-Safe Relay & Status LEDs | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 7 | Firmware 4-State Machine | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 8 | 60-Second Reservation Countdown | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 9 | 8-Second Auth Grant Window | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 10 | MicroSD CSV Telemetry Logging | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 11 | Hardware Fault Trip Protection (<50ms) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 12 | Firmware WiFi & MQTT Client Pub/Sub | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 13 | Mosquitto MQTT Broker Configuration | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 14 | FastAPI Async MQTT Service | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 15 | Multi-Connector Database Schemas | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 16 | MQTT Telemetry Ingestion Pipeline | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 17 | MQTT Reservation Lifecycle Handler | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 18 | MQTT Authentication & Command Handler | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 19 | SteVe OCPP 1.6J Bridge | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 20 | EVerest Architectural Alignment | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |

## Test Architecture
- Test Runner: `pytest` with `pytest-asyncio`
- Test Directory: `c:\Users\kusha\OneDrive\Music\Documents\Projects\EV_Hyundai\tests\`
- Test Suites:
  - `conftest.py`: Async test fixtures, virtual MQTT message broker, test DB, FastAPI TestClient.
  - `mock_esp32_hardware.py`: High-fidelity virtual ESP32 simulator (`MockEVSarthiHardware`).
  - `test_tier1_features.py`: Tier 1 Feature Coverage (>=5 test cases per feature).
  - `test_tier2_boundaries.py`: Tier 2 Boundary & Corner Cases (60s countdown, 8s auth window, buffer overflow, ADC rails).
  - `test_tier3_combined.py`: Tier 3 Cross-Feature Interactions (multi-connector isolation, mid-countdown cancel, broker network partition, SteVe OCPP bridge).
  - `test_tier4_faults.py`: Tier 4 Real-World Application & Fault Scenarios (overcurrent trip <50ms, overvoltage, sensor loss, SD unmount, power loss recovery).
- Invocation Command: `pytest tests/ -v`

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Emergency Overcurrent Instant Trip (<50ms) | Current ADC, Trip Logic, Relay, FAULTED state, Telemetry | High |
| 2 | Grid Overvoltage Surge Protection | Voltage ADC, Trip Logic, Relay, LED indicators, MQTT State | High |
| 3 | Sensor Disconnect / ADC Loss Handling | ADC sampling, Zero/Rail detection, Safe default fallback | Medium |
| 4 | MicroSD Storage Drop / Buffer Resilience | SPI arbitration, CSV buffer caching, unmount recovery | High |
| 5 | Power Loss & Reboot State Recovery | Non-volatile state, MQTT retain flag, re-subscription | High |

## Coverage Thresholds
- Tier 1: ≥5 per feature
- Tier 2: ≥5 per feature (where boundaries exist)
- Tier 3: Pairwise coverage of major feature interactions
- Tier 4: ≥5 realistic application and fault injection scenarios
- Target Total: >100 distinct assertions / test cases passing with exit code 0
