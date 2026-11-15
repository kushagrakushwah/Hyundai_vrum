#include <Arduino.h>
#include "config.h"
#include "state_machine.h"
#include "peripherals.h"
#include "mqtt_handler.h"

static String enteredPin = "";
static uint32_t lastTelemetryMillis = 0;
static uint32_t lastOledRefreshMillis = 0;
static uint32_t lastTftRefreshMillis = 0;

// Callback triggered whenever the FSM transitions between states
void handleStateTransition(ConnectorState oldState, ConnectorState newState, const char* reason) {
    if (newState != RESERVED) {
        enteredPin = "";
    }

    // Publish state update over MQTT (retained = true)
    mqttPublishState(newState, getRelayRaw(), reason, millis());

    // Immediately refresh UI displays
    SensorReadings readings = readSensors();
    updateOledDisplay(newState, readings, fsmGetAccumulatedEnergyKWh());
    updateTftDisplay(newState,
                     enteredPin,
                     fsmGetRemainingReservationSeconds(),
                     fsmGetRemainingAuthWindowSeconds(),
                     fsmIsAuthWindowActive(),
                     readings,
                     fsmGetAccumulatedEnergyKWh());
}

void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println("\n=======================================================");
    Serial.println("  EV-SARTHI ESP32 EDGE FIRMWARE (MILESTONE 1)");
    Serial.println("  Shared SPI Bus Arbitration & Dual Authentication");
    Serial.println("=======================================================");

    // 1. Initialize Peripherals (Fail-safe relay, SPI bus, OLED, TFT, RFID, MicroSD)
    peripheralsInit();

    // 2. Initialize Finite State Machine
    fsmInit(handleStateTransition);

    // 3. Initialize WiFi and MQTT pub/sub client
    mqttInit(STATION_ID_DEFAULT, CONNECTOR_ID_DEFAULT, MQTT_BROKER_DEFAULT, MQTT_PORT_DEFAULT);

    Serial.println("[SETUP] EV-SARTHI Firmware Ready.");
    Serial.println("Commands:");
    Serial.println("  R = Create 60s Demo Reservation");
    Serial.println("  A = Set Available");
    Serial.println("  F = Inject Fault (Emergency Trip)");
    Serial.println("  E = Expire Reservation");
    Serial.println("  S = Stop Charging");
    Serial.println("=======================================================\n");
}

void loop() {
    // =========================================================================
    // STEP 1: FAST EMERGENCY HARDWARE TRIP PROTECTION (<50ms)
    // Runs at the head of every cycle before anything else
    // =========================================================================
    SensorReadings readings = readSensors();
    const char* tripReason = nullptr;

    if (checkEmergencyTrip(readings, tripReason)) {
        if (fsmGetState() != FAULTED) {
            Serial.printf("[FAST-TRIP] Emergency condition tripped: %s (V-raw: %d, I-raw: %d)\n",
                          tripReason, readings.voltageRaw, readings.currentRaw);
            fsmTriggerEmergencyTrip(tripReason);
        }
    }

    // =========================================================================
    // STEP 2: NETWORK & MQTT SERVICING
    // =========================================================================
    mqttUpdate();

    // =========================================================================
    // STEP 3: STATE MACHINE LIFECYCLE (Timers, Plug detection, Energy)
    // =========================================================================
    fsmUpdate(readings.currentRaw, readings.voltageRaw);

    // =========================================================================
    // STEP 4: USER AUTHENTICATION & INTERACTIVE PERIPHERALS
    // =========================================================================
    ConnectorState currentState = fsmGetState();

    if (currentState == RESERVED && !fsmIsAuthWindowActive()) {
        // --- 4A. MFRC522 Contactless RFID Reader ---
        String scannedRfidUid;
        if (scanRfid(scannedRfidUid)) {
            // Forward /auth credential to MQTT broker
            mqttPublishAuth("RFID", "", scannedRfidUid.c_str(), currentState);

            // Compare against active reservation RFID or fallback default
            if (scannedRfidUid.equalsIgnoreCase(fsmGetAuthorizedRfid()) ||
                scannedRfidUid.equalsIgnoreCase("DEADBEEF")) {
                Serial.println("[AUTH] RFID Authorization ACCEPTED locally");
                fsmTriggerAuthGrant();
            } else {
                Serial.println("[AUTH] RFID Authorization DENIED");
                updateTftDisplay(currentState, enteredPin, fsmGetRemainingReservationSeconds(),
                                 fsmGetRemainingAuthWindowSeconds(), fsmIsAuthWindowActive(),
                                 readings, fsmGetAccumulatedEnergyKWh(), "RFID REJECTED");
                delay(1200);
            }
        }

        // --- 4B. 3x4 Matrix Keypad PIN Entry ---
        char key = scanKeypad();
        if (key) {
            if (key >= '0' && key <= '9') {
                if (enteredPin.length() < 6) {
                    enteredPin += key;
                }
            } else if (key == '*') {
                enteredPin = "";
            } else if (key == '#') {
                if (enteredPin.length() > 0) {
                    // Forward /auth credential to MQTT broker
                    mqttPublishAuth("PIN", enteredPin.c_str(), "", currentState);

                    // Compare against active reservation PIN or fallback default
                    if (enteredPin == fsmGetAuthorizedPin() || enteredPin == DEMO_PIN_DEFAULT) {
                        Serial.println("[AUTH] PIN Authorization ACCEPTED locally");
                        fsmTriggerAuthGrant();
                    } else {
                        Serial.println("[AUTH] PIN Authorization DENIED");
                        updateTftDisplay(currentState, enteredPin, fsmGetRemainingReservationSeconds(),
                                         fsmGetRemainingAuthWindowSeconds(), fsmIsAuthWindowActive(),
                                         readings, fsmGetAccumulatedEnergyKWh(), "INVALID PIN");
                        delay(1200);
                    }
                    enteredPin = "";
                }
            }
        }
    } else if (currentState == CHARGING) {
        // Allow driver to terminate charging session by pressing '#' on Keypad
        char key = scanKeypad();
        if (key == '#') {
            Serial.println("[CHARGING] Session stopped by user via Keypad '#'");
            fsmTransitionTo(AVAILABLE, "USER_KEYPAD_STOP");
        }
    }

    // =========================================================================
    // STEP 5: SERIAL DEBUG & TEST INTERFACE
    // =========================================================================
    if (Serial.available()) {
        char cmd = Serial.read();
        switch (cmd) {
            case 'R':
            case 'r':
                Serial.println("[SERIAL] Manual Trigger: Create Reservation");
                fsmStartReservation("RES_SERIAL", DEMO_PIN_DEFAULT, "DEADBEEF", 60);
                break;
            case 'A':
            case 'a':
                Serial.println("[SERIAL] Manual Trigger: Set AVAILABLE");
                fsmTransitionTo(AVAILABLE, "SERIAL_MANUAL_AVAILABLE");
                break;
            case 'F':
            case 'f':
                Serial.println("[SERIAL] Manual Trigger: Emergency Fault Injection");
                fsmTriggerEmergencyTrip("SERIAL_FAULT_INJECTION");
                break;
            case 'E':
            case 'e':
                if (fsmGetState() == RESERVED) {
                    Serial.println("[SERIAL] Manual Trigger: Cancel/Expire Reservation");
                    fsmCancelReservation("SERIAL_MANUAL_CANCEL");
                }
                break;
            case 'S':
            case 's':
                if (fsmGetState() == CHARGING) {
                    Serial.println("[SERIAL] Manual Trigger: Stop Charging");
                    fsmTransitionTo(AVAILABLE, "SERIAL_MANUAL_STOP");
                }
                break;
        }
    }

    // =========================================================================
    // STEP 6: PERIODIC TELEMETRY (MicroSD 11-column CSV & MQTT)
    // Every TELEMETRY_INTERVAL_MS (2000ms)
    // =========================================================================
    uint32_t now = millis();
    if (now - lastTelemetryMillis >= TELEMETRY_INTERVAL_MS) {
        lastTelemetryMillis = now;

        // 1. Persistent MicroSD 11-column CSV Logging with immediate flush
        logTelemetryToSd("2026-11-15T14:30:00Z",
                         now,
                         STATION_ID_DEFAULT,
                         CONNECTOR_ID_DEFAULT,
                         fsmGetState(),
                         readings,
                         fsmGetAccumulatedEnergyKWh());

        // 2. Publish MQTT Telemetry
        mqttPublishTelemetry(readings, fsmGetAccumulatedEnergyKWh(), now);

        Serial.printf("[TELEMETRY] State: %s | V: %.1fV | I: %.1fA | P: %.2fkW | E: %.3fkWh | Relay: %s\n",
                      fsmGetStateString(),
                      readings.voltageV,
                      readings.currentA,
                      readings.powerKW,
                      fsmGetAccumulatedEnergyKWh(),
                      getRelayRaw() ? "ENERGIZED" : "DE_ENERGIZED");
    }

    // =========================================================================
    // STEP 7: PERIODIC DISPLAY REFRESH
    // =========================================================================
    if (now - lastOledRefreshMillis >= OLED_REFRESH_INTERVAL_MS) {
        lastOledRefreshMillis = now;
        updateOledDisplay(fsmGetState(), readings, fsmGetAccumulatedEnergyKWh());
    }

    if (now - lastTftRefreshMillis >= 250UL) {
        lastTftRefreshMillis = now;
        updateTftDisplay(fsmGetState(),
                         enteredPin,
                         fsmGetRemainingReservationSeconds(),
                         fsmGetRemainingAuthWindowSeconds(),
                         fsmIsAuthWindowActive(),
                         readings,
                         fsmGetAccumulatedEnergyKWh());
    }
}
