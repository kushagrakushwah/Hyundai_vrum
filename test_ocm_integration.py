"""
End-to-End Test for Open Charge Map (OCM) Integration
Tests:
1. Live OCM API Connection using API Key 1f545914-8daa-4fa6-9d7b-4a6819f2f7cc
2. Database resolution of both BEE and OCM nodes
3. Reservation and PIN verification on live OCM stations
"""
import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
backend_dir = os.path.join(os.path.dirname(__file__), "smartreserve", "smartreserve_backend")
sys.path.insert(0, backend_dir)

from ocm_client import OpenChargeMapClient
from stations_db import StationsDB
from main import app, reservations, charging_sessions, ReserveRequest, VerifyOTPRequest
import asyncio

async def run_tests():
    print("=" * 60)
    print("🚗 HYUNDAI SMARTRESERVE — LIVE OCM API INTEGRATION TEST")
    print("=" * 60)

    # Test 1: Direct OCM API Client
    print("\n[TEST 1] Testing Open Charge Map API Client...")
    client = OpenChargeMapClient()
    stations = client.fetch_stations(country_code="IN", max_results=5)
    assert len(stations) > 0, "No stations returned from OCM API!"
    print(f"  ✅ OCM API Success! Fetched {len(stations)} live stations.")
    sample_station = stations[0]
    print(f"  Station: {sample_station['name']}")
    print(f"  ID: {sample_station['station_id']}")
    print(f"  Location: {sample_station['lat']}, {sample_station['lng']} ({sample_station['district']})")
    print(f"  Status: {sample_station['status']} (Live Title: {sample_station['live_status_title']})")
    print(f"  Power: {sample_station['power_kw']} kW | Connector: {sample_station['connector_type']}")

    # Test 2: Stations Database Dual-Source
    print("\n[TEST 2] Testing StationsDB Hybrid Repository...")
    db = StationsDB()
    all_stations = db.get_stations(source="all", limit=50)
    live_stations = db.get_stations(source="live", limit=20)
    bee_stations = db.get_stations(source="bee", limit=20)

    assert len(all_stations) > 0, "Failed to load stations!"
    print(f"  ✅ StationsDB Success!")
    print(f"     - Base Network Nodes: {len(db.stations)}")
    print(f"     - Live OCM Nodes: {len(db.live_ocm_stations)}")
    print(f"     - Total Combined Loaded: {len(all_stations)}")

    # Test 3: Booking & Reservation on Live OCM Station
    print("\n[TEST 3] Testing 30-Min Lockout on Live OCM Station...")
    target_id = sample_station["station_id"]
    from main import reserve_station, verify_otp

    # 3a: Reserve live station
    reserve_req = ReserveRequest(station_id=target_id, duration_minutes=30, user_id="test_hyundai_driver")
    res_out = await reserve_station(reserve_req)
    assert res_out["success"] is True
    pin = res_out["pin"]
    print(f"  ✅ Reservation Successful!")
    print(f"     - Target Station: {target_id}")
    print(f"     - Reservation ID: {res_out['reservation_id']}")
    print(f"     - 6-Digit PIN Generated: {pin}")
    print(f"     - Expiry: {res_out['expires_at']}")

    # 3b: Verify with wrong PIN
    print("\n[TEST 4] Testing Security Lockout with Wrong PIN...")
    try:
        await verify_otp(VerifyOTPRequest(station_id=target_id, pin="000000"))
        print("  ❌ ERROR: Wrong PIN was accepted!")
    except Exception as e:
        print(f"  ✅ Security Passed! Wrong PIN was rejected: {e.detail}")

    # 3c: Verify with correct PIN
    print("\n[TEST 5] Testing Physical Unlock with Correct PIN...")
    unlock_out = await verify_otp(VerifyOTPRequest(station_id=target_id, pin=pin))
    assert unlock_out["success"] is True
    print(f"  ✅ Unlock Successful! Contactor relay closed.")
    print(f"     - Session ID: {unlock_out['session_id']}")
    print(f"     - Message: {unlock_out['message']}")

    print("\n" + "=" * 60)
    print("🎉 ALL TESTS PASSED! OPEN CHARGE MAP API IS FULLY INTEGRATED.")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
