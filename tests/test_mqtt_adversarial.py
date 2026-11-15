"""
tests/test_mqtt_adversarial.py - Empirical Adversarial Stress Test Suite
Challenger M2.1: Adversarial challenge for Backend & MQTT Integration.

Stress-tests:
1. Topic wildcard parsing and routing integrity (single-level +, multi-level #, malformed topics, edge-case characters).
2. Handling of corrupt, truncated, non-UTF8, non-dict JSON payloads, malformed typed fields, and SQL injection strings.
3. Outbound message contracts for publish_reserve and publish_cmd (topics, payload schemas, QoS=1, retain=False).
4. Rapid bursts of telemetry updates and concurrent database throughput under high load.
5. Offline broker fallback behavior and graceful reconnection transitions.
6. Multi-connector concurrency, state isolation, and fault containment.
"""
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
import pytest
import aiosqlite
from starlette.testclient import TestClient

# Path setup
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "smartreserve", "smartreserve_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from mqtt_service import MQTTService, mqtt_service, CallableBool
from websocket_manager import ws_manager
from main import app, ReserveRequest


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_test_db():
    """Ensure database is initialized and fresh for each test."""
    await database.init_db()
    yield


# ─── 1. TOPIC WILDCARD MATCHING & ROUTING INTEGRITY ───────────────────────────

class TestTopicWildcardsAndRouting:
    """Empirical verification of topic pattern matching and dispatch routing."""

    def test_wildcard_topic_matching_logic(self):
        """Verifies _topic_matches logic for +, #, and exact topics."""
        service = MQTTService()

        # Exact match
        assert service._topic_matches("evsarthi/ST_01/1/state", "evsarthi/ST_01/1/state") is True
        assert service._topic_matches("evsarthi/ST_01/1/state", "evsarthi/ST_01/1/telemetry") is False

        # Single level wildcard (+)
        assert service._topic_matches("evsarthi/+/+/state", "evsarthi/ST_01/1/state") is True
        assert service._topic_matches("evsarthi/+/+/state", "evsarthi/STATION_XYZ_99/conn_2/state") is True
        assert service._topic_matches("evsarthi/+/+/telemetry", "evsarthi/A/B/telemetry") is True
        assert service._topic_matches("evsarthi/+/+/auth", "evsarthi/A/B/auth") is True

        # Negative matching with single level (+)
        assert service._topic_matches("evsarthi/+/+/state", "evsarthi/ST_01/1/telemetry") is False
        assert service._topic_matches("evsarthi/+/+/state", "evsarthi/ST_01/1/state/extra") is False
        assert service._topic_matches("evsarthi/+/+/state", "evsarthi/ST_01/state") is False
        assert service._topic_matches("evsarthi/+/+/state", "other_prefix/ST_01/1/state") is False

        # Multi-level wildcard (#)
        assert service._topic_matches("evsarthi/#", "evsarthi/ST_01/1/state") is True
        assert service._topic_matches("evsarthi/#", "evsarthi/ST_01/1/telemetry/extra/deep") is True
        assert service._topic_matches("evsarthi/ST_01/#", "evsarthi/ST_01/1/auth") is True
        assert service._topic_matches("evsarthi/ST_01/#", "evsarthi/ST_02/1/auth") is False
        assert service._topic_matches("#", "anything/at/all") is True

    @pytest.mark.asyncio
    async def test_malformed_topic_routing_rejection(self):
        """Verifies that non-conforming topic formats are silently discarded without throwing exceptions."""
        service = MQTTService()
        recorded = []
        # Register listeners on valid inbound topic patterns
        service.add_message_listener("evsarthi/+/+/state", lambda t, p: recorded.append((t, p)))
        service.add_message_listener("evsarthi/+/+/telemetry", lambda t, p: recorded.append((t, p)))
        service.add_message_listener("evsarthi/+/+/auth", lambda t, p: recorded.append((t, p)))

        valid_payload = json.dumps({"state": "AVAILABLE"}).encode("utf-8")

        # Too few segments
        await service._async_handle_message("evsarthi", valid_payload)
        await service._async_handle_message("evsarthi/ST_01", valid_payload)
        await service._async_handle_message("evsarthi/ST_01/1", valid_payload)

        # Too many segments
        await service._async_handle_message("evsarthi/ST_01/1/state/extra", valid_payload)
        await service._async_handle_message("evsarthi/ST_01/1/state/extra/deep/segment", valid_payload)

        # Wrong prefix
        await service._async_handle_message("other_system/ST_01/1/state", valid_payload)
        await service._async_handle_message("/evsarthi/ST_01/1/state", valid_payload)

        # Empty topic
        await service._async_handle_message("", valid_payload)

        # Unknown subtopic (neither state, telemetry, nor auth)
        await service._async_handle_message("evsarthi/ST_01/1/unknown_subtopic", valid_payload)

        # Confirm none of these invoked listeners for state/telemetry/auth
        assert len(recorded) == 0

        # Confirm no status was created in DB for invalid station
        assert await database.get_connector_status("ST_01", "1") is None

    @pytest.mark.asyncio
    async def test_adversarial_topic_identifiers(self):
        """Verifies handling of special characters, UUIDs, and Unicode in station/connector IDs."""
        service = MQTTService()
        station_id = "ST_ADV-@#$_UNICODE_दिल्ली_01"
        connector_id = "CONN-UUID-999-αβγ"

        payload = {
            "state": "AVAILABLE",
            "relay_state": "DE_ENERGIZED",
            "transition_reason": "adversarial_test",
            "uptime_millis": 5000
        }
        topic = f"evsarthi/{station_id}/{connector_id}/state"
        await service._async_handle_message(topic, json.dumps(payload).encode("utf-8"))

        status = await database.get_connector_status(station_id, connector_id)
        assert status is not None
        assert status["state"] == "AVAILABLE"
        assert status["transition_reason"] == "adversarial_test"


# ─── 2. CORRUPT & MALFORMED PAYLOAD RESILIENCE ────────────────────────────────

class TestCorruptAndMalformedPayloads:
    """Stress-tests MQTT service against corrupt bytes, malformed JSON, and unexpected types."""

    @pytest.mark.asyncio
    async def test_corrupt_non_utf8_binary_payloads(self):
        """Ensures non-UTF8 binary data does not crash or raise uncaught errors."""
        service = MQTTService()
        corrupt_bytes_samples = [
            b"\xff\xfe\x00\x01\x80\xc0",
            b"\x80\x81\x82\x83",
            b"\x00\x00\x00\x00",
            b"\x1b[2J\x1b[H",  # ANSI escape codes
            b"\xef\xbb\xbf\xff\xff",  # Malformed BOM
        ]

        for corrupt_bytes in corrupt_bytes_samples:
            for subtopic in ["state", "telemetry", "auth"]:
                topic = f"evsarthi/ST_CORRUPT/1/{subtopic}"
                # Must return safely without raising
                await service._async_handle_message(topic, corrupt_bytes)

    @pytest.mark.asyncio
    async def test_truncated_and_empty_json_payloads(self):
        """Ensures truncated or empty JSON payloads are handled safely."""
        service = MQTTService()
        truncated_samples = [
            b"",
            b"   ",
            b"{",
            b'{"state": ',
            b'{"state": "AVAILABLE", "uptime_millis": ',
            b'{"voltage_v": 230.0, "current_a": ',
            b'{"auth_type": "PIN", "credential": {',
            b'{"auth_type": "RFID", "credential": {"rfid_uid": "DEAD',
        ]

        for sample in truncated_samples:
            for subtopic in ["state", "telemetry", "auth"]:
                topic = f"evsarthi/ST_TRUNCATED/1/{subtopic}"
                await service._async_handle_message(topic, sample)

    @pytest.mark.asyncio
    async def test_non_dictionary_json_primitives_safety(self):
        """
        Tests behavior when payload is valid JSON but NOT a JSON Object (e.g., list, int, string, null).
        Verifies that threadsafe future callback handles any raised AttributeError without killing the event loop.
        """
        loop = asyncio.get_running_loop()
        service = MQTTService()
        service._loop = loop

        class MockPahoMsg:
            def __init__(self, topic: str, payload: bytes):
                self.topic = topic
                self.payload = payload

        non_dict_samples = [
            b'[1, 2, 3, "state"]',
            b'"plain string payload"',
            b'12345',
            b'3.14159',
            b'true',
            b'false',
            b'null',
        ]

        for sample in non_dict_samples:
            for subtopic in ["state", "telemetry", "auth"]:
                topic = f"evsarthi/ST_NON_DICT/1/{subtopic}"
                msg = MockPahoMsg(topic, sample)
                # Dispatch through Paho _on_message bridge
                service._on_message(None, None, msg)

        # Allow event loop to process scheduled tasks
        await asyncio.sleep(0.05)
        # Verify event loop is still running healthy
        assert loop.is_running()

    @pytest.mark.asyncio
    async def test_telemetry_missing_and_extreme_field_types(self):
        """Verifies telemetry handler survives missing fields, nulls, and extreme float values."""
        station_id = "ST_TELEM_ROBUST"
        conn_id = "1"

        # Missing all optional fields, only bare dict
        minimal_payload = {}
        await mqtt_service._handle_telemetry(station_id, conn_id, minimal_payload)
        telem = await database.get_latest_telemetry(station_id, conn_id)
        assert telem is not None
        assert telem["voltage_v"] == 0.0
        assert telem["current_a"] == 0.0

        # Extreme positive and negative values
        extreme_payload = {
            "voltage_v": 999999.9,
            "current_a": -50.0,
            "power_kw": 0.0,
            "energy_kwh": 1e8,
            "voltage_raw": 4095,
            "current_raw": 0,
            "millis": 2147483647
        }
        await mqtt_service._handle_telemetry(station_id, conn_id, extreme_payload)
        telem2 = await database.get_latest_telemetry(station_id, conn_id)
        assert telem2 is not None
        assert abs(telem2["voltage_v"] - 999999.9) < 0.1
        assert abs(telem2["current_a"] - (-50.0)) < 0.1

    @pytest.mark.asyncio
    async def test_non_dict_json_direct_call_flaw(self):
        """Verifies that direct invocation of _async_handle_message with non-dict JSON is safely ignored without raising AttributeError."""
        service = MQTTService()
        try:
            await service._async_handle_message("evsarthi/ST_01/1/state", b'[1, 2, 3]')
        except AttributeError:
            pytest.fail("EMPIRICAL BUG CONFIRMED: Non-dict JSON caused unhandled AttributeError in _handle_state")

    @pytest.mark.asyncio
    async def test_telemetry_null_value_flaw(self):
        """Verifies that null telemetry values do not cause TypeError in float() coercion."""
        station_id = "ST_TELEM_NULL"
        conn_id = "1"
        payload = {"voltage_v": None, "current_a": None}
        try:
            await mqtt_service._handle_telemetry(station_id, conn_id, payload)
        except TypeError:
            pytest.fail("EMPIRICAL BUG CONFIRMED: null voltage_v caused TypeError in float(payload.get('voltage_v', 0.0))")

    @pytest.mark.asyncio
    async def test_auth_malformed_credentials_fallback(self, monkeypatch):
        """Tests that auth payloads with malformed or non-dict credentials fail safely (DENIED)."""
        station_id = "ST_AUTH_ADV"
        conn_id = "1"

        published_cmds = []

        async def mock_publish_cmd(station_id, connector_id, action="AUTH_DECISION",
                                   status="ACCEPTED", status_code=200,
                                   auth_window_duration_ms=8000, relay_action="ENERGIZE"):
            published_cmds.append({
                "station_id": station_id,
                "connector_id": connector_id,
                "action": action,
                "status": status,
                "status_code": status_code,
                "auth_window_duration_ms": auth_window_duration_ms,
                "relay_action": relay_action
            })
            return True

        monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

        # 1. Non-dict credential string: "1234" with PIN auth
        published_cmds.clear()
        payload_str_cred = {
            "auth_type": "PIN",
            "credential": "9999_NON_EXISTENT",
            "current_state": "AVAILABLE"
        }
        await mqtt_service._handle_auth(station_id, conn_id, payload_str_cred)
        assert len(published_cmds) == 1
        cmd = published_cmds[0]
        assert cmd["status"] == "DENIED"
        assert cmd["status_code"] == 401
        assert cmd["relay_action"] == "DE_ENERGIZE"

        # 2. None / null credential
        published_cmds.clear()
        payload_null_cred = {
            "auth_type": "RFID",
            "credential": None,
            "current_state": "AVAILABLE"
        }
        await mqtt_service._handle_auth(station_id, conn_id, payload_null_cred)
        assert len(published_cmds) == 1
        assert published_cmds[0]["status"] == "DENIED"
        assert published_cmds[0]["status_code"] == 401

        # 3. Numeric integer credential
        published_cmds.clear()
        payload_num_cred = {
            "auth_type": "PIN",
            "credential": 1234,
            "current_state": "AVAILABLE"
        }
        await mqtt_service._handle_auth(station_id, conn_id, payload_num_cred)
        assert len(published_cmds) == 1
        assert published_cmds[0]["status"] == "DENIED"
        assert published_cmds[0]["status_code"] == 401

    @pytest.mark.asyncio
    async def test_auth_tier2_expired_rfid_card_flaw(self, monkeypatch):
        """
        Adversarial Test: Present an RFID card that is active (is_active=1) but expired (expires_at < now).
        Verified: mqtt_service.py validates card['expires_at'] < time.time() and returns DENIED.
        """
        now = time.time()
        expired_card_uid = "EXPIRED_FLEET_CARD"
        await database.add_authorized_rfid(
            card_uid=expired_card_uid,
            user_id="expired_driver",
            user_name="Expired Fleet Driver",
            is_active=1,
            expires_at=now - 3600  # Expired 1 hour ago
        )

        station_id = "ST_EXPIRED_CARD_TEST"
        conn_id = "1"
        await database.delete_reservation(station_id, conn_id)

        published_cmds = []
        async def mock_publish_cmd(**kwargs):
            published_cmds.append(kwargs)
            return True
        monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

        payload = {
            "auth_type": "RFID",
            "credential": {"pin": "", "rfid_uid": expired_card_uid},
            "current_state": "AVAILABLE"
        }
        await mqtt_service._handle_auth(station_id, conn_id, payload)

        assert len(published_cmds) == 1
        cmd = published_cmds[0]
        assert cmd["status"] == "DENIED"
        assert cmd["status_code"] == 401
        assert cmd["relay_action"] == "DE_ENERGIZE"

    @pytest.mark.asyncio
    async def test_sql_injection_defense(self):
        """Verifies that SQL injection attempts in station_id, connector_id, PIN, or RFID are neutralized."""
        station_sql_inj = "ST_TEST'; DROP TABLE reservations; --"
        conn_sql_inj = "1'; DROP TABLE connector_telemetry; --"

        # State update injection
        await database.save_connector_status(station_sql_inj, conn_sql_inj, {
            "state": "AVAILABLE",
            "relay_state": "DE_ENERGIZED"
        })

        # Telemetry injection
        await database.save_connector_telemetry(station_sql_inj, conn_sql_inj, {
            "voltage_v": 230.0,
            "current_a": 16.0,
            "power_kw": 3.68,
            "energy_kwh": 1.0
        })

        # RFID check injection
        is_auth = await database.is_rfid_authorized("' OR '1'='1' --")
        assert is_auth is False

        # Confirm tables still exist and are functional
        async with aiosqlite.connect(database.DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM reservations") as cur:
                count = (await cur.fetchone())[0]
                assert count >= 0

            async with db.execute("SELECT COUNT(*) FROM connector_telemetry") as cur:
                count_telem = (await cur.fetchone())[0]
                assert count_telem > 0


# ─── 3. OUTBOUND MESSAGE STRUCTURE, FORMATTING & QOS ─────────────────────────

class TestOutboundMessageContracts:
    """Verifies publish_reserve and publish_cmd exact schemas, topics, and QoS."""

    @pytest.mark.asyncio
    async def test_publish_reserve_contract(self):
        """Validates evsarthi/{station}/{connector}/reserve message schema and flags."""
        service = MQTTService()
        captured = []

        async def mock_publish(topic: str, payload: Any, qos: int = 1, retain: bool = False):
            captured.append({
                "topic": topic,
                "payload": payload,
                "qos": qos,
                "retain": retain
            })
            return True

        service.publish = mock_publish

        now = int(time.time())
        res = await service.publish_reserve(
            station_id="ST_CONTRACT_01",
            connector_id="2",
            action="CREATE",
            reservation_id="RES_TEST_123",
            pin="4567",
            duration_seconds=60,
            expires_at=now + 60,
            rfid_uid="DEADBEEF"
        )
        assert res is True
        assert len(captured) == 1

        msg = captured[0]
        assert msg["topic"] == "evsarthi/ST_CONTRACT_01/2/reserve"
        assert msg["qos"] == 1
        assert msg["retain"] is False

        p = msg["payload"]
        assert p["action"] == "CREATE"
        assert p["reservation_id"] == "RES_TEST_123"
        assert p["pin"] == "4567"
        assert p["duration_seconds"] == 60
        assert p["expires_at"] == now + 60
        assert p["rfid_uid"] == "DEADBEEF"

        # Validate types
        assert isinstance(p["action"], str)
        assert isinstance(p["reservation_id"], str)
        assert isinstance(p["pin"], str)
        assert isinstance(p["duration_seconds"], int)
        assert isinstance(p["expires_at"], int)
        assert isinstance(p["rfid_uid"], str)

    @pytest.mark.asyncio
    async def test_publish_cmd_contract(self):
        """Validates evsarthi/{station}/{connector}/cmd message schema and flags."""
        service = MQTTService()
        captured = []

        async def mock_publish(topic: str, payload: Any, qos: int = 1, retain: bool = False):
            captured.append({
                "topic": topic,
                "payload": payload,
                "qos": qos,
                "retain": retain
            })
            return True

        service.publish = mock_publish

        # Test ACCEPTED
        await service.publish_cmd(
            station_id="ST_CMD_01",
            connector_id="1",
            action="AUTH_DECISION",
            status="ACCEPTED",
            status_code=200,
            auth_window_duration_ms=8000,
            relay_action="ENERGIZE"
        )
        assert len(captured) == 1
        cmd_acc = captured[0]
        assert cmd_acc["topic"] == "evsarthi/ST_CMD_01/1/cmd"
        assert cmd_acc["qos"] == 1
        assert cmd_acc["retain"] is False

        p_acc = cmd_acc["payload"]
        assert p_acc["action"] == "AUTH_DECISION"
        assert p_acc["status"] == "ACCEPTED"
        assert p_acc["status_code"] == 200
        assert p_acc["auth_window_duration_ms"] == 8000
        assert p_acc["relay_action"] == "ENERGIZE"

        # Test DENIED
        captured.clear()
        await service.publish_cmd(
            station_id="ST_CMD_01",
            connector_id="1",
            action="AUTH_DECISION",
            status="DENIED",
            status_code=401,
            auth_window_duration_ms=0,
            relay_action="DE_ENERGIZE"
        )
        assert len(captured) == 1
        p_den = captured[0]["payload"]
        assert p_den["status"] == "DENIED"
        assert p_den["status_code"] == 401
        assert p_den["auth_window_duration_ms"] == 0
        assert p_den["relay_action"] == "DE_ENERGIZE"

    @pytest.mark.asyncio
    async def test_publish_notifies_custom_listeners(self):
        """Verifies that publish() notifies registered _custom_listeners on matching outbound topics."""
        service = MQTTService()
        notified = []

        # Listen to all outbound command topics
        service.add_message_listener("evsarthi/+/+/cmd", lambda t, p: notified.append((t, p)))

        # Publish outbound command
        test_payload = {
            "action": "AUTH_DECISION",
            "status": "ACCEPTED",
            "status_code": 200,
            "auth_window_duration_ms": 8000,
            "relay_action": "ENERGIZE"
        }
        published = await service.publish("evsarthi/ST_NOTIFY_01/1/cmd", test_payload)
        assert published is True
        assert len(notified) == 1
        top, pay = notified[0]
        assert top == "evsarthi/ST_NOTIFY_01/1/cmd"
        assert pay["status"] == "ACCEPTED"
        assert pay["status_code"] == 200


# ─── 4. RAPID TELEMETRY BURSTS & CONCURRENT THROUGHPUT ────────────────────────

class TestRapidTelemetryBurstsAndThroughput:
    """Stress-tests database throughput and dynamic session updates under high burst rates."""

    @pytest.mark.asyncio
    async def test_100_concurrent_telemetry_burst(self):
        """
        Dispatches a rapid burst of 100 concurrent telemetry updates.
        Verifies zero SQLite lock errors and 100% record persistence.
        """
        station_id = "ST_BURST_TEST"
        conn_id = "1"
        burst_size = 100

        async def send_sample(seq: int):
            payload = {
                "voltage_v": 230.0 + (seq % 10) * 0.1,
                "current_a": 16.0 + (seq % 5) * 0.2,
                "power_kw": 3.68 + (seq % 5) * 0.05,
                "energy_kwh": round(0.01 * seq, 4),
                "voltage_raw": 2850 + seq,
                "current_raw": 1950 + seq,
                "millis": 100000 + seq * 1000,
                "recorded_at": time.time() + seq * 0.001
            }
            await mqtt_service._handle_telemetry(station_id, conn_id, payload)

        start_time = time.time()
        # Fire 100 coroutines concurrently
        await asyncio.gather(*[send_sample(i) for i in range(burst_size)])
        elapsed = time.time() - start_time

        # Verify throughput
        throughput = burst_size / (elapsed if elapsed > 0 else 0.001)
        print(f"\n[BURST PERFORMANCE] {burst_size} samples processed in {elapsed:.3f}s ({throughput:.1f} msg/sec)")

        # Verify all 100 entries exist in DB
        async with aiosqlite.connect(database.DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM connector_telemetry WHERE station_id = ? AND connector_id = ?",
                (station_id, conn_id)
            ) as cur:
                count = (await cur.fetchone())[0]

        assert count >= burst_size, f"Expected at least {burst_size} entries, found {count}"

    @pytest.mark.asyncio
    async def test_dynamic_soc_monotonic_under_rapid_telemetry(self):
        """Verifies active charging session SoC continuously increases under rapid energy updates."""
        station_id = "ST_SOC_TEST"
        conn_id = "1"
        now = time.time()

        # Seed an active charging session at 20.0% SoC
        await database.save_session({
            "station_id": station_id,
            "connector_id": conn_id,
            "session_id": f"SESS_SOC_{int(now)}",
            "user_id": "soc_driver",
            "start_soc": 20.0,
            "current_soc": 20.0,
            "start_time": now,
            "kw": 0.0,
            "kwh": 0.0
        })

        # Push 20 telemetry updates with increasing kWh
        previous_soc = 20.0
        for i in range(1, 21):
            energy_kwh = float(i) * 0.5  # 0.5, 1.0, 1.5, ... 10.0 kWh
            payload = {
                "voltage_v": 230.0,
                "current_a": 16.0,
                "power_kw": 3.68,
                "energy_kwh": energy_kwh,
                "millis": i * 1000
            }
            await mqtt_service._handle_telemetry(station_id, conn_id, payload)

            sess = await database.get_session(station_id, conn_id)
            assert sess is not None
            current_soc = sess["current_soc"]
            assert current_soc >= previous_soc, f"SoC decreased from {previous_soc} to {current_soc}"
            previous_soc = current_soc

        # Final check: with 10 kWh added to a 60 kWh pack, added SoC = (10/60)*100 = 16.67%
        # Final SoC should be ~36.67%
        final_sess = await database.get_session(station_id, conn_id)
        assert abs(final_sess["current_soc"] - 36.67) <= 0.1
        await database.delete_session(station_id, conn_id)


# ─── 5. OFFLINE FALLBACK BEHAVIOR ─────────────────────────────────────────────

class TestOfflineFallbackBehavior:
    """Verifies that backend operates smoothly and safely without a live MQTT broker."""

    def test_callable_bool_property_and_method(self):
        """Verifies CallableBool compatibility with boolean conditions and method calls."""
        c_true = CallableBool(1)
        assert bool(c_true) is True
        assert c_true() is True
        if not c_true:
            pytest.fail("CallableBool(1) should evaluate to True in boolean condition")

        c_false = CallableBool(0)
        assert bool(c_false) is False
        assert c_false() is False
        if c_false:
            pytest.fail("CallableBool(0) should evaluate to False in boolean condition")

    @pytest.mark.asyncio
    async def test_offline_publish_fallback(self):
        """Verifies publish methods return True (simulation mode) when broker is offline."""
        service = MQTTService()
        assert service.is_connected() is False

        # Publishing while offline
        ok_reserve = await service.publish_reserve("ST_OFFLINE", "1", "CREATE", "RES_OFFLINE", "1234")
        assert ok_reserve is True

        ok_cmd = await service.publish_cmd("ST_OFFLINE", "1", "AUTH_DECISION", "ACCEPTED")
        assert ok_cmd is True

        ok_pub = await service.publish("evsarthi/any/topic", {"any": "data"})
        assert ok_pub is True

    @pytest.mark.asyncio
    async def test_fastapi_rest_endpoints_when_broker_offline(self):
        """Verifies FastAPI REST endpoints (POST /api/reserve, cancel) succeed when broker is offline."""
        client = TestClient(app)
        station_id = "NTC_0003"

        # Pre-clean test station to prevent residual state collisions
        await database.delete_reservation(station_id, "1")
        await database.delete_session(station_id, "1")

        # Create reservation
        res = client.post("/api/reserve", json={
            "station_id": station_id,
            "connector_id": "1",
            "duration_minutes": 20,
            "user_id": "offline_driver_01",
            "pin": "9988"
        })
        assert res.status_code == 200, f"Reservation failed: {res.text}"
        data = res.json()
        assert data["success"] is True
        assert data["pin"] == "9988"

        # Cancel reservation
        cancel_res = client.post(f"/api/cancel-reservation/{station_id}", params={
            "connector_id": "1",
            "user_id": "offline_driver_01"
        })
        assert cancel_res.status_code == 200, f"Cancel failed: {cancel_res.text}"
        assert cancel_res.json()["success"] is True

        # Clean up
        await database.delete_reservation(station_id, "1")

    def test_paho_connect_disconnect_lifecycle(self):
        """Simulates Paho connection and disconnection callback events."""
        service = MQTTService()
        assert service._connected is False

        # Mock Paho Client
        class MockClient:
            def __init__(self):
                self.subscriptions = []
            def subscribe(self, subs):
                self.subscriptions.extend(subs)

        mock_paho = MockClient()

        # Connect event
        service._on_connect(mock_paho, None, None, 0)
        assert service._connected is True
        assert service.is_connected() is True
        # Verify subscriptions were registered
        expected_subs = [
            ("evsarthi/+/+/state", 1),
            ("evsarthi/+/+/telemetry", 0),
            ("evsarthi/+/+/auth", 1),
        ]
        assert mock_paho.subscriptions == expected_subs

        # Disconnect event
        service._on_disconnect(mock_paho, None, None, 1)
        assert service._connected is False
        assert service.is_connected() is False


# ─── 6. MULTI-CONNECTOR CONCURRENCY & FAULT ISOLATION ─────────────────────────

class TestMultiConnectorConcurrencyAndFaultIsolation:
    """Verifies that events on Connector 1 do not leak to or disrupt Connector 2."""

    @pytest.mark.asyncio
    async def test_concurrent_independent_reservations(self):
        """Verifies concurrent reservations on Connector 1 and 2 at same station operate independently."""
        station_id = "ST_CONCURRENT_01"
        now = time.time()

        await database.save_reservation({
            "station_id": station_id,
            "connector_id": "1",
            "reservation_id": "RES_C1",
            "user_id": "driver_alpha",
            "pin": "1111",
            "created_at": now,
            "expires_at": now + 600
        })
        await database.save_reservation({
            "station_id": station_id,
            "connector_id": "2",
            "reservation_id": "RES_C2",
            "user_id": "driver_beta",
            "pin": "2222",
            "created_at": now,
            "expires_at": now + 600
        })

        res1 = await database.get_reservation(station_id, "1")
        res2 = await database.get_reservation(station_id, "2")

        assert res1 is not None and res1["user_id"] == "driver_alpha"
        assert res2 is not None and res2["user_id"] == "driver_beta"

        # Hardware timeout on Connector 1 only
        timeout_payload = {
            "state": "AVAILABLE",
            "relay_state": "DE_ENERGIZED",
            "transition_reason": "reservation_expired"
        }
        await mqtt_service._handle_state(station_id, "1", timeout_payload)

        # Connector 1 must be cleared, Connector 2 must remain intact
        assert await database.get_reservation(station_id, "1") is None
        assert await database.get_reservation(station_id, "2") is not None
        assert (await database.get_reservation(station_id, "2"))["pin"] == "2222"

        # Clean up
        await database.delete_reservation(station_id, "2")

    @pytest.mark.asyncio
    async def test_fault_isolation_between_connectors(self):
        """Verifies hardware fault trip on Connector 1 terminates only Connector 1, leaving Connector 2 charging."""
        station_id = "ST_FAULT_ISO"
        now = time.time()

        # Create active sessions on both connectors
        await database.save_session({
            "station_id": station_id,
            "connector_id": "1",
            "session_id": "SESS_CONN_1",
            "user_id": "user_1",
            "start_time": now,
            "current_soc": 40.0
        })
        await database.save_session({
            "station_id": station_id,
            "connector_id": "2",
            "session_id": "SESS_CONN_2",
            "user_id": "user_2",
            "start_time": now,
            "current_soc": 55.0
        })

        # Inject FAULTED state on Connector 1 (overcurrent trip)
        fault_payload = {
            "state": "FAULTED",
            "relay_state": "DE_ENERGIZED",
            "transition_reason": "FAULT_OVERCURRENT: Current 31.5A exceeded 29.7A limit",
            "uptime_millis": 80000
        }
        await mqtt_service._handle_state(station_id, "1", fault_payload)

        # Connector 1 session must be terminated and recorded in history
        assert await database.get_session(station_id, "1") is None
        status_c1 = await database.get_connector_status(station_id, "1")
        assert status_c1["state"] == "FAULTED"

        # Connector 2 session must still be active and completely unaffected
        sess_c2 = await database.get_session(station_id, "2")
        assert sess_c2 is not None
        assert sess_c2["session_id"] == "SESS_CONN_2"
        assert sess_c2["user_id"] == "user_2"

        # Clean up
        await database.delete_session(station_id, "2")
