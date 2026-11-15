#include "mqtt_handler.h"

static WiFiClient wifiClient;
static PubSubClient mqttClient(wifiClient);

static String currentStationId = STATION_ID_DEFAULT;
static String currentConnectorId = CONNECTOR_ID_DEFAULT;
static String currentBrokerHost = MQTT_BROKER_DEFAULT;
static uint16_t currentBrokerPort = MQTT_PORT_DEFAULT;

static String topicReserve;
static String topicState;
static String topicTelemetry;
static String topicAuth;
static String topicCmd;

static uint32_t lastMqttRetryMillis = 0;
static uint32_t lastWifiRetryMillis = 0;

static void onMqttMessageReceived(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT] Message arrived on topic: %s (length: %u)\n", topic, length);

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[MQTT] JSON parse error: %s\n", err.c_str());
        return;
    }

    // 1. Handle Reservation topic: evsarthi/{station}/{connector}/reserve
    if (String(topic) == topicReserve) {
        const char* action = doc["action"] | "";
        Serial.printf("[MQTT] /reserve action: %s\n", action);

        if (strcmp(action, "CREATE") == 0) {
            const char* resId = doc["reservation_id"] | "RES_REMOTE";
            const char* pin = doc["pin"] | DEMO_PIN_DEFAULT;
            const char* rfid = doc["rfid_uid"] | "DEADBEEF";
            uint32_t duration = doc["duration_seconds"] | 60;
            fsmStartReservation(resId, pin, rfid, duration);
        } else if (strcmp(action, "CANCEL") == 0) {
            fsmCancelReservation("BACKEND_REMOTE_CANCEL");
        }
    }
    // 2. Handle Command topic: evsarthi/{station}/{connector}/cmd
    else if (String(topic) == topicCmd) {
        const char* action = doc["action"] | "";
        const char* status = doc["status"] | "";
        uint32_t authWindowMs = doc["auth_window_duration_ms"] | AUTH_GRANT_WINDOW_MS;
        const char* relayAction = doc["relay_action"] | "";

        Serial.printf("[MQTT] /cmd received - action: %s, status: %s, relay: %s\n", action, status, relayAction);

        if (strcmp(action, "AUTH_DECISION") == 0) {
            if (strcmp(status, "ACCEPTED") == 0) {
                fsmTriggerAuthGrant(authWindowMs);
            } else if (strcmp(status, "DENIED") == 0) {
                Serial.println("[MQTT] Authorization explicitly DENIED by backend");
            }
        }

        if (strcmp(relayAction, "DE_ENERGIZE") == 0 || strcmp(action, "STOP_CHARGING") == 0) {
            if (fsmGetState() == CHARGING) {
                fsmTransitionTo(AVAILABLE, "REMOTE_COMMAND_STOP");
            }
        }
    }
}

void mqttInit(const char* stationId, const char* connectorId, const char* brokerHost, uint16_t brokerPort) {
    currentStationId = (stationId && strlen(stationId) > 0) ? stationId : STATION_ID_DEFAULT;
    currentConnectorId = (connectorId && strlen(connectorId) > 0) ? connectorId : CONNECTOR_ID_DEFAULT;
    currentBrokerHost = (brokerHost && strlen(brokerHost) > 0) ? brokerHost : MQTT_BROKER_DEFAULT;
    currentBrokerPort = (brokerPort > 0) ? brokerPort : MQTT_PORT_DEFAULT;

    // Construct full topic trees
    topicReserve   = "evsarthi/" + currentStationId + "/" + currentConnectorId + "/reserve";
    topicState     = "evsarthi/" + currentStationId + "/" + currentConnectorId + "/state";
    topicTelemetry = "evsarthi/" + currentStationId + "/" + currentConnectorId + "/telemetry";
    topicAuth      = "evsarthi/" + currentStationId + "/" + currentConnectorId + "/auth";
    topicCmd       = "evsarthi/" + currentStationId + "/" + currentConnectorId + "/cmd";

    mqttClient.setServer(currentBrokerHost.c_str(), currentBrokerPort);
    mqttClient.setCallback(onMqttMessageReceived);
    mqttClient.setBufferSize(MQTT_BUFFER_SIZE_DEF); // 512 bytes buffer

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID_DEFAULT, WIFI_PASS_DEFAULT);
    Serial.printf("[WIFI] Connecting to SSID: %s\n", WIFI_SSID_DEFAULT);
}

bool isWifiConnected() {
    return (WiFi.status() == WL_CONNECTED);
}

bool isMqttConnected() {
    return mqttClient.connected();
}

static void reconnectMqtt() {
    if (!isWifiConnected()) return;

    uint32_t now = millis();
    if (now - lastMqttRetryMillis < MQTT_RECONNECT_INTERVAL_MS) return;
    lastMqttRetryMillis = now;

    String clientId = "ESP32_EVSarthi_" + currentStationId + "_" + currentConnectorId + "_" + String(random(0xffff), HEX);
    Serial.printf("[MQTT] Attempting connection to %s:%u as %s...\n", currentBrokerHost.c_str(), currentBrokerPort, clientId.c_str());

    if (mqttClient.connect(clientId.c_str())) {
        Serial.println("[MQTT] Connected successfully!");

        // Subscribe to incoming operational topics
        mqttClient.subscribe(topicReserve.c_str());
        mqttClient.subscribe(topicCmd.c_str());

        Serial.printf("[MQTT] Subscribed to:\n  - %s\n  - %s\n", topicReserve.c_str(), topicCmd.c_str());

        // Publish initial state on connect (Retained = true)
        mqttPublishState(fsmGetState(), getRelayRaw(), fsmGetTransitionReason(), millis());
    } else {
        Serial.printf("[MQTT] Connect failed, rc=%d (will retry non-blockingly)\n", mqttClient.state());
    }
}

void mqttUpdate() {
    // Non-blocking WiFi reconnect check
    if (WiFi.status() != WL_CONNECTED) {
        uint32_t now = millis();
        if (now - lastWifiRetryMillis >= 10000UL) {
            lastWifiRetryMillis = now;
            Serial.println("[WIFI] Re-checking connection...");
            WiFi.reconnect();
        }
        return;
    }

    if (!mqttClient.connected()) {
        reconnectMqtt();
    } else {
        mqttClient.loop();
    }
}

bool mqttPublishState(ConnectorState state, bool relayEnergized, const char* reason, uint32_t uptimeMillis) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<256> doc;
    doc["state"] = fsmStateToString(state);
    doc["relay_state"] = relayEnergized ? "ENERGIZED" : "DE_ENERGIZED";
    doc["transition_reason"] = (reason != nullptr) ? reason : "UNSPECIFIED";
    doc["uptime_millis"] = uptimeMillis;

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    bool pubResult = mqttClient.publish(topicState.c_str(), (const uint8_t*)buffer, len, true); // Retained

    Serial.printf("[MQTT] Published /state: %s\n", buffer);
    return pubResult;
}

bool mqttPublishTelemetry(const SensorReadings& readings, float energyKWh, uint32_t uptimeMillis) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<256> doc;
    doc["voltage_v"] = serialized(String(readings.voltageV, 2));
    doc["current_a"] = serialized(String(readings.currentA, 2));
    doc["power_kw"] = serialized(String(readings.powerKW, 3));
    doc["energy_kwh"] = serialized(String(energyKWh, 4));
    doc["voltage_raw"] = readings.voltageRaw;
    doc["current_raw"] = readings.currentRaw;
    doc["millis"] = uptimeMillis;

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    bool pubResult = mqttClient.publish(topicTelemetry.c_str(), (const uint8_t*)buffer, len, false);

    Serial.printf("[MQTT] Published /telemetry: %s\n", buffer);
    return pubResult;
}

bool mqttPublishAuth(const char* authType, const char* pin, const char* rfidUid, ConnectorState currentState) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<256> doc;
    doc["auth_type"] = (authType != nullptr) ? authType : "PIN";
    JsonObject cred = doc.createNestedObject("credential");
    cred["pin"] = (pin != nullptr) ? pin : "";
    cred["rfid_uid"] = (rfidUid != nullptr) ? rfidUid : "";
    doc["current_state"] = fsmStateToString(currentState);

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    bool pubResult = mqttClient.publish(topicAuth.c_str(), (const uint8_t*)buffer, len, false);

    Serial.printf("[MQTT] Published /auth: %s\n", buffer);
    return pubResult;
}
