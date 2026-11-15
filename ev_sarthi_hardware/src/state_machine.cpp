#include "state_machine.h"

static ConnectorState currentState = AVAILABLE;
static StateChangeCallback onStateChangeCallback = nullptr;

static uint32_t reservationExpiryMillis = 0;
static uint32_t authWindowExpiryMillis = 0;
static bool isAuthWindowActive = false;

static String currentReservationId = "";
static String authorizedPin = DEMO_PIN_DEFAULT;
static String authorizedRfid = "DEADBEEF";
static String lastTransitionReason = "SYSTEM_BOOT";
static float accumulatedEnergyKWh = 0.0f;
static uint32_t lastEnergyCalcMillis = 0;

const char* fsmStateToString(ConnectorState state) {
    switch (state) {
        case AVAILABLE: return "AVAILABLE";
        case RESERVED:  return "RESERVED";
        case CHARGING:  return "CHARGING";
        case FAULTED:   return "FAULTED";
        default:        return "UNKNOWN";
    }
}

const char* fsmGetStateString() {
    return fsmStateToString(currentState);
}

ConnectorState fsmGetState() {
    return currentState;
}

const char* fsmGetTransitionReason() {
    return lastTransitionReason.c_str();
}

const char* fsmGetAuthorizedPin() {
    return authorizedPin.c_str();
}

const char* fsmGetAuthorizedRfid() {
    return authorizedRfid.c_str();
}

const char* fsmGetReservationId() {
    return currentReservationId.c_str();
}

bool fsmIsAuthWindowActive() {
    return isAuthWindowActive;
}

float fsmGetAccumulatedEnergyKWh() {
    return accumulatedEnergyKWh;
}

void fsmAccumulateEnergy(float powerKW, uint32_t deltaMillis) {
    if (powerKW > 0.0f && deltaMillis > 0) {
        // kWh = kW * (hours) = kW * (deltaMillis / 3,600,000)
        accumulatedEnergyKWh += powerKW * ((float)deltaMillis / 3600000.0f);
    }
}

void fsmResetEnergy() {
    accumulatedEnergyKWh = 0.0f;
}

uint32_t fsmGetRemainingReservationSeconds() {
    if (currentState != RESERVED || isAuthWindowActive) return 0;
    int32_t diff = (int32_t)(reservationExpiryMillis - millis());
    return (diff > 0) ? (uint32_t)(diff / 1000UL) : 0;
}

uint32_t fsmGetRemainingAuthWindowSeconds() {
    if (!isAuthWindowActive) return 0;
    int32_t diff = (int32_t)(authWindowExpiryMillis - millis());
    return (diff > 0) ? (uint32_t)((diff + 999UL) / 1000UL) : 0;
}

void fsmInit(StateChangeCallback callback) {
    onStateChangeCallback = callback;
    currentState = AVAILABLE;
    isAuthWindowActive = false;
    lastTransitionReason = "SYSTEM_INITIALIZED";
    accumulatedEnergyKWh = 0.0f;
    lastEnergyCalcMillis = millis();

    // Fail-safe default: Relay coil DE-ENERGIZED (LOW, Red LED ON, Contactor OPEN)
    digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);

    Serial.printf("[FSM] Initialized in %s state (Relay DE-ENERGIZED)\n", fsmGetStateString());
}

void fsmTransitionTo(ConnectorState newState, const char* reason) {
    ConnectorState oldState = currentState;
    currentState = newState;
    lastTransitionReason = (reason != nullptr) ? reason : "UNSPECIFIED";

    // Enforce Relay Fail-Safe Logic:
    // AVAILABLE, RESERVED, FAULTED: LOW (De-energized, Red LED ON)
    // CHARGING: HIGH (Energized, Green LED ON)
    if (newState == CHARGING) {
        digitalWrite(RELAY_PIN, RELAY_ENERGIZED);
        lastEnergyCalcMillis = millis();
    } else {
        if (!isAuthWindowActive) {
            digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
        }
    }

    if (newState != RESERVED) {
        isAuthWindowActive = false;
    }

    Serial.printf("[FSM] State Change: %s -> %s (Reason: %s, Relay: %s)\n",
                  fsmStateToString(oldState),
                  fsmStateToString(newState),
                  lastTransitionReason.c_str(),
                  (digitalRead(RELAY_PIN) == RELAY_ENERGIZED) ? "ENERGIZED" : "DE_ENERGIZED");

    if (onStateChangeCallback != nullptr) {
        onStateChangeCallback(oldState, newState, lastTransitionReason.c_str());
    }
}

void fsmStartReservation(const char* resId, const char* pin, const char* rfidUid, uint32_t durationSecs) {
    if (currentState == FAULTED) {
        Serial.println("[FSM] Reject reservation: Connector is FAULTED");
        return;
    }

    currentReservationId = (resId && strlen(resId) > 0) ? resId : "RES_DEFAULT";
    authorizedPin = (pin && strlen(pin) > 0) ? pin : DEMO_PIN_DEFAULT;
    authorizedRfid = (rfidUid && strlen(rfidUid) > 0) ? rfidUid : "DEADBEEF";

    uint32_t durationMs = (durationSecs > 0) ? (durationSecs * 1000UL) : DEFAULT_RESERVATION_TIME_MS;
    reservationExpiryMillis = millis() + durationMs;
    isAuthWindowActive = false;

    fsmTransitionTo(RESERVED, "RESERVATION_CREATED");
}

void fsmCancelReservation(const char* reason) {
    if (currentState == RESERVED) {
        isAuthWindowActive = false;
        fsmTransitionTo(AVAILABLE, (reason && strlen(reason) > 0) ? reason : "RESERVATION_CANCELLED");
    }
}

void fsmTriggerAuthGrant(uint32_t windowDurationMs) {
    if (currentState != RESERVED) {
        Serial.println("[FSM] Auth grant ignored: Not in RESERVED state");
        return;
    }

    isAuthWindowActive = true;
    authWindowExpiryMillis = millis() + windowDurationMs;

    // Energize relay during the 8-second plug-in window so vehicle detects line potential
    digitalWrite(RELAY_PIN, RELAY_ENERGIZED);

    Serial.printf("[FSM] Auth Granted: 8s Cable Insertion Window Active (Relay ENERGIZED)\n");
}

void fsmTriggerEmergencyTrip(const char* reason) {
    // Instant hardware trip (<50ms): drop relay immediately
    digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
    isAuthWindowActive = false;
    fsmTransitionTo(FAULTED, (reason && strlen(reason) > 0) ? reason : "EMERGENCY_FAST_TRIP");
}

void fsmUpdate(int currentRaw, int voltageRaw) {
    uint32_t now = millis();

    // 1. Check active 8s auth grant window
    if (currentState == RESERVED && isAuthWindowActive) {
        // Sensed current check: > 0.8A (raw >= 100) confirms vehicle cable plugged in
        if (currentRaw >= PLUG_DETECT_RAW_CURR) {
            isAuthWindowActive = false;
            fsmTransitionTo(CHARGING, "VEHICLE_PLUGGED_IN");
            return;
        }

        // Timeout check: window expired without vehicle connection
        if ((int32_t)(now - authWindowExpiryMillis) >= 0) {
            isAuthWindowActive = false;
            digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
            fsmTransitionTo(AVAILABLE, "AUTH_WINDOW_TIMEOUT_NO_PLUG");
            return;
        }
    }

    // 2. Check 60s reservation countdown timer
    if (currentState == RESERVED && !isAuthWindowActive) {
        if ((int32_t)(now - reservationExpiryMillis) >= 0) {
            fsmTransitionTo(AVAILABLE, "RESERVATION_TIMEOUT");
            return;
        }
    }

    // 3. Accumulate energy in CHARGING state
    if (currentState == CHARGING) {
        uint32_t deltaMillis = now - lastEnergyCalcMillis;
        lastEnergyCalcMillis = now;

        float v = ((float)voltageRaw / ADC_MAX_RAW) * VOLTAGE_MAX_V;
        float a = ((float)currentRaw / ADC_MAX_RAW) * CURRENT_MAX_A;
        float kw = (v * a) / 1000.0f;
        fsmAccumulateEnergy(kw, deltaMillis);
    }
}
