"""
tests/test_challenger_m3_4_adversarial_deep.py
=============================================
Milestone 3 Challenger 4 Deep Adversarial & Fuzzing Suite for handle_remote_start_transaction.

Adversarial Objectives:
1. Invariant: Contactor relay GPIO 15 MUST NEVER energize under any invalid, malformed, or faulted conditions.
2. Full boundary matrix:
   - Non-dict payloads (None, int, list, str, bool)
   - Malformed/missing idTag (None, empty, whitespace, non-string, int, dict, list)
   - Malformed/mismatched connectorId (string non-numeric, negative, out of bounds, complex types)
   - State guards (CHARGING, FAULTED, is_tripped=True)
   - Reservation credential mismatch in RESERVED state
   - CSMS rejection or exception during StartTransaction
   - 50-thread concurrent bombardment (race condition stress test)
"""

import asyncio
import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "smartreserve", "smartreserve_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ocpp_bridge import (
    OCPPBridge,
    VirtualCSMS,
)


class MockMQTTCollector:
    """Mock MQTT service capturing all published commands and messages."""

    def __init__(self):
        self.published_commands = []
        self.published_messages = []

    async def publish_cmd(
        self,
        station_id: str,
        connector_id: str,
        action: str,
        status: str,
        status_code: int,
        auth_window_duration_ms: int = 0,
        relay_action: str = "DE_ENERGIZE",
    ):
        self.published_commands.append({
            "station_id": station_id,
            "connector_id": connector_id,
            "action": action,
            "status": status,
            "status_code": status_code,
            "auth_window_duration_ms": auth_window_duration_ms,
            "relay_action": relay_action,
        })

    async def publish(self, topic: str, payload: dict):
        self.published_messages.append({
            "topic": topic,
            "payload": payload,
        })
        if "cmd" in topic and isinstance(payload, dict):
            self.published_commands.append(payload)

    @property
    def energized_count(self) -> int:
        return sum(
            1 for c in self.published_commands
            if c.get("relay_action") == "ENERGIZE"
        )


@pytest.fixture
async def bridge_fixture():
    csms = VirtualCSMS()
    mqtt_mock = MockMQTTCollector()
    bridge = OCPPBridge(
        station_id="ST001",
        connector_id="1",
        mode="virtual",
        mqtt_svc=mqtt_mock,
        virtual_csms=csms,
    )
    await bridge.start()
    try:
        yield bridge, mqtt_mock, csms
    finally:
        await bridge.stop()


class TestRemoteStartTransactionAdversarialMatrix:
    """Exhaustive test matrix for handle_remote_start_transaction."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_payload", [
        None,
        "",
        "not_a_dict",
        12345,
        -1,
        3.14159,
        True,
        False,
        [],
        ["connectorId", 1],
    ])
    async def test_non_dict_payload_rejection_and_zero_relay_energize(
        self, bridge_fixture, invalid_payload
    ):
        bridge, mqtt_mock, csms = bridge_fixture
        res = await bridge.handle_remote_start_transaction(invalid_payload)
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "AVAILABLE"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_id_tag", [
        None,
        "",
        "   ",
        "\t\n",
        12345,
        0,
        -99,
        3.14,
        True,
        False,
        [],
        ["RFID_1234"],
        {"id": "TAG1"},
    ])
    async def test_invalid_id_tag_rejection_and_zero_relay_energize(
        self, bridge_fixture, invalid_id_tag
    ):
        bridge, mqtt_mock, csms = bridge_fixture
        payload = {"connectorId": 1}
        if invalid_id_tag is not None:
            payload["idTag"] = invalid_id_tag

        res = await bridge.handle_remote_start_transaction(payload)
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "AVAILABLE"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_connector", [
        "invalid",
        "abc",
        "1.5",
        "-1",
        -1,
        -999,
        2,  # Station ST001 has connector 1
        99,
        1000,
        [],
        [1],
        {"conn": 1},
    ])
    async def test_invalid_and_mismatched_connector_id_rejection(
        self, bridge_fixture, invalid_connector
    ):
        bridge, mqtt_mock, csms = bridge_fixture
        res = await bridge.handle_remote_start_transaction({
            "connectorId": invalid_connector,
            "idTag": "VALID_TAG_001",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_connector_id_zero_is_accepted_as_default(self, bridge_fixture):
        """OCPP 1.6 specifies connectorId=0 means 'any connector'."""
        bridge, mqtt_mock, csms = bridge_fixture
        res = await bridge.handle_remote_start_transaction({
            "connectorId": 0,
            "idTag": "VALID_TAG_002",
        })
        assert res.get("status") == "Accepted"
        assert mqtt_mock.energized_count == 1
        assert bridge.everest_manager.state == "CHARGING"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fault_condition", [
        "FAULTED_STATE",
        "TRIPPED_BOARD",
        "BOTH_FAULTED_AND_TRIPPED",
    ])
    async def test_fault_and_trip_guards_prevent_relay_energize(
        self, bridge_fixture, fault_condition
    ):
        bridge, mqtt_mock, csms = bridge_fixture

        if fault_condition == "FAULTED_STATE":
            bridge.everest_manager.state = "FAULTED"
        elif fault_condition == "TRIPPED_BOARD":
            bridge.everest_board.is_tripped = True
        elif fault_condition == "BOTH_FAULTED_AND_TRIPPED":
            bridge.everest_manager.state = "FAULTED"
            bridge.everest_board.is_tripped = True

        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "VALID_TAG_FAULT_TEST",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.active_transaction_id is None

    @pytest.mark.asyncio
    async def test_already_charging_prevents_duplicate_relay_energize(self, bridge_fixture):
        bridge, mqtt_mock, csms = bridge_fixture
        # Transition to CHARGING
        bridge.everest_manager.state = "CHARGING"
        bridge.everest_manager.active_transaction_id = 9999

        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "VALID_TAG_ALREADY_CHARGING",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.active_transaction_id == 9999

    @pytest.mark.asyncio
    async def test_reserved_state_mismatched_id_tag_rejected(self, bridge_fixture):
        bridge, mqtt_mock, csms = bridge_fixture
        bridge.everest_manager.state = "RESERVED"
        bridge.everest_manager.active_reservation_id = 42
        bridge.everest_auth.last_credential = "RESERVED_USER_TAG"

        # Attempt remote start with a DIFFERENT tag
        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "UNAUTHORIZED_INTRUDER_TAG",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "RESERVED"

    @pytest.mark.asyncio
    async def test_reserved_state_matching_id_tag_accepted_with_reservation_id(
        self, bridge_fixture
    ):
        bridge, mqtt_mock, csms = bridge_fixture
        bridge.everest_manager.state = "RESERVED"
        bridge.everest_manager.active_reservation_id = 42
        bridge.everest_auth.last_credential = "RESERVED_USER_TAG"

        # Attempt remote start with MATCHING tag
        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "RESERVED_USER_TAG",
        })
        assert res.get("status") == "Accepted"
        assert mqtt_mock.energized_count == 1
        assert bridge.everest_manager.state == "CHARGING"
        assert bridge.everest_manager.active_transaction_id is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("csms_response", [
        {"transactionId": 101, "idTagInfo": {"status": "Blocked"}},
        {"transactionId": 102, "idTagInfo": {"status": "Invalid"}},
        {"transactionId": 103, "idTagInfo": {"status": "Expired"}},
        {"transactionId": None, "idTagInfo": {"status": "Accepted"}},
        {},
        {"idTagInfo": {"status": "Accepted"}},  # Missing transactionId
    ])
    async def test_csms_rejected_start_transaction_never_energizes_relay(
        self, bridge_fixture, csms_response
    ):
        bridge, mqtt_mock, csms = bridge_fixture

        async def mocked_start_tx(*args, **kwargs):
            return csms_response

        bridge.send_start_transaction = mocked_start_tx

        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "TEST_TAG_REJECTED",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "AVAILABLE"
        assert bridge.everest_manager.active_transaction_id is None

    @pytest.mark.asyncio
    async def test_csms_exception_during_start_transaction_never_energizes_relay(
        self, bridge_fixture
    ):
        bridge, mqtt_mock, csms = bridge_fixture

        # Mock send_start_transaction to simulate network crash or timeout
        async def failing_start_transaction(*args, **kwargs):
            raise ConnectionError("Simulated network drop to CSMS")

        bridge.send_start_transaction = failing_start_transaction

        res = await bridge.handle_remote_start_transaction({
            "connectorId": 1,
            "idTag": "VALID_TAG_EXCEPTION_TEST",
        })
        assert res.get("status") == "Rejected"
        assert mqtt_mock.energized_count == 0
        assert bridge.everest_manager.state == "AVAILABLE"
        assert bridge.everest_manager.active_transaction_id is None

    @pytest.mark.asyncio
    async def test_high_concurrency_race_condition_remote_start_bombardment(
        self, bridge_fixture
    ):
        """
        Adversarial Concurrency Stress:
        50 concurrent remote start requests fired simultaneously.
        Only exactly ONE transaction must be started, exactly ONE relay ENERGIZE command issued.
        All 49 other concurrent calls must be Rejected without duplicate energization.
        """
        bridge, mqtt_mock, csms = bridge_fixture

        payloads = [
            {"connectorId": 1, "idTag": f"CONCURRENT_TAG_{i}"}
            for i in range(50)
        ]

        results = await asyncio.gather(
            *(bridge.handle_remote_start_transaction(p) for p in payloads)
        )

        accepted_count = sum(1 for r in results if r.get("status") == "Accepted")
        rejected_count = sum(1 for r in results if r.get("status") == "Rejected")

        assert accepted_count == 1, f"Expected exactly 1 Accepted, got {accepted_count}"
        assert rejected_count == 49, f"Expected 49 Rejected, got {rejected_count}"
        assert mqtt_mock.energized_count == 1, f"Expected exactly 1 relay energize, got {mqtt_mock.energized_count}"
        assert bridge.everest_manager.state == "CHARGING"
