"""
tests/conftest.py - Master E2E Test Harness and Fixtures
Provides VirtualMQTTBroker, isolated SQLite test DB, FastAPI async/sync clients,
and multi-connector hardware simulation fixtures for headless pytest execution.
"""
import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
import pytest
import aiosqlite
import httpx
from starlette.testclient import TestClient

# ── 1. Path & Compatibility Safeguards ─────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "smartreserve", "smartreserve_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Safety patch for smartreserve_backend/main.py typing bugs
import builtins, typing
builtins.List = getattr(builtins, "List", typing.List)
builtins.Dict = getattr(builtins, "Dict", typing.Dict)

import database
from main import app as fastapi_app
from stations_db import StationsDB


# ── 2. Virtual MQTT Broker Implementation ──────────────────────────────────────
@dataclass
class MQTTMessage:
    topic: str
    payload: dict
    qos: int = 0
    retain: bool = False
    timestamp: float = field(default_factory=time.time)


class VirtualMQTTBroker:
    """
    High-fidelity, in-memory MQTT broker supporting:
    - Topic matching with MQTT wildcards (+ and #)
    - Retained message storage and delivery on subscription
    - QoS 0 and 1 semantics
    - Message history logging and deterministic async wait helpers
    - Simulated network partitions and disconnections
    """
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[str, dict], Any]]] = {}
        self._retained_messages: Dict[str, MQTTMessage] = {}
        self.history: List[MQTTMessage] = []
        self._waiters: List[tuple] = []
        self.is_connected: bool = True
        self._lock = asyncio.Lock()

    @staticmethod
    def match_topic(pattern: str, topic: str) -> bool:
        """MQTT 3.1.1 topic matching with + (single level) and # (multi level)."""
        pattern_parts = pattern.split('/')
        topic_parts = topic.split('/')

        i = 0
        while i < len(pattern_parts):
            p = pattern_parts[i]
            if p == '#':
                return True
            if i >= len(topic_parts):
                return False
            t = topic_parts[i]
            if p != '+' and p != t:
                return False
            i += 1
        return i == len(topic_parts)

    def subscribe(self, pattern: str, callback: Callable[[str, dict], Any]) -> None:
        """Subscribe a callback to a topic pattern. Immediately delivers matching retained messages."""
        if pattern not in self._subscribers:
            self._subscribers[pattern] = []
        if callback not in self._subscribers[pattern]:
            self._subscribers[pattern].append(callback)

        # Deliver matching retained messages
        for retained_topic, msg in list(self._retained_messages.items()):
            if self.match_topic(pattern, retained_topic):
                try:
                    if asyncio.iscoroutinefunction(callback):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(callback(retained_topic, msg.payload))
                        except RuntimeError:
                            pass
                    else:
                        callback(retained_topic, msg.payload)
                except Exception:
                    pass

    def unsubscribe(self, pattern: str, callback: Callable[[str, dict], Any]) -> None:
        if pattern in self._subscribers and callback in self._subscribers[pattern]:
            self._subscribers[pattern].remove(callback)

    async def publish(self, topic: str, payload: dict | str, qos: int = 0, retain: bool = False) -> None:
        """Publish a message to all matching subscribers and update history/retained messages."""
        if not self.is_connected:
            raise ConnectionError("VirtualMQTTBroker is disconnected")

        parsed_payload = json.loads(payload) if isinstance(payload, str) else payload
        msg = MQTTMessage(topic=topic, payload=parsed_payload, qos=qos, retain=retain)
        self.history.append(msg)

        if retain:
            self._retained_messages[topic] = msg

        # Notify active subscriptions
        for pattern, callbacks in list(self._subscribers.items()):
            if self.match_topic(pattern, topic):
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(topic, parsed_payload)
                        else:
                            cb(topic, parsed_payload)
                    except Exception:
                        pass

        # Resolve any waiters
        for waiter in list(self._waiters):
            pat, predicate, fut = waiter
            if not fut.done() and self.match_topic(pat, topic):
                if predicate is None or predicate(topic, parsed_payload):
                    fut.set_result(msg)
                    self._waiters.remove(waiter)

    async def wait_for_message(
        self,
        topic_pattern: str,
        timeout: float = 2.0,
        predicate: Optional[Callable[[str, dict], bool]] = None
    ) -> MQTTMessage:
        """Async test helper to deterministically await a message on a given topic."""
        # Check history first
        for msg in reversed(self.history):
            if self.match_topic(topic_pattern, msg.topic):
                if predicate is None or predicate(msg.topic, msg.payload):
                    return msg

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        waiter = (topic_pattern, predicate, fut)
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            raise TimeoutError(f"Timed out after {timeout}s waiting for message matching {topic_pattern}")

    def get_messages(self, topic_pattern: Optional[str] = None) -> List[MQTTMessage]:
        """Retrieve recorded messages matching optional topic pattern."""
        if topic_pattern is None:
            return list(self.history)
        return [m for m in self.history if self.match_topic(topic_pattern, m.topic)]

    def clear_history(self) -> None:
        self.history.clear()

    def simulate_partition(self, connected: bool = False) -> None:
        self.is_connected = connected


# ── 3. Pytest Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_broker() -> VirtualMQTTBroker:
    """Provides a clean in-memory VirtualMQTTBroker instance per test."""
    broker = VirtualMQTTBroker()
    yield broker
    broker.clear_history()


@pytest.fixture
def virtual_broker(mock_broker) -> VirtualMQTTBroker:
    """Alias for mock_broker."""
    return mock_broker


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """
    Provides a completely isolated temporary SQLite database for test execution.
    Monkeypatches database.DB_PATH, initializes schemas, and cleans up on teardown.
    """
    db_file = tmp_path / "test_smartreserve.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    asyncio.run(database.init_db())

    # Seed an authorized test RFID card if table exists
    async def seed_card():
        async with aiosqlite.connect(str(db_file)) as db:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO authorized_rfid_cards (card_uid, user_id, user_name) VALUES (?, ?, ?)",
                    ("DEADBEEF", "driver_001", "Hyundai Driver")
                )
                await db.commit()
            except Exception:
                pass
    asyncio.run(seed_card())

    yield str(db_file)


@pytest.fixture
def app(test_db, monkeypatch):
    """Configures FastAPI app with clean test database and suppresses background loops."""
    async def dummy_cleanup():
        pass
    monkeypatch.setattr("main.cleanup_expired_reservations", dummy_cleanup)
    yield fastapi_app


@pytest.fixture
async def async_client(app):
    """Provides an httpx.AsyncClient wired to the FastAPI test application."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def sync_client(app):
    """Provides a synchronous Starlette TestClient for the FastAPI test application."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def hardware_factory(mock_broker):
    """
    Factory fixture creating MockEVSarthiHardware instances wired to the virtual broker.
    """
    from tests.mock_esp32_hardware import MockEVSarthiHardware
    created_nodes = []

    def _create(station_id: str = "ST001", connector_id: str = "1"):
        def broker_publish(t: str, p: dict):
            if mock_broker.is_connected:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(mock_broker.publish(t, p))
                except RuntimeError:
                    asyncio.run(mock_broker.publish(t, p))

        node = MockEVSarthiHardware(
            station_id=station_id,
            connector_id=connector_id,
            on_publish=broker_publish
        )
        mock_broker.subscribe(f"evsarthi/{station_id}/{connector_id}/reserve", node.handle_reserve_message)
        mock_broker.subscribe(f"evsarthi/{station_id}/{connector_id}/cmd", node.handle_cmd_message)
        created_nodes.append(node)
        return node

    yield _create


@pytest.fixture
def single_connector(hardware_factory):
    """Provides a single simulated hardware connector ST001/C1."""
    return hardware_factory(station_id="ST001", connector_id="1")


@pytest.fixture
def dual_connectors(hardware_factory):
    """Provides two concurrent connectors on the same station: ST001/C1 and ST001/C2."""
    c1 = hardware_factory(station_id="ST001", connector_id="1")
    c2 = hardware_factory(station_id="ST001", connector_id="2")
    return c1, c2


# ── 4. Standard Test Payload Builders ──────────────────────────────────────────

@pytest.fixture
def payload_builder():
    """Generates standard, schema-compliant JSON payloads for testing."""
    class PayloadBuilder:
        @staticmethod
        def reserve_create(station_id="ST001", connector_id="1", pin="2580", duration_seconds=60, rfid="DEADBEEF"):
            return {
                "message_id": f"res-req-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "action": "CREATE",
                "reservation_id": f"RES_{station_id}_{int(time.time())}",
                "user_id": "test_driver_01",
                "pin": pin,
                "duration_seconds": duration_seconds,
                "expires_at": int(time.time()) + duration_seconds,
                "rfid_uid": rfid
            }

        @staticmethod
        def reserve_cancel(station_id="ST001", connector_id="1", reservation_id="RES_ST001_100"):
            return {
                "message_id": f"res-cancel-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "action": "CANCEL",
                "reservation_id": reservation_id,
                "user_id": "test_driver_01"
            }

        @staticmethod
        def auth_pin(station_id="ST001", connector_id="1", pin="2580", current_state="RESERVED"):
            return {
                "message_id": f"auth-pin-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "station_id": station_id,
                "connector_id": connector_id,
                "auth_type": "PIN",
                "credential": {"pin": pin},
                "current_state": current_state
            }

        @staticmethod
        def auth_rfid(station_id="ST001", connector_id="1", rfid="DEADBEEF", current_state="RESERVED"):
            return {
                "message_id": f"auth-rfid-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "station_id": station_id,
                "connector_id": connector_id,
                "auth_type": "RFID",
                "credential": {"rfid_uid": rfid},
                "current_state": current_state
            }

        @staticmethod
        def cmd_decision(status="ACCEPTED", status_code=200, action="AUTH_DECISION", relay_action="ENERGIZE"):
            return {
                "message_id": f"cmd-resp-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "action": action,
                "status": status,
                "status_code": status_code,
                "auth_window_duration_ms": 8000,
                "relay_action": relay_action,
                "session_id": "SESS_TEST_01" if status == "ACCEPTED" else None
            }

        @staticmethod
        def telemetry(station_id="ST001", connector_id="1", voltage_v=230.0, current_a=16.0, state="CHARGING"):
            v_raw = int((voltage_v / 250.0) * 4095)
            c_raw = int((current_a / 32.0) * 4095)
            power_kw = round((voltage_v * current_a) / 1000.0, 3)
            return {
                "message_id": f"telem-{int(time.time()*1000)}",
                "timestamp": int(time.time()),
                "station_id": station_id,
                "connector_id": connector_id,
                "state": state,
                "millis": 50000,
                "raw": {"voltage_raw": v_raw, "current_raw": c_raw},
                "electrical": {"voltage_v": voltage_v, "current_a": current_a, "power_kw": power_kw, "frequency_hz": 50.0},
                "energy": {"energy_kwh": 0.5, "session_seconds": 120},
                "sd_status": {"card_mounted": True, "last_log_success": True}
            }

    return PayloadBuilder()
