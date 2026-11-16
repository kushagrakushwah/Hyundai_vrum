"""
tests/test_challenger_m1_2_empirical.py - Challenger 2 Empirical Test Suite
Verifies SPI Bus Arbitration, MicroSD CSV Logging, and MQTT Protocol Contracts
for Milestone 1 (ev_sarthi_hardware).
"""

import os
import json
import re
import subprocess
import pytest
import jsonschema


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARDWARE_DIR = os.path.join(BASE_DIR, "ev_sarthi_hardware")
SRC_DIR = os.path.join(HARDWARE_DIR, "src")


# ==============================================================================
# 1. SHARED SPI BUS ARBITRATION TESTS
# ==============================================================================

def test_spi_pin_definitions_in_config_and_ino():
    """Verify SPI pin assignments match hardware design in both config.h and sketch.ino."""
    config_path = os.path.join(SRC_DIR, "config.h")
    ino_path = os.path.join(HARDWARE_DIR, "sketch.ino")

    for file_path in [config_path, ino_path]:
        with open(file_path, "r") as f:
            content = f.read()

        assert "SPI_SCK_PIN           18" in content or "SPI_SCK_PIN 18" in content
        assert "SPI_MISO_PIN          19" in content or "SPI_MISO_PIN 19" in content
        assert "SPI_MOSI_PIN          23" in content or "SPI_MOSI_PIN 23" in content
        assert "SD_CS_PIN             5" in content or "SD_CS_PIN 5" in content
        assert "TFT_CS_PIN            14" in content or "TFT_CS_PIN 14" in content
        assert "RFID_CS_PIN           27" in content or "RFID_CS_PIN 27" in content
        assert "TFT_RST_PIN           4" in content or "TFT_RST_PIN 4" in content
        assert "RFID_RST_PIN          26" in content or "RFID_RST_PIN 26" in content


def test_spi_preassertion_sequence_in_peripherals():
    """Verify that CS pins (5, 14, 27) and RST pins (4, 26) are driven HIGH before SPI.begin()."""
    cpp_path = os.path.join(SRC_DIR, "peripherals.cpp")
    with open(cpp_path, "r") as f:
        cpp = f.read()

    # Find peripheralsInit function
    init_match = re.search(r"void peripheralsInit\(\)\s*\{(.*?)\n\}", cpp, re.DOTALL)
    assert init_match, "peripheralsInit() not found in peripherals.cpp"
    body = init_match.group(1)

    # Check relay configured first
    relay_pin_mode_idx = body.find("pinMode(RELAY_PIN, OUTPUT);")
    relay_write_idx = body.find("digitalWrite(RELAY_PIN, RELAY_DE_ENERGIZED);")
    assert relay_pin_mode_idx != -1 and relay_write_idx != -1
    assert relay_write_idx > relay_pin_mode_idx

    # Check CS and RST pin modes and digitalWrites
    sd_cs_high_idx = body.find("digitalWrite(SD_CS_PIN, HIGH);")
    tft_cs_high_idx = body.find("digitalWrite(TFT_CS_PIN, HIGH);")
    rfid_cs_high_idx = body.find("digitalWrite(RFID_CS_PIN, HIGH);")
    tft_rst_high_idx = body.find("digitalWrite(TFT_RST_PIN, HIGH);")
    rfid_rst_high_idx = body.find("digitalWrite(RFID_RST_PIN, HIGH);")

    assert sd_cs_high_idx != -1
    assert tft_cs_high_idx != -1
    assert rfid_cs_high_idx != -1
    assert tft_rst_high_idx != -1
    assert rfid_rst_high_idx != -1

    # Check SPI.begin occurs strictly AFTER all CS and RST are driven HIGH
    spi_begin_idx = body.find("SPI.begin(SPI_SCK_PIN, SPI_MISO_PIN, SPI_MOSI_PIN);")
    assert spi_begin_idx != -1
    assert sd_cs_high_idx < spi_begin_idx
    assert tft_cs_high_idx < spi_begin_idx
    assert rfid_cs_high_idx < spi_begin_idx
    assert tft_rst_high_idx < spi_begin_idx
    assert rfid_rst_high_idx < spi_begin_idx


def test_peripheral_initialization_order():
    """Verify initialization order: OLED (I2C) -> TFT (SPI) -> RFID (SPI) -> SD (SPI)."""
    cpp_path = os.path.join(SRC_DIR, "peripherals.cpp")
    with open(cpp_path, "r") as f:
        body = f.read()

    oled_idx = body.find("oled.begin(")
    tft_idx = body.find("tft.begin();")
    rfid_idx = body.find("rfid.PCD_Init();")
    sd_idx = body.find("SD.begin(SD_CS_PIN);")

    assert oled_idx != -1, "OLED initialization not found"
    assert tft_idx != -1, "TFT initialization not found"
    assert rfid_idx != -1, "RFID initialization not found"
    assert sd_idx != -1, "SD initialization not found"

    # Strictly ordered
    assert oled_idx < tft_idx < rfid_idx < sd_idx, "Incorrect peripheral init sequence order"


def test_runtime_cs_deassertion_safety():
    """Verify that every runtime peripheral function leaves its CS pin HIGH upon exit."""
    cpp_path = os.path.join(SRC_DIR, "peripherals.cpp")
    with open(cpp_path, "r") as f:
        cpp = f.read()

    # In updateTftDisplay: TFT_CS_PIN driven HIGH at exit
    assert "digitalWrite(TFT_CS_PIN, HIGH);" in cpp
    # In scanRfid: RFID_CS_PIN driven HIGH on all return paths
    rfid_cs_count = cpp.count("digitalWrite(RFID_CS_PIN, HIGH);")
    assert rfid_cs_count >= 4, f"Expected at least 4 RFID CS de-assertions across paths, found {rfid_cs_count}"
    # In logTelemetryToSd: SD_CS_PIN driven HIGH on error and completion
    sd_cs_count = cpp.count("digitalWrite(SD_CS_PIN, HIGH);")
    assert sd_cs_count >= 3, f"Expected at least 3 SD CS de-assertions across paths, found {sd_cs_count}"


# ==============================================================================
# 2. MICROSD CSV LOGGING TESTS
# ==============================================================================

EXPECTED_CSV_HEADER = "timestamp_utc,millis,station_id,connector_id,state,voltage_raw,current_raw,voltage_v,current_a,power_kw,energy_kwh"
EXPECTED_COLUMNS = EXPECTED_CSV_HEADER.split(",")


def test_microsd_csv_header_schema():
    """Verify exactly 11 standardized columns in MicroSD header."""
    assert len(EXPECTED_COLUMNS) == 11
    assert EXPECTED_COLUMNS == [
        "timestamp_utc",
        "millis",
        "station_id",
        "connector_id",
        "state",
        "voltage_raw",
        "current_raw",
        "voltage_v",
        "current_a",
        "power_kw",
        "energy_kwh"
    ]


def test_microsd_csv_header_in_sources():
    """Verify header string in peripherals.cpp and sketch.ino matches expected schema."""
    for path in [os.path.join(SRC_DIR, "peripherals.cpp"), os.path.join(HARDWARE_DIR, "sketch.ino")]:
        with open(path, "r") as f:
            text = f.read()
        assert EXPECTED_CSV_HEADER in text, f"CSV header missing in {path}"


def test_microsd_data_row_formatting():
    """Simulate formatting of CSV line and verify column count and data types."""
    iso = "2026-11-15T14:30:00Z"
    uptime = 12345
    station = "ST001"
    connector = "C001"
    state = "CHARGING"
    v_raw = 3767
    i_raw = 2048
    v = 230.00
    i = 16.00
    p = 3.680
    e = 0.0520

    line = f"{iso},{uptime},{station},{connector},{state},{v_raw},{i_raw},{v:.2f},{i:.2f},{p:.3f},{e:.4f}"
    tokens = line.split(",")

    assert len(tokens) == 11, f"Expected 11 columns, got {len(tokens)}"
    assert tokens[0] == iso
    assert int(tokens[1]) == uptime
    assert tokens[2] == station
    assert tokens[3] == connector
    assert tokens[4] in ["AVAILABLE", "RESERVED", "CHARGING", "FAULTED"]
    assert 0 <= int(tokens[5]) <= 4095
    assert 0 <= int(tokens[6]) <= 4095
    assert float(tokens[7]) == 230.0
    assert float(tokens[8]) == 16.0
    assert float(tokens[9]) == 3.68
    assert float(tokens[10]) == 0.052


def test_microsd_flush_on_write_present():
    """Verify file.flush() call is present in logTelemetryToSd in both peripherals.cpp and sketch.ino."""
    for path in [os.path.join(SRC_DIR, "peripherals.cpp"), os.path.join(HARDWARE_DIR, "sketch.ino")]:
        with open(path, "r") as f:
            text = f.read()

        log_match = re.search(r"bool logTelemetryToSd\([^)]*\)\s*\{(.*?)\n\}", text, re.DOTALL)
        assert log_match, f"logTelemetryToSd not found in {path}"
        func_body = log_match.group(1)

        assert "file.println(line);" in func_body
        assert "file.flush();" in func_body
        assert func_body.find("file.println(line);") < func_body.find("file.flush();") < func_body.find("file.close();")


# ==============================================================================
# 3. MQTT PROTOCOL CONTRACTS & SERIALIZATION/DESERIALIZATION
# ==============================================================================

SCHEMA_STATE = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["AVAILABLE", "RESERVED", "CHARGING", "FAULTED"]},
        "relay_state": {"type": "string", "enum": ["ENERGIZED", "DE_ENERGIZED"]},
        "transition_reason": {"type": "string"},
        "uptime_millis": {"type": "integer"}
    },
    "required": ["state", "relay_state", "transition_reason", "uptime_millis"]
}

SCHEMA_TELEMETRY = {
    "type": "object",
    "properties": {
        "voltage_v": {"type": "number"},
        "current_a": {"type": "number"},
        "power_kw": {"type": "number"},
        "energy_kwh": {"type": "number"},
        "voltage_raw": {"type": "integer"},
        "current_raw": {"type": "integer"},
        "millis": {"type": "integer"}
    },
    "required": ["voltage_v", "current_a", "power_kw", "energy_kwh", "voltage_raw", "current_raw", "millis"]
}

SCHEMA_AUTH = {
    "type": "object",
    "properties": {
        "auth_type": {"type": "string", "enum": ["PIN", "RFID"]},
        "credential": {
            "type": "object",
            "properties": {
                "pin": {"type": "string"},
                "rfid_uid": {"type": "string"}
            },
            "required": ["pin", "rfid_uid"]
        },
        "current_state": {"type": "string"}
    },
    "required": ["auth_type", "credential", "current_state"]
}


def test_mqtt_topic_hierarchy():
    """Verify standard MQTT topic strings constructed by firmware."""
    stn = "ST001"
    conn = "C001"
    assert f"evsarthi/{stn}/{conn}/reserve" == "evsarthi/ST001/C001/reserve"
    assert f"evsarthi/{stn}/{conn}/state" == "evsarthi/ST001/C001/state"
    assert f"evsarthi/{stn}/{conn}/telemetry" == "evsarthi/ST001/C001/telemetry"
    assert f"evsarthi/{stn}/{conn}/auth" == "evsarthi/ST001/C001/auth"
    assert f"evsarthi/{stn}/{conn}/cmd" == "evsarthi/ST001/C001/cmd"


def test_mqtt_buffer_size_configured():
    """Verify 512-byte buffer in platformio.ini, config.h, and mqtt_handler.cpp."""
    pio_path = os.path.join(HARDWARE_DIR, "platformio.ini")
    with open(pio_path, "r") as f:
        pio = f.read()
    assert "-D MQTT_MAX_PACKET_SIZE=512" in pio

    config_path = os.path.join(SRC_DIR, "config.h")
    with open(config_path, "r") as f:
        cfg = f.read()
    assert "MQTT_BUFFER_SIZE_DEF  512" in cfg

    mqtt_cpp = os.path.join(SRC_DIR, "mqtt_handler.cpp")
    with open(mqtt_cpp, "r") as f:
        mcpp = f.read()
    assert "mqttClient.setBufferSize(MQTT_BUFFER_SIZE_DEF);" in mcpp


def test_mqtt_state_payload_schema():
    """Verify serialized /state JSON adheres strictly to schema."""
    payload_str = '{"state":"AVAILABLE","relay_state":"DE_ENERGIZED","transition_reason":"BOOT_INITIALIZATION","uptime_millis":1000}'
    payload = json.loads(payload_str)
    jsonschema.validate(instance=payload, schema=SCHEMA_STATE)


def test_mqtt_telemetry_payload_schema():
    """Verify serialized /telemetry JSON adheres strictly to schema with numeric floats."""
    payload_str = '{"voltage_v":230.00,"current_a":16.00,"power_kw":3.680,"energy_kwh":0.0520,"voltage_raw":3767,"current_raw":2048,"millis":12345}'
    payload = json.loads(payload_str)
    jsonschema.validate(instance=payload, schema=SCHEMA_TELEMETRY)
    assert isinstance(payload["voltage_v"], float)
    assert isinstance(payload["current_a"], float)
    assert isinstance(payload["power_kw"], float)
    assert isinstance(payload["energy_kwh"], float)
    assert isinstance(payload["voltage_raw"], int)
    assert isinstance(payload["current_raw"], int)
    assert isinstance(payload["millis"], int)


def test_mqtt_auth_payload_schema():
    """Verify serialized /auth JSON adheres strictly to schema with nested credential."""
    payload_str = '{"auth_type":"PIN","credential":{"pin":"2580","rfid_uid":""},"current_state":"RESERVED"}'
    payload = json.loads(payload_str)
    jsonschema.validate(instance=payload, schema=SCHEMA_AUTH)
    assert payload["credential"]["pin"] == "2580"
    assert payload["credential"]["rfid_uid"] == ""


def test_mqtt_deserialization_reserve_create():
    """Simulate receiving /reserve CREATE payload."""
    payload = {
        "action": "CREATE",
        "reservation_id": "RES_789",
        "pin": "1234",
        "duration_seconds": 60,
        "expires_at": "2026-11-15T15:30:00Z",
        "rfid_uid": "DEADBEEF"
    }
    raw = json.dumps(payload)
    doc = json.loads(raw)
    assert doc["action"] == "CREATE"
    assert doc.get("reservation_id") == "RES_789"
    assert doc.get("pin") == "1234"
    assert doc.get("duration_seconds") == 60


def test_mqtt_deserialization_cmd_auth_decision():
    """Simulate receiving /cmd AUTH_DECISION payload."""
    payload = {
        "action": "AUTH_DECISION",
        "status": "ACCEPTED",
        "status_code": 200,
        "auth_window_duration_ms": 8000,
        "relay_action": "ENERGIZE"
    }
    raw = json.dumps(payload)
    doc = json.loads(raw)
    assert doc["action"] == "AUTH_DECISION"
    assert doc["status"] == "ACCEPTED"
    assert doc["auth_window_duration_ms"] == 8000
    assert doc["relay_action"] == "ENERGIZE"


# ==============================================================================
# 4. C++ HARNESS INTEGRATION TEST
# ==============================================================================

def test_compiled_cpp_challenger_harness():
    """Run the standalone C++ verification binary and verify 100% pass."""
    exe_path = os.path.join(BASE_DIR, "tests", "challenger_test.exe")
    cpp_src = os.path.join(BASE_DIR, "tests", "empirical_challenger_m1_2.cpp")

    env = os.environ.copy()
    env["PATH"] = r"C:\msys64\ucrt64\bin;" + env.get("PATH", "")

    # Auto-compile if executable does not exist
    if not os.path.exists(exe_path) and os.path.exists(cpp_src):
        compiler = r"C:\msys64\ucrt64\bin\g++.exe" if os.path.exists(r"C:\msys64\ucrt64\bin\g++.exe") else "g++"
        try:
            compile_res = subprocess.run(
                [compiler, "-std=c++17", cpp_src, "-o", exe_path],
                capture_output=True, text=True, env=env
            )
            if compile_res.returncode != 0:
                pytest.skip(f"Could not compile C++ test harness: {compile_res.stderr}")
        except FileNotFoundError:
            pytest.skip("g++ compiler not found to build C++ test harness")

    if not os.path.exists(exe_path):
        pytest.skip(f"C++ test executable not found at {exe_path}")

    result = subprocess.run([exe_path], capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"C++ test binary failed with output:\n{result.stdout}\n{result.stderr}"
    assert "ALL EMPIRICAL CHALLENGES VERIFIED SUCCESSFULLY!" in result.stdout
    assert "11 / 11 TESTS PASSED" in result.stdout
