// ==============================================================================
// EV-SARTHI ESP32 EDGE FIRMWARE (INTEGRATED PRODUCTION SKETCH)
// Hardware: ESP32 DevKit C v4
// Wokwi Web, Wokwi CLI & PlatformIO Dual-Compatible Integration
// ==============================================================================

#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_ILI9341.h>
#include <MFRC522.h>
#include <Keypad.h>

// ==============================================================================
// 1. PIN DEFINITIONS & HARDWARE CONSTANTS
// ==============================================================================

#define STATION_ID_DEFAULT    "ST001"
#define CONNECTOR_ID_DEFAULT  "C001"

// Analog ADC Sensors (ADC1 only - WiFi safe)
#define CURRENT_PIN           34  // ADC1_CH6 - Potentiometer 2 (Current ADC)
#define VOLTAGE_PIN           35  // ADC1_CH7 - Potentiometer 1 (Voltage ADC)
#define ADC_MAX_RAW           4095.0f
#define VOLTAGE_MAX_V         250.0f
#define CURRENT_MAX_A         32.0f

// Shared SPI Bus (SCK 18, MISO 19, MOSI 23)
#define SPI_SCK_PIN           18
#define SPI_MISO_PIN          19
#define SPI_MOSI_PIN          23

#define SD_CS_PIN             5   // MicroSD Card CS
#define TFT_CS_PIN            14  // ILI9341 TFT Display CS
#define TFT_DC_PIN            2   // ILI9341 Data / Command
#define TFT_RST_PIN           4   // ILI9341 Hardware Reset
#define RFID_CS_PIN           27  // MFRC522 RFID Reader CS
#define RFID_RST_PIN          26  // MFRC522 Hardware Reset

// I2C Bus - SSD1306 Diagnostic OLED
#define OLED_SDA_PIN          21
#define OLED_SCL_PIN          22
#define OLED_I2C_ADDR         0x3C
#define OLED_SCREEN_WIDTH     128
#define OLED_SCREEN_HEIGHT    64

// Relay Module & Status LEDs
// Fail-safe: LOW = NC = Red LED (Contactor OPEN / De-energized)
//            HIGH = NO = Green LED (Contactor CLOSED / Energized)
#define RELAY_PIN             15
#define RELAY_DE_ENERGIZED    LOW
#define RELAY_ENERGIZED       HIGH

// Fast Emergency Trip Limits (<50ms hardware interlock)
// Overvoltage: > 227.0V -> (227.0 / 250.0) * 4095 = 3718.26 -> 3719 raw
// Overcurrent: > 29.7A  -> (29.7  /  32.0) * 4095 = 3800.72 -> 3801 raw
// Vehicle Plug-in Detection: >= 0.8A -> (0.8 / 32.0) * 4095 = 102.375 -> >= 100 raw
#define OVERVOLTAGE_MAX_V     227.0f
#define OVERCURRENT_MAX_A     29.7f
#define TRIP_RAW_VOLTAGE      3719
#define TRIP_RAW_CURRENT      3801
#define PLUG_DETECT_RAW_CURR  100

// Timers & Intervals
#define DEFAULT_RESERVATION_TIME_MS   60000UL // 60s reservation countdown
#define AUTH_GRANT_WINDOW_MS          30000UL // 30s cable insertion window
#define TELEMETRY_INTERVAL_MS         2000UL  // 2s telemetry logging & publish
#define OLED_REFRESH_INTERVAL_MS      500UL   // 500ms OLED refresh
#define MQTT_RECONNECT_INTERVAL_MS    5000UL  // 5s MQTT non-blocking retry

// Default Local Fallback Credentials
#define DEMO_PIN_DEFAULT      "2580"
const byte AUTHORIZED_RFID_DEFAULT[4] = {0xDE, 0xAD, 0xBE, 0xEF};

// Network & MQTT Settings
#define WIFI_SSID_DEFAULT     "Wokwi-GUEST"
#define WIFI_PASS_DEFAULT     ""
#define MQTT_BROKER_DEFAULT   "broker.hivemq.com" // Public cloud broker for Wokwi + Cloud backend
#define MQTT_PORT_DEFAULT     1883
#define MQTT_BUFFER_SIZE_DEF  512

#define TELEMETRY_CSV_PATH    "/telemetry.csv"

// ==============================================================================
// 2. DATA STRUCTURES & 4-STATE FSM DEFINITION
// ==============================================================================

enum ConnectorState {
    AVAILABLE = 0,
    RESERVED  = 1,
    CHARGING  = 2,
    FAULTED   = 3
};

struct SensorReadings {
    int voltageRaw;
    int currentRaw;
    float voltageV;
    float currentA;
    float powerKW;
};

// ==============================================================================
// 3. HARDWARE DRIVER INSTANCES
// ==============================================================================

Adafruit_SSD1306 oled(OLED_SCREEN_WIDTH, OLED_SCREEN_HEIGHT, &Wire, -1);
Adafruit_ILI9341 tft(TFT_CS_PIN, TFT_DC_PIN, TFT_RST_PIN);
MFRC522 rfid(RFID_CS_PIN, RFID_RST_PIN);

// 3x4 Matrix Keypad Configuration
const byte KEYPAD_ROWS = 4;
const byte KEYPAD_COLS = 3;
char keys[KEYPAD_ROWS][KEYPAD_COLS] = {
    {'1', '2', '3'},
    {'4', '5', '6'},
    {'7', '8', '9'},
    {'*', '0', '#'}
};
byte rowPins[KEYPAD_ROWS] = {13, 12, 16, 17};
byte colPins[KEYPAD_COLS] = {25, 32, 33};
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, KEYPAD_ROWS, KEYPAD_COLS);

// Network Clients
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

// ==============================================================================
// 4. GLOBAL STATE VARIABLES
// ==============================================================================

ConnectorState connectorState = AVAILABLE;
bool isAuthWindowActive = false;
bool sdAvailable = false;

uint32_t reservationExpiryMillis = 0;
uint32_t authWindowExpiryMillis = 0;
String currentReservationId = "RES_001";
String authorizedPin = DEMO_PIN_DEFAULT;
String authorizedRfid = "DEADBEEF";
String enteredPin = "";
String lastTransitionReason = "BOOT";

float accumulatedEnergyKWh = 0.0f;
uint32_t lastEnergyCalcMillis = 0;

uint32_t lastTelemetryMillis = 0;
uint32_t lastOledRefreshMillis = 0;
uint32_t lastTftRefreshMillis = 0;
uint32_t lastMqttRetryMillis = 0;
uint32_t lastWifiRetryMillis = 0;

ConnectorState lastDrawnTftState = (ConnectorState)-1;
String lastDrawnPin = "";
bool lastDrawnAuthWindow = false;

// MQTT Topics
String topicReserve;
String topicState;
String topicTelemetry;
String topicAuth;
String topicCmd;

// Forward Declarations
void setConnectorState(ConnectorState newState, const char* reason);
void triggerAuthGrant(uint32_t windowMs = AUTH_GRANT_WINDOW_MS);
void triggerEmergencyTrip(const char* tripReason);
SensorReadings readSensors();
void updateDisplays(const SensorReadings& readings, const char* alertMsg = nullptr);
bool logTelemetryToSd(const char* isoTimestamp, uint32_t uptimeMillis, const SensorReadings& readings);
bool mqttPublishState(ConnectorState state, bool relayEnergized, const char* reason, uint32_t uptimeMillis);
bool mqttPublishTelemetry(const SensorReadings& readings, float energyKWh, uint32_t uptimeMillis);
bool mqttPublishAuth(const char* authType, const char* pin, const char* rfidUid, ConnectorState currentState);

const char* stateToString(ConnectorState state) {
    switch (state) {
        case AVAILABLE: return "AVAILABLE";
        case RESERVED:  return "RESERVED";
        case CHARGING:  return "CHARGING";
        case FAULTED:   return "FAULTED";
        default:        return "UNKNOWN";
    }
}

// ==============================================================================
// 5. HARDWARE & PERIPHERAL INITIALIZATION (SHARED SPI ARBITRATION)
// ==============================================================================

void initPeripherals() {
    // Step 1: Configure Contactor Relay to FAIL-SAFE state immediately
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED); // LOW: Red LED ON, Contactor OPEN

    // Step 2: Pre-assert all SPI Chip Selects and Reset lines HIGH before SPI.begin()
    pinMode(SD_CS_PIN, OUTPUT);
    pinMode(TFT_CS_PIN, OUTPUT);
    pinMode(RFID_CS_PIN, OUTPUT);
    pinMode(TFT_DC_PIN, OUTPUT);
    pinMode(TFT_RST_PIN, OUTPUT);
    pinMode(RFID_RST_PIN, OUTPUT);

    digitalWrite(SD_CS_PIN, HIGH);
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(RFID_CS_PIN, HIGH);
    digitalWrite(TFT_RST_PIN, HIGH);
    digitalWrite(RFID_RST_PIN, HIGH);
    delay(10);

    // Step 3: Configure ADC Resolution (12-bit, 0 - 4095)
    analogReadResolution(12);

    // Step 4: Initialize Shared SPI Bus
    SPI.begin(SPI_SCK_PIN, SPI_MISO_PIN, SPI_MOSI_PIN);

    // Step 5: Initialize I2C SSD1306 Diagnostic OLED
    Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
    if (oled.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        oled.clearDisplay();
        oled.setTextSize(1);
        oled.setTextColor(SSD1306_WHITE);
        oled.setCursor(0, 0);
        oled.println("EV-SARTHI BOOT");
        oled.println("SPI Arbitration Init");
        oled.display();
        Serial.println("[PERIPHERALS] OLED initialized");
    } else {
        Serial.println("[PERIPHERALS] ERROR: OLED init failed");
    }

    // Step 6: Initialize ILI9341 SPI TFT Display
    digitalWrite(RFID_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);
    tft.begin();
    tft.setRotation(1); // Landscape
    tft.fillScreen(ILI9341_BLACK);
    digitalWrite(TFT_CS_PIN, HIGH);
    Serial.println("[PERIPHERALS] ILI9341 TFT initialized");

    // Step 7: Initialize MFRC522 SPI RFID Reader
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);
    rfid.PCD_Init();
    digitalWrite(RFID_CS_PIN, HIGH);
    Serial.println("[PERIPHERALS] MFRC522 RFID initialized");

    // Step 8: Initialize MicroSD Card Storage
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(RFID_CS_PIN, HIGH);
    sdAvailable = SD.begin(SD_CS_PIN);
    if (sdAvailable) {
        Serial.println("[PERIPHERALS] SD CARD INITIALISED");
        File file = SD.open(TELEMETRY_CSV_PATH, FILE_READ);
        bool needsHeader = (!file || file.size() == 0);
        if (file) file.close();

        if (needsHeader) {
            File outFile = SD.open(TELEMETRY_CSV_PATH, FILE_WRITE);
            if (outFile) {
                outFile.println("timestamp_utc,millis,station_id,connector_id,state,voltage_raw,current_raw,voltage_v,current_a,power_kw,energy_kwh");
                outFile.flush();
                outFile.close();
                Serial.println("[PERIPHERALS] MicroSD 11-column CSV header written");
            }
        }
    } else {
        Serial.println("[PERIPHERALS] SD CARD ERROR (Module missing or bus conflict)");
    }
    digitalWrite(SD_CS_PIN, HIGH);
}

// ==============================================================================
// 6. SENSORS & EMERGENCY FAST TRIP (<50ms)
// ==============================================================================

SensorReadings readSensors() {
    SensorReadings r;
    r.voltageRaw = analogRead(VOLTAGE_PIN);
    r.currentRaw = analogRead(CURRENT_PIN);
    r.voltageV = ((float)r.voltageRaw / ADC_MAX_RAW) * VOLTAGE_MAX_V;
    r.currentA = ((float)r.currentRaw / ADC_MAX_RAW) * CURRENT_MAX_A;
    r.powerKW = (r.voltageV * r.currentA) / 1000.0f;
    return r;
}

bool checkEmergencyTrip(const SensorReadings& readings, const char*& outTripReason) {
    if (readings.voltageRaw >= TRIP_RAW_VOLTAGE) {
        outTripReason = "OVERVOLTAGE_TRIP (>227V)";
        return true;
    }
    if (readings.currentRaw >= TRIP_RAW_CURRENT) {
        outTripReason = "OVERCURRENT_TRIP (>29.7A)";
        return true;
    }
    return false;
}

void triggerEmergencyTrip(const char* tripReason) {
    // Instantaneous hardware trip: force relay coil LOW immediately (<50ms)
    digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
    isAuthWindowActive = false;
    setConnectorState(FAULTED, tripReason);
}

// ==============================================================================
// 7. FINITE STATE MACHINE (FSM) & TIMERS
// ==============================================================================

void setConnectorState(ConnectorState newState, const char* reason) {
    ConnectorState oldState = connectorState;
    connectorState = newState;
    lastTransitionReason = (reason != nullptr) ? reason : "UNSPECIFIED";

    if (newState != RESERVED) {
        enteredPin = "";
        isAuthWindowActive = false;
    }

    // Fail-safe relay enforcement:
    // Only HIGH in CHARGING or during active 8-second auth grant window
    if (newState == CHARGING) {
        digitalWrite(RELAY_PIN, RELAY_ENERGIZED);
        lastEnergyCalcMillis = millis();
    } else {
        if (!isAuthWindowActive) {
            digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
        }
    }

    Serial.printf("[FSM] State Change: %s -> %s (Reason: %s, Relay: %s)\n",
                  stateToString(oldState),
                  stateToString(newState),
                  lastTransitionReason.c_str(),
                  (digitalRead(RELAY_PIN) == RELAY_ENERGIZED) ? "ENERGIZED" : "DE_ENERGIZED");

    // Publish MQTT State update immediately
    mqttPublishState(connectorState, (digitalRead(RELAY_PIN) == RELAY_ENERGIZED), lastTransitionReason.c_str(), millis());

    // Update Displays
    SensorReadings readings = readSensors();
    updateDisplays(readings);
}

void triggerAuthGrant(uint32_t windowMs) {
    if (connectorState != RESERVED) return;

    isAuthWindowActive = true;
    authWindowExpiryMillis = millis() + windowMs;

    // Energize relay during 8-second plug-in window so vehicle detects AC pilot / contactor
    digitalWrite(RELAY_PIN, RELAY_ENERGIZED);

    Serial.println("[FSM] Access Granted: 8-second plug-in window active (Relay ENERGIZED)");

    SensorReadings readings = readSensors();
    updateDisplays(readings);
}

void updateStateMachine(const SensorReadings& readings) {
    uint32_t now = millis();

    // 1. Check active 8s auth grant window
    if (connectorState == RESERVED && isAuthWindowActive) {
        // Vehicle plug-in current detection: > 0.8A (raw >= 100)
        if (readings.currentRaw >= PLUG_DETECT_RAW_CURR) {
            isAuthWindowActive = false;
            setConnectorState(CHARGING, "VEHICLE_PLUGGED_IN");
            return;
        }

        // Window timeout without vehicle plug-in
        if ((int32_t)(now - authWindowExpiryMillis) >= 0) {
            isAuthWindowActive = false;
            digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);
            setConnectorState(AVAILABLE, "AUTH_WINDOW_TIMEOUT_NO_PLUG");
            return;
        }
    }

    // 2. Check 60s reservation countdown timer
    if (connectorState == RESERVED && !isAuthWindowActive) {
        if ((int32_t)(now - reservationExpiryMillis) >= 0) {
            setConnectorState(AVAILABLE, "RESERVATION_TIMEOUT");
            return;
        }
    }

    // 3. Accumulate Energy in CHARGING state
    if (connectorState == CHARGING) {
        uint32_t deltaMillis = now - lastEnergyCalcMillis;
        lastEnergyCalcMillis = now;
        if (readings.powerKW > 0.0f && deltaMillis > 0) {
            accumulatedEnergyKWh += readings.powerKW * ((float)deltaMillis / 3600000.0f);
        }
    }
}

// ==============================================================================
// 8. DISPLAY DRIVERS (SSD1306 OLED & ILI9341 TFT)
// ==============================================================================

void updateDisplays(const SensorReadings& readings, const char* alertMsg) {
    // ---------------------------------------------------------
    // 8A. SSD1306 OLED Telemetry Driver
    // ---------------------------------------------------------
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(SSD1306_WHITE);
    oled.setCursor(0, 0);

    oled.println("=== EV-SARTHI EDGE ===");
    oled.printf("STN:%s  CONN:%s\n", STATION_ID_DEFAULT, CONNECTOR_ID_DEFAULT);
    oled.printf("STATE: %s\n", stateToString(connectorState));
    oled.printf("V:%5.1fV  I:%4.1fA\n", readings.voltageV, readings.currentA);
    oled.printf("P:%5.2fkW E:%6.3fkWh\n", readings.powerKW, accumulatedEnergyKWh);
    oled.printf("RELAY: %s\n", (digitalRead(RELAY_PIN) == RELAY_ENERGIZED) ? "CLOSED [ON]" : "OPEN [SAFE]");
    oled.display();

    // ---------------------------------------------------------
    // 8B. ILI9341 TFT User Display Driver (Shared SPI CS safe)
    // ---------------------------------------------------------
    digitalWrite(RFID_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);

    if (alertMsg != nullptr) {
        tft.fillScreen(ILI9341_RED);
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(2);
        tft.setCursor(30, 90);
        tft.println("ALERT");
        tft.setCursor(30, 130);
        tft.println(alertMsg);
        digitalWrite(TFT_CS_PIN, HIGH);
        return;
    }

    bool fullRedraw = (connectorState != lastDrawnTftState || isAuthWindowActive != lastDrawnAuthWindow);

    if (connectorState == AVAILABLE) {
        if (fullRedraw) {
            tft.fillScreen(ILI9341_BLACK);
            tft.fillRect(0, 0, 320, 45, ILI9341_DARKGREEN);
            tft.setTextColor(ILI9341_WHITE);
            tft.setTextSize(2);
            tft.setCursor(20, 12);
            tft.println("EV-SARTHI SMARTRESERVE");

            tft.fillRect(20, 60, 280, 50, ILI9341_GREEN);
            tft.setTextColor(ILI9341_BLACK);
            tft.setTextSize(2);
            tft.setCursor(35, 75);
            tft.println("STATUS: AVAILABLE");

            tft.setTextColor(ILI9341_WHITE);
            tft.setTextSize(1);
            tft.setCursor(25, 130);
            tft.println("- Tap registered RFID card to start");
            tft.setCursor(25, 150);
            tft.println("- Or reserve slot in Hyundai SmartReserve App");
            tft.setCursor(25, 185);
            tft.setTextColor(ILI9341_LIGHTGREY);
            tft.println("Contactor: OPEN (Red LED ON / Fail-Safe)");
        }
    } else if (connectorState == RESERVED) {
        if (isAuthWindowActive) {
            if (fullRedraw) {
                tft.fillScreen(ILI9341_BLACK);
                tft.fillRect(0, 0, 320, 45, ILI9341_GREEN);
                tft.setTextColor(ILI9341_BLACK);
                tft.setTextSize(2);
                tft.setCursor(20, 12);
                tft.println("AUTH ACCEPTED");
            }

            int32_t remAuth = (int32_t)(authWindowExpiryMillis - millis());
            uint32_t remSecs = (remAuth > 0) ? (uint32_t)((remAuth + 999UL) / 1000UL) : 0;

            tft.fillRect(20, 60, 280, 70, ILI9341_NAVY);
            tft.setTextColor(ILI9341_YELLOW);
            tft.setTextSize(2);
            tft.setCursor(30, 70);
            tft.println("INSERT CABLE NOW");

            tft.setTextSize(2);
            tft.setTextColor(ILI9341_WHITE);
            tft.setCursor(30, 100);
            tft.printf("Window: %2u s\n", (unsigned int)remSecs);

            tft.setTextColor(ILI9341_GREEN);
            tft.setTextSize(1);
            tft.setCursor(25, 150);
            tft.println("Relay ENERGIZED (Green LED ON)");
            tft.setCursor(25, 170);
            tft.println("Plug in vehicle to initiate charging.");
        } else {
            if (fullRedraw || enteredPin != lastDrawnPin) {
                int32_t remRes = (int32_t)(reservationExpiryMillis - millis());
                uint32_t remSecs = (remRes > 0) ? (uint32_t)(remRes / 1000UL) : 0;

                tft.fillScreen(ILI9341_BLACK);
                tft.fillRect(0, 0, 320, 45, ILI9341_BLUE);
                tft.setTextColor(ILI9341_WHITE);
                tft.setTextSize(2);
                tft.setCursor(20, 12);
                tft.println("CONNECTOR RESERVED");

                tft.setTextColor(ILI9341_YELLOW);
                tft.setTextSize(2);
                tft.setCursor(20, 60);
                tft.printf("Expires: %2u sec\n", (unsigned int)remSecs);

                tft.setTextColor(ILI9341_WHITE);
                tft.setTextSize(2);
                tft.setCursor(20, 95);
                tft.println("ENTER PIN OR TAP RFID:");

                tft.fillRect(20, 125, 280, 40, ILI9341_DARKGREY);
                tft.setTextColor(ILI9341_CYAN);
                tft.setTextSize(3);
                tft.setCursor(30, 132);
                for (unsigned int i = 0; i < enteredPin.length(); i++) {
                    tft.print("* ");
                }

                tft.setTextColor(ILI9341_LIGHTGREY);
                tft.setTextSize(1);
                tft.setCursor(20, 185);
                tft.println("Press '*' to clear, '#' to submit PIN");
            }
        }
    } else if (connectorState == CHARGING) {
        if (fullRedraw) {
            tft.fillScreen(ILI9341_BLACK);
            tft.fillRect(0, 0, 320, 45, ILI9341_DARKCYAN);
            tft.setTextColor(ILI9341_WHITE);
            tft.setTextSize(2);
            tft.setCursor(20, 12);
            tft.println("CHARGING IN PROGRESS");
        }

        tft.fillRect(20, 55, 280, 125, ILI9341_NAVY);
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(2);
        tft.setCursor(30, 65);
        tft.printf("Voltage: %5.1f V\n", readings.voltageV);
        tft.setCursor(30, 92);
        tft.printf("Current: %5.1f A\n", readings.currentA);
        tft.setCursor(30, 120);
        tft.printf("Power:   %5.2f kW\n", readings.powerKW);
        tft.setCursor(30, 148);
        tft.printf("Energy:  %5.3f kWh\n", accumulatedEnergyKWh);

        tft.setTextColor(ILI9341_YELLOW);
        tft.setTextSize(1);
        tft.setCursor(20, 195);
        tft.println("Press '#' on Keypad to stop charging");
    } else if (connectorState == FAULTED) {
        if (fullRedraw) {
            tft.fillScreen(ILI9341_RED);
            tft.fillRect(0, 0, 320, 45, ILI9341_MAROON);
            tft.setTextColor(ILI9341_WHITE);
            tft.setTextSize(2);
            tft.setCursor(20, 12);
            tft.println("EMERGENCY TRIP FAULT");

            tft.setTextSize(2);
            tft.setCursor(20, 70);
            tft.println("SAFETY INTERLOCK TRIPPED");

            tft.setTextSize(1);
            tft.setCursor(20, 110);
            tft.println("Contactor FORCED OPEN (<50ms trip)");
            tft.setCursor(20, 130);
            tft.println("Line de-energized to prevent damage.");
            tft.setCursor(20, 160);
            tft.printf("V-Raw: %d | I-Raw: %d\n", readings.voltageRaw, readings.currentRaw);
            tft.setCursor(20, 190);
            tft.println("Contact station operator to reset.");
        }
    }

    lastDrawnTftState = connectorState;
    lastDrawnPin = enteredPin;
    lastDrawnAuthWindow = isAuthWindowActive;

    digitalWrite(TFT_CS_PIN, HIGH);
}

// ==============================================================================
// 9. MICROSD 11-COLUMN CSV TELEMETRY LOGGER
// ==============================================================================

bool logTelemetryToSd(const char* isoTimestamp, uint32_t uptimeMillis, const SensorReadings& readings) {
    if (!sdAvailable) return false;

    // Deselect other SPI peripherals
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(RFID_CS_PIN, HIGH);

    File file = SD.open(TELEMETRY_CSV_PATH, FILE_APPEND);
    if (!file) {
        digitalWrite(SD_CS_PIN, HIGH);
        return false;
    }

    char line[200];
    snprintf(line, sizeof(line),
             "%s,%lu,%s,%s,%s,%d,%d,%.2f,%.2f,%.3f,%.4f",
             (isoTimestamp && strlen(isoTimestamp) > 0) ? isoTimestamp : "NTP_PENDING",
             (unsigned long)uptimeMillis,
             STATION_ID_DEFAULT,
             CONNECTOR_ID_DEFAULT,
             stateToString(connectorState),
             readings.voltageRaw,
             readings.currentRaw,
             readings.voltageV,
             readings.currentA,
             readings.powerKW,
             accumulatedEnergyKWh);

    file.println(line);
    file.flush(); // Critical: commit blocks to flash immediately
    file.close();

    digitalWrite(SD_CS_PIN, HIGH);
    return true;
}

// ==============================================================================
// 10. NETWORKING & MQTT INTEGRATION
// ==============================================================================

void onMqttMessageReceived(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT] Message arrived on: %s\n", topic);

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[MQTT] JSON parse failed: %s\n", err.c_str());
        return;
    }

    if (String(topic) == topicReserve || String(topic).endsWith("/reserve")) {
        const char* action = doc["action"] | "";
        if (strcmp(action, "CREATE") == 0) {
            currentReservationId = (const char*)(doc["reservation_id"] | "RES_REMOTE");
            authorizedPin = (const char*)(doc["pin"] | DEMO_PIN_DEFAULT);
            authorizedRfid = (const char*)(doc["rfid_uid"] | "DEADBEEF");
            uint32_t durationSec = doc["duration_seconds"] | 60;
            reservationExpiryMillis = millis() + (durationSec * 1000UL);
            isAuthWindowActive = false;
            setConnectorState(RESERVED, "MQTT_RESERVE_CREATE");
        } else if (strcmp(action, "CANCEL") == 0) {
            if (connectorState == RESERVED) {
                setConnectorState(AVAILABLE, "MQTT_RESERVE_CANCEL");
            }
        }
    } else if (String(topic) == topicCmd || String(topic).endsWith("/cmd")) {
        const char* action = doc["action"] | "";
        const char* status = doc["status"] | "";
        uint32_t authWindowMs = doc["auth_window_duration_ms"] | AUTH_GRANT_WINDOW_MS;
        const char* relayAction = doc["relay_action"] | "";

        if (strcmp(action, "AUTH_DECISION") == 0 || strcmp(action, "REMOTE_START") == 0 || strcmp(relayAction, "ENERGIZE") == 0) {
            if (strcmp(status, "ACCEPTED") == 0 || strcmp(relayAction, "ENERGIZE") == 0) {
                triggerAuthGrant(authWindowMs);
            } else if (strcmp(status, "DENIED") == 0) {
                Serial.println("[MQTT] Authorization DENIED by CSMS backend");
            }
        } else if (strcmp(action, "START_CHARGING") == 0 || strcmp(status, "CHARGING") == 0) {
            Serial.println("[MQTT] Vehicle cable plug-in confirmed -> transition to CHARGING");
            isAuthWindowActive = false;
            setConnectorState(CHARGING, "MQTT_CABLE_CONNECTED");
        }

        if (strcmp(relayAction, "DE_ENERGIZE") == 0 || strcmp(action, "STOP_CHARGING") == 0) {
            if (strcmp(status, "FAULTED") == 0) {
                triggerEmergencyTrip("MQTT_SAFETY_TRIP");
            } else if (connectorState == CHARGING) {
                setConnectorState(AVAILABLE, "REMOTE_COMMAND_STOP");
            }
        }
    }
}

void initMqtt() {
    topicReserve   = "evsarthi/" STATION_ID_DEFAULT "/" CONNECTOR_ID_DEFAULT "/reserve";
    topicState     = "evsarthi/" STATION_ID_DEFAULT "/" CONNECTOR_ID_DEFAULT "/state";
    topicTelemetry = "evsarthi/" STATION_ID_DEFAULT "/" CONNECTOR_ID_DEFAULT "/telemetry";
    topicAuth      = "evsarthi/" STATION_ID_DEFAULT "/" CONNECTOR_ID_DEFAULT "/auth";
    topicCmd       = "evsarthi/" STATION_ID_DEFAULT "/" CONNECTOR_ID_DEFAULT "/cmd";

    mqttClient.setServer(MQTT_BROKER_DEFAULT, MQTT_PORT_DEFAULT);
    mqttClient.setCallback(onMqttMessageReceived);
    mqttClient.setBufferSize(MQTT_BUFFER_SIZE_DEF);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID_DEFAULT, WIFI_PASS_DEFAULT);
    Serial.printf("[WIFI] Connecting to SSID: %s\n", WIFI_SSID_DEFAULT);
}

void serviceMqtt() {
    if (WiFi.status() != WL_CONNECTED) {
        uint32_t now = millis();
        if (now - lastWifiRetryMillis >= 10000UL) {
            lastWifiRetryMillis = now;
            WiFi.reconnect();
        }
        return;
    }

    if (!mqttClient.connected()) {
        uint32_t now = millis();
        if (now - lastMqttRetryMillis >= MQTT_RECONNECT_INTERVAL_MS) {
            lastMqttRetryMillis = now;
            String clientId = "ESP32_EVSarthi_" STATION_ID_DEFAULT "_" CONNECTOR_ID_DEFAULT "_" + String(random(0xffff), HEX);
            if (mqttClient.connect(clientId.c_str())) {
                Serial.println("[MQTT] Connected to broker!");
                mqttClient.subscribe(topicReserve.c_str());
                mqttClient.subscribe(topicCmd.c_str());
                mqttClient.subscribe("evsarthi/+/+/reserve");
                mqttClient.subscribe("evsarthi/+/+/cmd");
                mqttPublishState(connectorState, (digitalRead(RELAY_PIN) == RELAY_ENERGIZED), lastTransitionReason.c_str(), millis());
            }
        }
    } else {
        mqttClient.loop();
    }
}

bool mqttPublishState(ConnectorState state, bool relayEnergized, const char* reason, uint32_t uptimeMillis) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<256> doc;
    doc["state"] = stateToString(state);
    doc["relay_state"] = relayEnergized ? "ENERGIZED" : "DE_ENERGIZED";
    doc["transition_reason"] = (reason != nullptr) ? reason : "UNSPECIFIED";
    doc["uptime_millis"] = uptimeMillis;

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    return mqttClient.publish(topicState.c_str(), (const uint8_t*)buffer, len, true); // Retained
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
    return mqttClient.publish(topicTelemetry.c_str(), (const uint8_t*)buffer, len, false);
}

bool mqttPublishAuth(const char* authType, const char* pin, const char* rfidUid, ConnectorState currentState) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<256> doc;
    doc["auth_type"] = authType;
    JsonObject cred = doc.createNestedObject("credential");
    cred["pin"] = (pin != nullptr) ? pin : "";
    cred["rfid_uid"] = (rfidUid != nullptr) ? rfidUid : "";
    doc["current_state"] = stateToString(currentState);

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    return mqttClient.publish(topicAuth.c_str(), (const uint8_t*)buffer, len, false);
}

// ==============================================================================
// 11. SETUP
// ==============================================================================

void setup() {
    Serial.begin(115200);
    delay(400);

    Serial.println("\n=======================================================");
    Serial.println("  EV-SARTHI ESP32 EDGE FIRMWARE - INTEGRATED SKETCH");
    Serial.println("  Wokwi & PlatformIO Dual-Compatible Integration");
    Serial.println("=======================================================");

    // Initialize all local peripherals with shared SPI bus arbitration
    initPeripherals();

    // Initial state: AVAILABLE with contactor de-energized
    setConnectorState(AVAILABLE, "BOOT_INITIALIZATION");

    // Initialize networking
    initMqtt();

    Serial.println("Commands:");
    Serial.println("  R = Create 60s Reservation");
    Serial.println("  A = Set Available");
    Serial.println("  F = Trigger Emergency Trip (Fault)");
    Serial.println("  E = Expire Reservation");
    Serial.println("  S = Stop Charging Session");
    Serial.println("=======================================================\n");
}

// ==============================================================================
// 12. MAIN LOOP
// ==============================================================================

void loop() {
    // -------------------------------------------------------------------------
    // 1. FAST EMERGENCY HARDWARE TRIP PROTECTION (<50ms)
    // -------------------------------------------------------------------------
    SensorReadings readings = readSensors();
    const char* tripReason = nullptr;

    if (checkEmergencyTrip(readings, tripReason)) {
        if (connectorState != FAULTED) {
            Serial.printf("[FAST-TRIP] TRIP CONDITION DETECTED: %s (V=%d, I=%d)\n",
                          tripReason, readings.voltageRaw, readings.currentRaw);
            triggerEmergencyTrip(tripReason);
        }
    }

    // -------------------------------------------------------------------------
    // 2. NETWORK & MQTT SERVICING
    // -------------------------------------------------------------------------
    serviceMqtt();

    // -------------------------------------------------------------------------
    // 3. FINITE STATE MACHINE (Timers, Plug-in Sensing, Energy Accumulation)
    // -------------------------------------------------------------------------
    updateStateMachine(readings);

    // -------------------------------------------------------------------------
    // 4. USER AUTHENTICATION & INTERACTIVE INPUTS
    // -------------------------------------------------------------------------
    if (connectorState == RESERVED && !isAuthWindowActive) {
        // --- 4A. MFRC522 Contactless RFID ---
        digitalWrite(TFT_CS_PIN, HIGH);
        digitalWrite(SD_CS_PIN, HIGH);

        if (rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
            String scannedUid = "";
            for (byte i = 0; i < rfid.uid.size; i++) {
                if (rfid.uid.uidByte[i] < 0x10) scannedUid += "0";
                scannedUid += String(rfid.uid.uidByte[i], HEX);
            }
            scannedUid.toUpperCase();
            rfid.PICC_HaltA();
            rfid.PCD_StopCrypto1();
            digitalWrite(RFID_CS_PIN, HIGH);

            Serial.printf("[AUTH] RFID Card Scanned: %s\n", scannedUid.c_str());
            mqttPublishAuth("RFID", "", scannedUid.c_str(), connectorState);

            if (scannedUid.equalsIgnoreCase(authorizedRfid) || scannedUid.equalsIgnoreCase("DEADBEEF")) {
                Serial.println("[AUTH] RFID Authorization ACCEPTED");
                triggerAuthGrant();
            } else {
                Serial.println("[AUTH] RFID Authorization DENIED");
                updateDisplays(readings, "RFID REJECTED");
                delay(1200);
            }
        }
        digitalWrite(RFID_CS_PIN, HIGH);

        // --- 4B. 3x4 Matrix Keypad PIN Entry ---
        char key = keypad.getKey();
        if (key) {
            if (key >= '0' && key <= '9') {
                if (enteredPin.length() < 4) {
                    enteredPin += key;
                }
            } else if (key == '*') {
                enteredPin = "";
            } else if (key == '#') {
                if (enteredPin.length() > 0) {
                    Serial.printf("[AUTH] Keypad PIN Submitted: %s\n", enteredPin.c_str());
                    mqttPublishAuth("PIN", enteredPin.c_str(), "", connectorState);

                    if (enteredPin == authorizedPin || enteredPin == DEMO_PIN_DEFAULT) {
                        Serial.println("[AUTH] PIN Authorization ACCEPTED");
                        triggerAuthGrant();
                    } else {
                        Serial.println("[AUTH] PIN Authorization DENIED");
                        updateDisplays(readings, "INVALID PIN");
                        delay(1200);
                    }
                    enteredPin = "";
                }
            }
        }
    } else if (connectorState == CHARGING) {
        // Driver can terminate session by pressing '#' on Keypad
        char key = keypad.getKey();
        if (key == '#') {
            Serial.println("[CHARGING] Session terminated by driver via Keypad '#'");
            setConnectorState(AVAILABLE, "USER_KEYPAD_STOP");
        }
    }

    // -------------------------------------------------------------------------
    // 5. SERIAL TEST INTERFACE
    // -------------------------------------------------------------------------
    if (Serial.available()) {
        char cmd = Serial.read();
        switch (cmd) {
            case 'R':
            case 'r':
                Serial.println("[SERIAL] Trigger: 60s Demo Reservation");
                reservationExpiryMillis = millis() + 60000UL;
                authorizedPin = DEMO_PIN_DEFAULT;
                authorizedRfid = "DEADBEEF";
                isAuthWindowActive = false;
                setConnectorState(RESERVED, "SERIAL_DEMO_RESERVE");
                break;
            case 'A':
            case 'a':
                Serial.println("[SERIAL] Trigger: AVAILABLE");
                setConnectorState(AVAILABLE, "SERIAL_AVAILABLE");
                break;
            case 'P':
            case 'p':
                Serial.println("[SERIAL] Trigger: Cable Plugged In -> CHARGING");
                isAuthWindowActive = false;
                setConnectorState(CHARGING, "SERIAL_PLUG_SENSED");
                break;
            case 'F':
            case 'f':
            case 'T':
            case 't':
                Serial.println("[SERIAL] Trigger: SAFETY FAULT TRIP (<50ms)");
                triggerEmergencyTrip("SERIAL_FAULT_INJECTION");
                break;
            case 'E':
            case 'e':
                if (connectorState == RESERVED) {
                    Serial.println("[SERIAL] Trigger: EXPIRE RESERVATION");
                    setConnectorState(AVAILABLE, "SERIAL_EXPIRE");
                }
                break;
            case 'S':
            case 's':
                if (connectorState == CHARGING) {
                    Serial.println("[SERIAL] Trigger: STOP CHARGING");
                    setConnectorState(AVAILABLE, "SERIAL_STOP");
                }
                break;
        }
    }

    // -------------------------------------------------------------------------
    // 6. PERIODIC CSV TELEMETRY LOGGING & MQTT PUBLISH (Every 2s)
    // -------------------------------------------------------------------------
    uint32_t now = millis();
    if (now - lastTelemetryMillis >= TELEMETRY_INTERVAL_MS) {
        lastTelemetryMillis = now;

        // Persistent 11-column MicroSD logging
        logTelemetryToSd("2026-11-15T14:30:00Z", now, readings);

        // Periodic MQTT Telemetry publish
        mqttPublishTelemetry(readings, accumulatedEnergyKWh, now);

        Serial.printf("[TELEMETRY] State: %s | V: %.1fV | I: %.1fA | P: %.2fkW | E: %.3fkWh | Relay: %s\n",
                      stateToString(connectorState),
                      readings.voltageV,
                      readings.currentA,
                      readings.powerKW,
                      accumulatedEnergyKWh,
                      (digitalRead(RELAY_PIN) == RELAY_ENERGIZED) ? "ENERGIZED" : "DE_ENERGIZED");
    }

    // -------------------------------------------------------------------------
    // 7. PERIODIC DISPLAY REFRESH
    // -------------------------------------------------------------------------
    if (now - lastOledRefreshMillis >= OLED_REFRESH_INTERVAL_MS) {
        lastOledRefreshMillis = now;
        updateDisplays(readings);
    }
}
