#ifndef EV_SARTHI_PERIPHERALS_H
#define EV_SARTHI_PERIPHERALS_H

#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_ILI9341.h>
#include <MFRC522.h>
#include <Keypad.h>

#include "config.h"
#include "state_machine.h"

// Sensor data structure
struct SensorReadings {
    int voltageRaw;
    int currentRaw;
    float voltageV;
    float currentA;
    float powerKW;
};

// Peripheral initialization & bus arbitration
void peripheralsInit();
bool isSdAvailable();

// Sensor acquisition & fast emergency trip check
SensorReadings readSensors();
bool checkEmergencyTrip(const SensorReadings& readings, const char*& outTripReason);

// Display update drivers
void updateOledDisplay(ConnectorState state, const SensorReadings& readings, float energyKWh);
void updateTftDisplay(ConnectorState state,
                      const String& enteredPin,
                      uint32_t reservationSecsRemaining,
                      uint32_t authWindowSecsRemaining,
                      bool isAuthWindowActive,
                      const SensorReadings& readings,
                      float energyKWh,
                      const char* alertMessage = nullptr);

// Local authentication input devices
char scanKeypad();
bool scanRfid(String& outUid);

// Relay direct state
void setRelayRaw(bool energized);
bool getRelayRaw();

// MicroSD persistent 11-column CSV telemetry logger
bool logTelemetryToSd(const char* isoTimestamp,
                      uint32_t uptimeMillis,
                      const char* stationId,
                      const char* connectorId,
                      ConnectorState state,
                      const SensorReadings& readings,
                      float energyKWh);

#endif // EV_SARTHI_PERIPHERALS_H
