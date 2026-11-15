#ifndef EV_SARTHI_STATE_MACHINE_H
#define EV_SARTHI_STATE_MACHINE_H

#include <Arduino.h>
#include "config.h"

// ==============================================================================
// 4-STATE FINITE STATE MACHINE (FSM)
// AVAILABLE, RESERVED, CHARGING, FAULTED
// ==============================================================================

enum ConnectorState {
    AVAILABLE = 0,
    RESERVED  = 1,
    CHARGING  = 2,
    FAULTED   = 3
};

// State change callback type for external notifications (MQTT, Display, Log)
typedef void (*StateChangeCallback)(ConnectorState oldState, ConnectorState newState, const char* reason);

void fsmInit(StateChangeCallback callback = nullptr);
ConnectorState fsmGetState();
const char* fsmGetStateString();
const char* fsmStateToString(ConnectorState state);

void fsmTransitionTo(ConnectorState newState, const char* reason);
void fsmStartReservation(const char* resId, const char* pin, const char* rfidUid, uint32_t durationSecs);
void fsmCancelReservation(const char* reason);
void fsmTriggerAuthGrant(uint32_t windowDurationMs = AUTH_GRANT_WINDOW_MS);
void fsmTriggerEmergencyTrip(const char* reason);

void fsmUpdate(int currentRaw, int voltageRaw);

bool fsmIsAuthWindowActive();
uint32_t fsmGetRemainingReservationSeconds();
uint32_t fsmGetRemainingAuthWindowSeconds();
float fsmGetAccumulatedEnergyKWh();
void fsmAccumulateEnergy(float powerKW, uint32_t deltaMillis);
void fsmResetEnergy();
const char* fsmGetTransitionReason();
const char* fsmGetAuthorizedPin();
const char* fsmGetAuthorizedRfid();
const char* fsmGetReservationId();

#endif // EV_SARTHI_STATE_MACHINE_H
