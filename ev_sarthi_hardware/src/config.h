#ifndef EV_SARTHI_CONFIG_H
#define EV_SARTHI_CONFIG_H

#include <Arduino.h>

// ==============================================================================
// EV SARTHI HARDWARE CONFIGURATION & PIN DEFINITIONS
// ESP32 DevKit C v4 Architecture (Wokwi Simulation & Production Edge Board)
// ==============================================================================

// ------------------------------------------------------------------------------
// Station & Connector Identity
// ------------------------------------------------------------------------------
#define STATION_ID_DEFAULT    "ST001"
#define CONNECTOR_ID_DEFAULT  "C001"

// ------------------------------------------------------------------------------
// Analog ADC Sensors (ADC1 only - WiFi safe, zero RF interference)
// ------------------------------------------------------------------------------
#define VOLTAGE_PIN           35  // ADC1_CH7 - Potentiometer 1 (0 - 250V AC/DC)
#define CURRENT_PIN           34  // ADC1_CH6 - Potentiometer 2 (0 - 32A AC/DC)
#define ADC_MAX_RAW           4095.0f
#define VOLTAGE_MAX_V         250.0f
#define CURRENT_MAX_A         32.0f

// ------------------------------------------------------------------------------
// Shared SPI Bus (SCK, MISO, MOSI) & Peripheral Chip Selects
// ------------------------------------------------------------------------------
#define SPI_SCK_PIN           18
#define SPI_MISO_PIN          19
#define SPI_MOSI_PIN          23

#define SD_CS_PIN             5   // MicroSD Card SPI Chip Select
#define TFT_CS_PIN            14  // ILI9341 TFT Display SPI Chip Select
#define TFT_DC_PIN            2   // ILI9341 Data / Command Control
#define TFT_RST_PIN           4   // ILI9341 Hardware Reset
#define RFID_CS_PIN           27  // MFRC522 RFID Reader SPI NSS / Chip Select
#define RFID_RST_PIN          26  // MFRC522 Hardware Reset

// ------------------------------------------------------------------------------
// I2C Bus - SSD1306 Diagnostic OLED
// ------------------------------------------------------------------------------
#define OLED_SDA_PIN          21
#define OLED_SCL_PIN          22
#define OLED_I2C_ADDR         0x3C
#define OLED_SCREEN_WIDTH     128
#define OLED_SCREEN_HEIGHT    64

// ------------------------------------------------------------------------------
// 3x4 Matrix Keypad (Rows and Columns)
// ------------------------------------------------------------------------------
#define KEYPAD_ROWS           4
#define KEYPAD_COLS           3
const byte KEYPAD_ROW_PINS[KEYPAD_ROWS] = {13, 12, 16, 17};
const byte KEYPAD_COL_PINS[KEYPAD_COLS] = {25, 32, 33};

// ------------------------------------------------------------------------------
// Contactor Actuation Relay & Status LEDs
// Fail-Safe: LOW = NC = Red LED (Contactor OPEN / De-energized)
//            HIGH = NO = Green LED (Contactor CLOSED / Energized)
// ------------------------------------------------------------------------------
#define RELAY_PIN             15
#define RELAY_DE_ENERGIZED    LOW
#define RELAY_ENERGIZED       HIGH

// ------------------------------------------------------------------------------
// Safety & Fast Trip Thresholds (<50ms hardware interlock)
// Overvoltage: > 227.0V -> (227.0 / 250.0) * 4095 = 3718.26 -> 3719 raw
// Overcurrent: > 29.7A  -> (29.7  /  32.0) * 4095 = 3800.72 -> 3801 raw
// Vehicle Plug-in current: >= 0.8A -> (0.8 / 32.0) * 4095 = 102.375 -> >100 raw
// ------------------------------------------------------------------------------
#define OVERVOLTAGE_MAX_V     227.0f
#define OVERCURRENT_MAX_A     29.7f
#define TRIP_RAW_VOLTAGE      3719
#define TRIP_RAW_CURRENT      3801
#define PLUG_DETECT_RAW_CURR  100

// ------------------------------------------------------------------------------
// Timers & State Machine Lifecycles
// ------------------------------------------------------------------------------
#define DEFAULT_RESERVATION_TIME_MS   60000UL // 60-second reservation countdown
#define AUTH_GRANT_WINDOW_MS          30000UL // 30-second vehicle plug-in window
#define TELEMETRY_INTERVAL_MS         2000UL  // 2-second CSV & MQTT telemetry period
#define OLED_REFRESH_INTERVAL_MS      500UL   // 500ms OLED refresh
#define MQTT_RECONNECT_INTERVAL_MS    5000UL  // 5-second non-blocking MQTT retry

// ------------------------------------------------------------------------------
// Default Local Credentials (Fallback when offline)
// ------------------------------------------------------------------------------
#define DEMO_PIN_DEFAULT      "2580"
const byte AUTHORIZED_RFID_DEFAULT[4] = {0xDE, 0xAD, 0xBE, 0xEF};

// ------------------------------------------------------------------------------
// Networking & MQTT Configuration
// ------------------------------------------------------------------------------
#define WIFI_SSID_DEFAULT     "Wokwi-GUEST"
#define WIFI_PASS_DEFAULT     ""
#define MQTT_BROKER_DEFAULT   "broker.hivemq.com" // Public cloud broker for Wokwi + Cloud backend
#define MQTT_PORT_DEFAULT     1883
#define MQTT_BUFFER_SIZE_DEF  512

// Telemetry File Path
#define TELEMETRY_CSV_PATH    "/telemetry.csv"

#endif // EV_SARTHI_CONFIG_H
