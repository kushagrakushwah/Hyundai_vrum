#include "peripherals.h"

// Peripheral Hardware Objects
static Adafruit_SSD1306 oled(OLED_SCREEN_WIDTH, OLED_SCREEN_HEIGHT, &Wire, -1);
static Adafruit_ILI9341 tft(TFT_CS_PIN, TFT_DC_PIN, TFT_RST_PIN);
static MFRC522 rfid(RFID_CS_PIN, RFID_RST_PIN);

// Keypad definition
static char keys[KEYPAD_ROWS][KEYPAD_COLS] = {
    {'1', '2', '3'},
    {'4', '5', '6'},
    {'7', '8', '9'},
    {'*', '0', '#'}
};
static byte rowPins[KEYPAD_ROWS] = {13, 12, 16, 17};
static byte colPins[KEYPAD_COLS] = {25, 32, 33};
static Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, KEYPAD_ROWS, KEYPAD_COLS);

static bool sdAvailable = false;
static ConnectorState lastDrawnTftState = (ConnectorState)-1;
static String lastDrawnPin = "";
static bool lastDrawnAuthWindow = false;

bool isSdAvailable() {
    return sdAvailable;
}

void setRelayRaw(bool energized) {
    digitalWrite(RELAY_PIN, energized ? RELAY_ENERGIZED : RELAY_DE_ENERGIZED);
}

bool getRelayRaw() {
    return (digitalRead(RELAY_PIN) == RELAY_ENERGIZED);
}

void peripheralsInit() {
    // 1. Configure Relay Fail-Safe FIRST (Open contactor, Red LED ON, Green LED OFF)
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);

    // 2. Pre-assert all SPI Chip Selects and Reset pins HIGH to prevent bus contention
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

    // 3. Configure ADC resolution (12-bit, 0 - 4095)
    analogReadResolution(12);

    // 4. Initialize Shared SPI Bus
    SPI.begin(SPI_SCK_PIN, SPI_MISO_PIN, SPI_MOSI_PIN);

    // 5. Initialize OLED via I2C (SDA=21, SCL=22)
    Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
    if (oled.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        oled.clearDisplay();
        oled.setTextSize(1);
        oled.setTextColor(SSD1306_WHITE);
        oled.setCursor(0, 0);
        oled.println("EV-SARTHI BOOT");
        oled.println("SPI Arbitration Init");
        oled.display();
        Serial.println("[PERIPHERALS] SSD1306 OLED initialized");
    } else {
        Serial.println("[PERIPHERALS] ERROR: SSD1306 OLED init failed");
    }

    // 6. Initialize ILI9341 TFT Display
    digitalWrite(RFID_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);
    tft.begin();
    tft.setRotation(1); // Landscape
    tft.fillScreen(ILI9341_BLACK);
    tft.setTextColor(ILI9341_WHITE);
    tft.setTextSize(2);
    tft.setCursor(20, 30);
    tft.println("EV-SARTHI HARDWARE");
    digitalWrite(TFT_CS_PIN, HIGH);
    Serial.println("[PERIPHERALS] ILI9341 TFT initialized");

    // 7. Initialize MFRC522 RFID
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);
    rfid.PCD_Init();
    digitalWrite(RFID_CS_PIN, HIGH);
    Serial.println("[PERIPHERALS] MFRC522 RFID initialized");

    // 8. Initialize MicroSD Card Storage
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(RFID_CS_PIN, HIGH);
    sdAvailable = SD.begin(SD_CS_PIN);
    if (sdAvailable) {
        Serial.println("[PERIPHERALS] MicroSD card initialized");
        // Verify or create 11-column CSV header
        File file = SD.open(TELEMETRY_CSV_PATH, FILE_READ);
        bool needsHeader = (!file || file.size() == 0);
        if (file) file.close();

        if (needsHeader) {
            File outFile = SD.open(TELEMETRY_CSV_PATH, FILE_WRITE);
            if (outFile) {
                outFile.println("timestamp_utc,millis,station_id,connector_id,state,voltage_raw,current_raw,voltage_v,current_a,power_kw,energy_kwh");
                outFile.flush();
                outFile.close();
                Serial.println("[PERIPHERALS] MicroSD CSV header written (11 columns)");
            }
        }
    } else {
        Serial.println("[PERIPHERALS] WARNING: MicroSD card not detected or init failed");
    }
    digitalWrite(SD_CS_PIN, HIGH);
}

SensorReadings readSensors() {
    SensorReadings readings;
    readings.voltageRaw = analogRead(VOLTAGE_PIN);
    readings.currentRaw = analogRead(CURRENT_PIN);

    readings.voltageV = ((float)readings.voltageRaw / ADC_MAX_RAW) * VOLTAGE_MAX_V;
    readings.currentA = ((float)readings.currentRaw / ADC_MAX_RAW) * CURRENT_MAX_A;
    readings.powerKW = (readings.voltageV * readings.currentA) / 1000.0f;
    return readings;
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

void updateOledDisplay(ConnectorState state, const SensorReadings& readings, float energyKWh) {
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(SSD1306_WHITE);
    oled.setCursor(0, 0);

    oled.println("=== EV-SARTHI EDGE ===");
    oled.printf("STN:%s  CONN:%s\n", STATION_ID_DEFAULT, CONNECTOR_ID_DEFAULT);
    oled.printf("STATE: %s\n", fsmStateToString(state));
    oled.printf("V:%5.1fV  I:%4.1fA\n", readings.voltageV, readings.currentA);
    oled.printf("P:%5.2fkW E:%6.3fkWh\n", readings.powerKW, energyKWh);
    oled.printf("RELAY: %s\n", (digitalRead(RELAY_PIN) == RELAY_ENERGIZED) ? "CLOSED [ON]" : "OPEN [SAFE]");

    oled.display();
}

void updateTftDisplay(ConnectorState state,
                      const String& enteredPin,
                      uint32_t reservationSecsRemaining,
                      uint32_t authWindowSecsRemaining,
                      bool isAuthWindowActive,
                      const SensorReadings& readings,
                      float energyKWh,
                      const char* alertMessage) {
    // Shared SPI safety: deselect RFID & SD
    digitalWrite(RFID_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);

    if (alertMessage != nullptr) {
        tft.fillScreen(ILI9341_RED);
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(2);
        tft.setCursor(30, 90);
        tft.println("SECURITY ALERT");
        tft.setCursor(30, 130);
        tft.println(alertMessage);
        digitalWrite(TFT_CS_PIN, HIGH);
        return;
    }

    bool fullRedraw = (state != lastDrawnTftState || isAuthWindowActive != lastDrawnAuthWindow);

    if (state == AVAILABLE) {
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
    } else if (state == RESERVED) {
        if (isAuthWindowActive) {
            if (fullRedraw) {
                tft.fillScreen(ILI9341_BLACK);
                tft.fillRect(0, 0, 320, 45, ILI9341_GREEN);
                tft.setTextColor(ILI9341_BLACK);
                tft.setTextSize(2);
                tft.setCursor(20, 12);
                tft.println("AUTH ACCEPTED");
            }

            tft.fillRect(20, 60, 280, 70, ILI9341_NAVY);
            tft.setTextColor(ILI9341_YELLOW);
            tft.setTextSize(2);
            tft.setCursor(30, 70);
            tft.println("INSERT CABLE NOW");

            tft.setTextSize(2);
            tft.setTextColor(ILI9341_WHITE);
            tft.setCursor(30, 100);
            tft.printf("Window: %2u s\n", (unsigned int)authWindowSecsRemaining);

            tft.setTextColor(ILI9341_GREEN);
            tft.setTextSize(1);
            tft.setCursor(25, 150);
            tft.println("Relay ENERGIZED (Green LED ON)");
            tft.setCursor(25, 170);
            tft.println("Plug in vehicle to initiate charging.");
        } else {
            if (fullRedraw || enteredPin != lastDrawnPin) {
                tft.fillScreen(ILI9341_BLACK);
                tft.fillRect(0, 0, 320, 45, ILI9341_BLUE);
                tft.setTextColor(ILI9341_WHITE);
                tft.setTextSize(2);
                tft.setCursor(20, 12);
                tft.println("CONNECTOR RESERVED");

                tft.setTextColor(ILI9341_YELLOW);
                tft.setTextSize(2);
                tft.setCursor(20, 60);
                tft.printf("Expires: %2u sec\n", (unsigned int)reservationSecsRemaining);

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
    } else if (state == CHARGING) {
        if (fullRedraw) {
            tft.fillScreen(ILI9341_BLACK);
            tft.fillRect(0, 0, 320, 45, ILI9341_DARKCYAN);
            tft.setTextColor(ILI9341_WHITE);
            tft.setTextSize(2);
            tft.setCursor(20, 12);
            tft.println("CHARGING IN PROGRESS");
        }

        // Live charging telemetry box
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
        tft.printf("Energy:  %5.3f kWh\n", energyKWh);

        tft.setTextColor(ILI9341_YELLOW);
        tft.setTextSize(1);
        tft.setCursor(20, 195);
        tft.println("Press '#' on Keypad to stop charging");
    } else if (state == FAULTED) {
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

    lastDrawnTftState = state;
    lastDrawnPin = enteredPin;
    lastDrawnAuthWindow = isAuthWindowActive;

    digitalWrite(TFT_CS_PIN, HIGH);
}

char scanKeypad() {
    return keypad.getKey();
}

bool scanRfid(String& outUid) {
    digitalWrite(TFT_CS_PIN, HIGH);
    digitalWrite(SD_CS_PIN, HIGH);

    if (!rfid.PICC_IsNewCardPresent()) {
        digitalWrite(RFID_CS_PIN, HIGH);
        return false;
    }

    if (!rfid.PICC_ReadCardSerial()) {
        digitalWrite(RFID_CS_PIN, HIGH);
        return false;
    }

    outUid = "";
    for (byte i = 0; i < rfid.uid.size; i++) {
        if (rfid.uid.uidByte[i] < 0x10) outUid += "0";
        outUid += String(rfid.uid.uidByte[i], HEX);
    }
    outUid.toUpperCase();

    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    digitalWrite(RFID_CS_PIN, HIGH);

    Serial.printf("[RFID] Scanned UID: %s\n", outUid.c_str());
    return true;
}

bool logTelemetryToSd(const char* isoTimestamp,
                      uint32_t uptimeMillis,
                      const char* stationId,
                      const char* connectorId,
                      ConnectorState state,
                      const SensorReadings& readings,
                      float energyKWh) {
    if (!sdAvailable) return false;

    // Deselect TFT and RFID on shared SPI bus
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
             (isoTimestamp != nullptr && strlen(isoTimestamp) > 0) ? isoTimestamp : "NTP_SYNC_PENDING",
             (unsigned long)uptimeMillis,
             stationId,
             connectorId,
             fsmStateToString(state),
             readings.voltageRaw,
             readings.currentRaw,
             readings.voltageV,
             readings.currentA,
             readings.powerKW,
             energyKWh);

    file.println(line);
    file.flush(); // Critical: commit buffer to flash immediately
    file.close();

    digitalWrite(SD_CS_PIN, HIGH);
    return true;
}
