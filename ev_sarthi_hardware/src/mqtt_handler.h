#ifndef EV_SARTHI_MQTT_HANDLER_H
#define EV_SARTHI_MQTT_HANDLER_H

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "config.h"
#include "state_machine.h"
#include "peripherals.h"

// MQTT subsystem initialization
void mqttInit(const char* stationId = STATION_ID_DEFAULT,
              const char* connectorId = CONNECTOR_ID_DEFAULT,
              const char* brokerHost = MQTT_BROKER_DEFAULT,
              uint16_t brokerPort = MQTT_PORT_DEFAULT);

// Non-blocking WiFi & MQTT loop
void mqttUpdate();

// Topic publishers matching interface contracts
bool mqttPublishState(ConnectorState state,
                      bool relayEnergized,
                      const char* reason,
                      uint32_t uptimeMillis);

bool mqttPublishTelemetry(const SensorReadings& readings,
                          float energyKWh,
                          uint32_t uptimeMillis);

bool mqttPublishAuth(const char* authType,
                     const char* pin,
                     const char* rfidUid,
                     ConnectorState currentState);

bool isMqttConnected();
bool isWifiConnected();

#endif // EV_SARTHI_MQTT_HANDLER_H
