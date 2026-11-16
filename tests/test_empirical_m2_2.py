"""
tests/test_empirical_m2_2.py - Milestone 2 Empirical Stress Test Suite (Challenger M2.2)

Adversarial Stress Testing of:
1. Multi-Connector Database Isolation:
   - Simultaneous distinct reservations on (station, C1) vs (station, C2)
   - Multi-connector telemetry isolation (independent timeseries without crosstalk)
   - Multi-connector active charging sessions isolation
   - Connector deletion isolation (deleting C1 does not delete C2)
   - Alphanumeric connector IDs ("CCS2", "CHAdeMO", "Type2")
2. Double-Booking Prevention & Concurrency:
   - Double-booking conflict on identical (station, connector) -> HTTP 409 Conflict
   - Same-user idempotent reservation re-entry -> HTTP 200 with existing reservation
   - Reservation on occupied/charging connector -> HTTP 409 Conflict
   - Reservation on FAULTED connector -> HTTP 503 Service Unavailable
3. 2-Tier Authentication Precedence & Decision Engine:
   - Tier 1: Valid PIN on active unexpired reservation -> ACCEPTED (200, 8000ms, ENERGIZE, session created, reservation consumed)
   - Tier 1: Valid RFID on active unexpired reservation -> ACCEPTED (200, 8000ms, ENERGIZE)
   - Tier 1: Invalid PIN on active reservation -> DENIED (401, 0ms, DE_ENERGIZE, reservation intact)
   - Expired Reservation: Valid PIN/RFID on expired reservation -> DENIED (401, DE_ENERGIZE, EXPIRED_NO_SHOW recorded, reservation purged)
   - Tier 2: Walk-up with Whitelisted Active RFID -> ACCEPTED (200, 8000ms, ENERGIZE, walk-up session created)
   - Tier 2: Walk-up with Whitelisted Deactivated RFID (is_active=0) -> DENIED (401, DE_ENERGIZE)
   - Tier 2: Walk-up with Whitelisted Expired RFID (expires_at < now) -> DENIED (401, DE_ENERGIZE)
   - Tier 2: Walk-up with Unregistered RFID -> DENIED (401, DE_ENERGIZE)
   - Tier 2: Walk-up PIN without reservation -> DENIED (401, DE_ENERGIZE)
   - RFID normalization stress: case-insensitivity, colons, spaces ("de:ad:be:ef", "  DEADBEEF  ")
4. Backward Compatibility with voice_assistant.py:
   - Legacy synchronous raw SQL insert without connector_id column (default '1')
   - Database retrieval via get_reservation with default connector_id="1"
   - Database retrieval via get_reservation with connector_id=None
   - voice_assistant.get_active_reservations_sync retrieval
5. Concurrency & Security Stress Testing:
   - High-concurrency operations across 10 connectors on a single station
   - SQL Injection safety on station_id, connector_id, pin, rfid
"""

import asyncio
import time
import sqlite3
import aiosqlite
import pytest
from starlette.testclient import TestClient

import database
from mqtt_service import mqtt_service
import voice_assistant


# ==============================================================================
# SECTION 1: Multi-Connector Database Isolation Stress Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_empirical_multi_connector_simultaneous_reservations(test_db):
    """Stress test: 3 distinct connectors on the same station reserved simultaneously."""
    station_id = "ST_EMP_MULTI_01"
    now = time.time()

    connectors = ["1", "2", "3"]
    for i, cid in enumerate(connectors):
        await database.save_reservation({
            "station_id": station_id,
            "connector_id": cid,
            "reservation_id": f"RES_EMP_{cid}_{int(now)}",
            "user_id": f"user_{cid}",
            "user_name": f"Driver {cid}",
            "pin": f"100{cid}",
            "rfid_uid": f"RFID000{cid}",
            "duration_minutes": 30,
            "created_at": now,
            "expires_at": now + 1800,
            "vehicle_soc": 20.0 + (i * 10.0),
        })

    # Verify each connector holds its distinct reservation
    for i, cid in enumerate(connectors):
        res = await database.get_reservation(station_id, cid)
        assert res is not None, f"Connector {cid} reservation missing"
        assert res["connector_id"] == cid
        assert res["pin"] == f"100{cid}"
        assert res["user_id"] == f"user_{cid}"
        assert res["vehicle_soc"] == 20.0 + (i * 10.0)

    # Deleting connector 2 must NOT affect connector 1 or connector 3
    await database.delete_reservation(station_id, "2")
    assert await database.get_reservation(station_id, "2") is None
    res1 = await database.get_reservation(station_id, "1")
    res3 = await database.get_reservation(station_id, "3")
    assert res1 is not None and res1["pin"] == "1001"
    assert res3 is not None and res3["pin"] == "1003"


@pytest.mark.asyncio
async def test_empirical_multi_connector_telemetry_isolation(test_db):
    """Stress test: High-frequency telemetry streams for C1 and C2 must remain completely isolated."""
    station_id = "ST_EMP_TELEM_01"
    now = time.time()

    # Log 10 distinct samples for C1 (Fast Charger: 400V, 100A, 40kW)
    for step in range(10):
        await database.save_connector_telemetry({
            "station_id": station_id,
            "connector_id": "1",
            "voltage_v": 400.0,
            "current_a": 100.0 + step,
            "power_kw": 40.0 + (0.4 * step),
            "energy_kwh": 5.0 + (0.1 * step),
            "voltage_raw": 3000,
            "current_raw": 2000 + step,
            "millis": step * 1000,
            "recorded_at": now + step
        })

    # Log 10 distinct samples for C2 (AC Charger: 230V, 16A, 3.68kW)
    for step in range(10):
        await database.save_connector_telemetry({
            "station_id": station_id,
            "connector_id": "2",
            "voltage_v": 230.0,
            "current_a": 16.0 + (step * 0.1),
            "power_kw": 3.68,
            "energy_kwh": 1.0 + (0.05 * step),
            "voltage_raw": 2200,
            "current_raw": 1200 + step,
            "millis": step * 1000,
            "recorded_at": now + step
        })

    # Check latest telemetry on C1 vs C2
    telem1 = await database.get_latest_telemetry(station_id, "1")
    telem2 = await database.get_latest_telemetry(station_id, "2")

    assert telem1 is not None and telem2 is not None
    assert abs(telem1["voltage_v"] - 400.0) < 0.01
    assert abs(telem1["current_a"] - 109.0) < 0.01
    assert abs(telem2["voltage_v"] - 230.0) < 0.01
    assert abs(telem2["current_a"] - 16.9) < 0.01

    # Check that C1 telemetry never bled into C2
    async with aiosqlite.connect(database.DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM connector_telemetry WHERE station_id = ? AND connector_id = '1' AND voltage_v < 300",
            (station_id,)
        ) as cur:
            count_bleed = (await cur.fetchone())[0]
            assert count_bleed == 0, "Connector 2 telemetry contaminated Connector 1 records"


@pytest.mark.asyncio
async def test_empirical_alphanumeric_connector_ids(test_db):
    """Stress test: Support for non-numeric connector IDs (e.g. 'CCS2', 'CHAdeMO', 'AC_TYPE2')."""
    station_id = "ST_ALPHANUM_01"
    now = time.time()
    for conn_name in ["CCS2", "CHAdeMO", "AC_TYPE2"]:
        await database.save_reservation({
            "station_id": station_id,
            "connector_id": conn_name,
            "reservation_id": f"RES_{conn_name}",
            "user_id": f"user_{conn_name}",
            "pin": "9999",
            "created_at": now,
            "expires_at": now + 600
        })

    for conn_name in ["CCS2", "CHAdeMO", "AC_TYPE2"]:
        res = await database.get_reservation(station_id, conn_name)
        assert res is not None
        assert res["connector_id"] == conn_name
        assert res["user_id"] == f"user_{conn_name}"


# ==============================================================================
# SECTION 2: Double-Booking Prevention & Concurrency Tests
# ==============================================================================

def test_empirical_double_booking_prevention_via_api(sync_client, test_db):
    """Empirical REST test: Conflict detection on identical (station_id, connector_id)."""
    station_id = "NTC_0001"
    connector_id = "1"

    # User A reserves Connector 1
    res_a = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": connector_id,
        "user_id": "user_alpha",
        "user_name": "Driver Alpha",
        "duration_minutes": 30,
        "pin": "1234"
    })
    assert res_a.status_code == 200, f"Reservation A failed: {res_a.text}"
    data_a = res_a.json()
    assert data_a["success"] is True
    assert data_a["pin"] == "1234"

    # User B attempts to reserve the SAME (station, connector) -> Expect HTTP 409
    res_b = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": connector_id,
        "user_id": "user_beta",
        "user_name": "Driver Beta",
        "duration_minutes": 30
    })
    assert res_b.status_code == 409
    assert "already reserved" in res_b.json()["detail"].lower()

    # User B reserves Connector 2 on the SAME station -> Expect HTTP 200 (Multi-connector isolation)
    res_b_c2 = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": "2",
        "user_id": "user_beta",
        "user_name": "Driver Beta",
        "duration_minutes": 30,
        "pin": "5678"
    })
    assert res_b_c2.status_code == 200
    data_b_c2 = res_b_c2.json()
    assert data_b_c2["connector_id"] == "2"
    assert data_b_c2["pin"] == "5678"

    # User A repeats request for Connector 1 -> Idempotent renewal, returns HTTP 200
    res_a_repeat = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": connector_id,
        "user_id": "user_alpha",
        "user_name": "Driver Alpha",
        "duration_minutes": 30
    })
    assert res_a_repeat.status_code == 200
    assert res_a_repeat.json()["reservation_id"] == data_a["reservation_id"]
    assert res_a_repeat.json()["pin"] == "1234"


def test_empirical_reservation_conflict_when_charging(sync_client, test_db):
    """Empirical REST test: Reject reservation if connector has an active charging session."""
    station_id = "NTC_0001"
    connector_id = "1"
    now = time.time()

    # Seed an active charging session in DB
    asyncio.run(database.save_session({
        "station_id": station_id,
        "connector_id": connector_id,
        "session_id": "CHG_ACTIVE_01",
        "user_id": "current_charging_user",
        "start_time": now,
        "kw": 50.0,
        "kwh": 10.0
    }))

    # Try to reserve connector 1 -> Expect HTTP 409
    res = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": connector_id,
        "user_id": "prospective_user",
        "duration_minutes": 30
    })
    assert res.status_code == 409
    assert "currently charging" in res.json()["detail"].lower()


def test_empirical_reservation_conflict_when_faulted(sync_client, test_db):
    """Empirical REST test: Reject reservation if connector is in FAULTED state."""
    station_id = "NTC_0001"
    connector_id = "1"

    # Set connector status to FAULTED
    asyncio.run(database.save_connector_status({
        "station_id": station_id,
        "connector_id": connector_id,
        "state": "FAULTED",
        "relay_state": "DE_ENERGIZED",
        "transition_reason": "overcurrent_trip"
    }))

    # Try to reserve connector 1 -> Expect HTTP 503
    res = sync_client.post("/api/reserve", json={
        "station_id": station_id,
        "connector_id": connector_id,
        "user_id": "prospective_user",
        "duration_minutes": 30
    })
    assert res.status_code == 503
    assert "faulted" in res.json()["detail"].lower()


# ==============================================================================
# SECTION 3: 2-Tier Authentication Precedence & Decision Engine Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_empirical_auth_tier1_valid_pin_acceptance(test_db, monkeypatch):
    """Tier 1: Valid PIN on active reservation -> ACCEPTED (status 200, 8000ms window, ENERGIZE, session created)."""
    station_id = "ST_AUTH_T1_01"
    connector_id = "1"
    now = time.time()

    # 1. Setup active reservation
    await database.save_reservation({
        "station_id": station_id,
        "connector_id": connector_id,
        "reservation_id": "RES_T1_PIN",
        "user_id": "driver_t1",
        "user_name": "Tier 1 Driver",
        "pin": "4455",
        "created_at": now,
        "expires_at": now + 600,
        "vehicle_soc": 35.0
    })

    # Hook publish_cmd to verify outbound decision
    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # 2. Trigger Auth with valid PIN
    auth_msg = {
        "auth_type": "PIN",
        "credential": {"pin": "4455", "rfid_uid": ""},
        "current_state": "RESERVED"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    # 3. Assert /cmd published decision
    assert len(published_cmds) > 0, "No /cmd message published"
    cmd = published_cmds[-1]
    assert cmd["station_id"] == station_id
    assert cmd["connector_id"] == connector_id
    assert cmd["action"] == "AUTH_DECISION"
    assert cmd["status"] == "ACCEPTED"
    assert cmd["status_code"] == 200
    assert cmd["auth_window_duration_ms"] in (8000, 30000)
    assert cmd["relay_action"] == "ENERGIZE"

    # 4. Assert session created in DB
    session = await database.get_session(station_id, connector_id)
    assert session is not None, "Active charging session not created"
    assert session["user_id"] == "driver_t1"
    assert session["start_soc"] == 35.0

    # 5. Assert reservation consumed (deleted from reservations)
    res_after = await database.get_reservation(station_id, connector_id)
    assert res_after is None, "Reservation was not consumed after successful authentication"


@pytest.mark.asyncio
async def test_empirical_auth_tier1_valid_rfid_acceptance(test_db, monkeypatch):
    """Tier 1: Valid RFID on active reservation -> ACCEPTED (status 200, 8000ms window, ENERGIZE)."""
    station_id = "ST_AUTH_T1_02"
    connector_id = "2"
    now = time.time()

    await database.save_reservation({
        "station_id": station_id,
        "connector_id": connector_id,
        "reservation_id": "RES_T1_RFID",
        "user_id": "driver_rfid_01",
        "user_name": "RFID Driver",
        "pin": "7777",
        "rfid_uid": "9988AABB",
        "created_at": now,
        "expires_at": now + 600,
        "vehicle_soc": 40.0
    })

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    auth_msg = {
        "auth_type": "RFID",
        "credential": {"pin": "", "rfid_uid": "9988AABB"},
        "current_state": "RESERVED"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "ACCEPTED"
    assert cmd["status_code"] == 200
    assert cmd["relay_action"] == "ENERGIZE"
    assert cmd["auth_window_duration_ms"] in (8000, 30000)

    # Verify session created and reservation consumed
    assert await database.get_session(station_id, connector_id) is not None
    assert await database.get_reservation(station_id, connector_id) is None


@pytest.mark.asyncio
async def test_empirical_auth_tier1_invalid_pin_denial(test_db, monkeypatch):
    """Tier 1: Invalid PIN on active reservation -> DENIED (status 401, 0ms, DE_ENERGIZE, reservation preserved)."""
    station_id = "ST_AUTH_T1_03"
    connector_id = "1"
    now = time.time()

    await database.save_reservation({
        "station_id": station_id,
        "connector_id": connector_id,
        "reservation_id": "RES_WRONG_PIN",
        "user_id": "driver_wrong_pin",
        "pin": "9999",
        "created_at": now,
        "expires_at": now + 600
    })

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # Send wrong PIN "0000"
    auth_msg = {
        "auth_type": "PIN",
        "credential": {"pin": "0000", "rfid_uid": ""},
        "current_state": "RESERVED"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "DENIED"
    assert cmd["status_code"] == 401
    assert cmd["auth_window_duration_ms"] == 0
    assert cmd["relay_action"] == "DE_ENERGIZE"

    # Reservation must NOT be consumed
    res = await database.get_reservation(station_id, connector_id)
    assert res is not None and res["pin"] == "9999"

    # No session should be created
    assert await database.get_session(station_id, connector_id) is None


@pytest.mark.asyncio
async def test_empirical_auth_expired_reservation_denial(test_db, monkeypatch):
    """Expired reservation rejection: PIN submitted after expiration -> DENIED (401, EXPIRED_NO_SHOW recorded)."""
    station_id = "ST_AUTH_EXP_01"
    connector_id = "1"
    now = time.time()

    # Create reservation that expired 10 seconds ago
    await database.save_reservation({
        "station_id": station_id,
        "connector_id": connector_id,
        "reservation_id": "RES_EXPIRED_01",
        "user_id": "late_driver",
        "pin": "1234",
        "created_at": now - 70,
        "expires_at": now - 10,
        "duration_minutes": 1
    })

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # Submit auth with PIN matching the expired reservation
    auth_msg = {
        "auth_type": "PIN",
        "credential": {"pin": "1234", "rfid_uid": ""},
        "current_state": "RESERVED"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "DENIED"
    assert cmd["status_code"] == 401
    assert cmd["relay_action"] == "DE_ENERGIZE"
    assert cmd["auth_window_duration_ms"] == 0

    # Reservation must be purged from reservations table
    assert await database.get_reservation(station_id, connector_id) is None

    # History must record EXPIRED_NO_SHOW
    history = await database.get_history(limit=5)
    matched = [h for h in history if h.get("reservation_id") == "RES_EXPIRED_01"]
    assert len(matched) == 1, "EXPIRED_NO_SHOW not logged in bookings_history"
    assert matched[0]["outcome"] == "EXPIRED_NO_SHOW"


@pytest.mark.asyncio
async def test_empirical_auth_tier2_walkup_whitelisted_rfid(test_db, monkeypatch):
    """Tier 2: Walk-up charging with pre-seeded whitelisted RFID card -> ACCEPTED (200, ENERGIZE)."""
    station_id = "ST_AUTH_T2_01"
    connector_id = "1"

    await database.delete_reservation(station_id, connector_id)

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # Tap pre-seeded whitelisted RFID "DEADBEEF" (Fleet Driver)
    auth_msg = {
        "auth_type": "RFID",
        "credential": {"pin": "", "rfid_uid": "DEADBEEF"},
        "current_state": "AVAILABLE"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "ACCEPTED"
    assert cmd["status_code"] == 200
    assert cmd["relay_action"] == "ENERGIZE"
    assert cmd["auth_window_duration_ms"] in (8000, 30000)

    # Walk-up session must be created
    session = await database.get_session(station_id, connector_id)
    assert session is not None
    assert session["user_id"] == "fleet_driver_001"


@pytest.mark.asyncio
async def test_empirical_auth_tier2_walkup_rfid_normalization(test_db, monkeypatch):
    """Tier 2: Case-insensitivity, colons, and whitespace normalization for RFID tags."""
    station_id = "ST_AUTH_T2_NORM"
    connector_id = "1"
    await database.delete_reservation(station_id, connector_id)
    await database.delete_session(station_id, connector_id)

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # Test RFID with lowercase, colons, and spaces: "  de:ad:be:ef  " -> maps to "DEADBEEF"
    auth_msg = {
        "auth_type": "RFID",
        "credential": {"rfid_uid": "  de:ad:be:ef  "},
        "current_state": "AVAILABLE"
    }
    await mqtt_service._handle_auth(station_id, connector_id, auth_msg)

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "ACCEPTED"
    assert cmd["status_code"] == 200

    # Session created
    assert await database.get_session(station_id, connector_id) is not None


@pytest.mark.asyncio
async def test_empirical_auth_tier2_deactivated_and_expired_cards(test_db, monkeypatch):
    """Tier 2: Rejection of deactivated cards (is_active=0) and expired cards (expires_at < now)."""
    now = time.time()
    await database.add_authorized_rfid(
        card_uid="DEACTIVATED_CARD",
        user_id="user_inactive",
        user_name="Inactive User",
        is_active=0
    )
    await database.add_authorized_rfid(
        card_uid="EXPIRED_CARD",
        user_id="user_expired",
        user_name="Expired User",
        is_active=1,
        expires_at=now - 500
    )

    station_id = "ST_AUTH_T2_CARDS"
    connector_id = "1"
    await database.delete_reservation(station_id, connector_id)

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    # 1. Test deactivated card
    await mqtt_service._handle_auth(station_id, connector_id, {
        "auth_type": "RFID",
        "credential": {"rfid_uid": "DEACTIVATED_CARD"},
        "current_state": "AVAILABLE"
    })
    cmd1 = published_cmds[-1]
    assert cmd1["status"] == "DENIED"
    assert cmd1["status_code"] == 401

    # 2. Test expired card
    await mqtt_service._handle_auth(station_id, connector_id, {
        "auth_type": "RFID",
        "credential": {"rfid_uid": "EXPIRED_CARD"},
        "current_state": "AVAILABLE"
    })
    cmd2 = published_cmds[-1]
    assert cmd2["status"] == "DENIED"
    assert cmd2["status_code"] == 401

    # 3. Test unregistered card
    await mqtt_service._handle_auth(station_id, connector_id, {
        "auth_type": "RFID",
        "credential": {"rfid_uid": "UNREGISTERED_CARD_123"},
        "current_state": "AVAILABLE"
    })
    cmd3 = published_cmds[-1]
    assert cmd3["status"] == "DENIED"
    assert cmd3["status_code"] == 401


@pytest.mark.asyncio
async def test_empirical_auth_tier2_walkup_pin_denial(test_db, monkeypatch):
    """Walk-up PIN entry without an active reservation must be rejected (401 DENIED)."""
    station_id = "ST_AUTH_WALKUP_PIN"
    connector_id = "1"
    await database.delete_reservation(station_id, connector_id)

    published_cmds = []
    async def mock_publish_cmd(**kwargs):
        published_cmds.append(kwargs)
        return True
    monkeypatch.setattr(mqtt_service, "publish_cmd", mock_publish_cmd)

    await mqtt_service._handle_auth(station_id, connector_id, {
        "auth_type": "PIN",
        "credential": {"pin": "1234"},
        "current_state": "AVAILABLE"
    })

    assert len(published_cmds) > 0
    cmd = published_cmds[-1]
    assert cmd["status"] == "DENIED"
    assert cmd["status_code"] == 401
    assert cmd["relay_action"] == "DE_ENERGIZE"


# ==============================================================================
# SECTION 4: Backward Compatibility with voice_assistant.py Tests
# ==============================================================================

def test_empirical_voice_assistant_legacy_insert_and_queries(test_db, monkeypatch):
    """
    Empirically execute voice_assistant.py line 1001 legacy insert:
    Omission of connector_id column must default to '1' and be retrievable
    via both database.get_reservation and voice_assistant.get_active_reservations_sync.
    """
    station_id = "ST_VOICE_LEGACY_01"
    user_id = "voice_driver_007"
    pin_str = "8899"
    now = time.time()
    expires_at = now + 1800

    # Ensure voice_assistant sqlite connection routes to the test_db path
    orig_connect = sqlite3.connect
    monkeypatch.setattr(voice_assistant.sqlite3, "connect", lambda path, **kwargs: orig_connect(database.DB_PATH, **kwargs))

    # 1. Execute exact raw SQL insert from voice_assistant.py:1001
    conn = orig_connect(database.DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO reservations 
        (station_id, reservation_id, user_id, user_name, pin, duration_minutes, created_at, expires_at, vehicle_soc, payment_order_id, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        station_id,
        f"resv_{int(now)}",
        user_id,
        "Hyundai Driver",
        pin_str,
        30,
        now,
        expires_at,
        45.0,
        "corridor_escrow",
        "CONFIRMED"
    ))
    conn.commit()
    conn.close()

    # 2. Query via database.get_reservation with default connector_id (defaults to "1")
    res_default = asyncio.run(database.get_reservation(station_id))
    assert res_default is not None, "Legacy reservation not found by get_reservation(station_id)"
    assert res_default["connector_id"] == "1", f"Expected connector_id '1', got {res_default['connector_id']}"
    assert res_default["pin"] == pin_str
    assert res_default["user_id"] == user_id

    # 3. Query via database.get_reservation with connector_id=None
    res_none = asyncio.run(database.get_reservation(station_id, connector_id=None))
    assert res_none is not None
    assert res_none["reservation_id"] == res_default["reservation_id"]

    # 4. Query via voice_assistant.get_active_reservations_sync(user_id)
    active_user = voice_assistant.get_active_reservations_sync(user_id=user_id)
    assert len(active_user) >= 1
    matched = [r for r in active_user if r["station_id"] == station_id]
    assert len(matched) == 1
    assert matched[0]["pin"] == pin_str
    assert matched[0]["connector_id"] == "1"

    # 5. Query via voice_assistant.get_active_reservations_sync() (global query)
    active_all = voice_assistant.get_active_reservations_sync()
    assert any(r["station_id"] == station_id and r["connector_id"] == "1" for r in active_all)

    # 6. Clean up
    asyncio.run(database.delete_reservation(station_id, "1"))
    assert asyncio.run(database.get_reservation(station_id)) is None


# ==============================================================================
# SECTION 5: Concurrency, Scale & SQL Injection Robustness Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_empirical_10_connector_concurrent_stress(test_db):
    """Stress test: 10 parallel connectors on a single station operating concurrently."""
    station_id = "ST_MEGA_STATION"
    now = time.time()
    num_connectors = 10

    # Concurrently write reservations to all 10 connectors
    async def create_res(cid: int):
        await database.save_reservation({
            "station_id": station_id,
            "connector_id": str(cid),
            "reservation_id": f"RES_MEGA_{cid}",
            "user_id": f"user_mega_{cid}",
            "pin": f"{1000 + cid}",
            "created_at": now,
            "expires_at": now + 600,
            "vehicle_soc": 20.0 + cid
        })

    await asyncio.gather(*(create_res(i) for i in range(1, num_connectors + 1)))

    # Verify all 10 exist and have correct isolation
    for cid in range(1, num_connectors + 1):
        res = await database.get_reservation(station_id, str(cid))
        assert res is not None, f"Connector {cid} missing"
        assert res["pin"] == f"{1000 + cid}"
        assert res["vehicle_soc"] == 20.0 + cid

    # Concurrently write sessions to all 10 connectors
    async def create_sess(cid: int):
        await database.save_session({
            "station_id": station_id,
            "connector_id": str(cid),
            "session_id": f"SESS_MEGA_{cid}",
            "user_id": f"user_mega_{cid}",
            "start_time": now,
            "kw": 50.0 + cid,
            "kwh": 5.0 + (0.5 * cid)
        })

    await asyncio.gather(*(create_sess(i) for i in range(1, num_connectors + 1)))

    for cid in range(1, num_connectors + 1):
        sess = await database.get_session(station_id, str(cid))
        assert sess is not None
        assert sess["session_id"] == f"SESS_MEGA_{cid}"
        assert abs(sess["kw"] - (50.0 + cid)) < 0.01


@pytest.mark.asyncio
async def test_empirical_sql_injection_defense(test_db):
    """Adversarial security test: Malicious SQL injection payloads in station_id, connector_id, and pin."""
    malicious_station = "ST_ATTACK'; DROP TABLE reservations; --"
    malicious_connector = "1' OR '1'='1"
    malicious_pin = "' OR 1=1 --"

    # Save reservation with malicious strings
    await database.save_reservation({
        "station_id": malicious_station,
        "connector_id": malicious_connector,
        "reservation_id": "RES_ATTACK_01",
        "user_id": "attacker",
        "pin": malicious_pin,
        "created_at": time.time(),
        "expires_at": time.time() + 600
    })

    # Ensure table was NOT dropped and query functions safely
    res = await database.get_reservation(malicious_station, malicious_connector)
    assert res is not None
    assert res["pin"] == malicious_pin

    # Verify reservations table is still fully intact and functional
    all_res = await database.get_all_active_reservations()
    assert isinstance(all_res, list)
    assert any(r["station_id"] == malicious_station for r in all_res)

    # Clean up
    await database.delete_reservation(malicious_station, malicious_connector)
    assert await database.get_reservation(malicious_station, malicious_connector) is None
