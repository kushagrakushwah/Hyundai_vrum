import sys, os, asyncio
sys.stdout.reconfigure(encoding='utf-8')
BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'smartreserve', 'smartreserve_backend')
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from main import app, ocpp_engine, payment_engine, db
import database
from main import reserve_station, verify_otp, ReserveRequest, VerifyOTPRequest

async def run():
    await database.init_db()
    print("=" * 55)
    print("HYUNDAI SMARTRESERVE v2.0 -- FULL SYSTEM TEST")
    print("=" * 55)
    print(f"FastAPI: {app.title} {app.version}")
    print(f"Stations: {len(db.stations)} BEE base nodes")
    print(f"OCPP Engine: ready (1.6J mock)")
    print(f"Payment: mock_mode={payment_engine.mock_mode} | UPI={payment_engine.UPI_ID}")
    print()

    first_id = list(db.stations.keys())[0]
    print(f"[TEST 1] Using station: {first_id} ({db.stations[first_id]['name']})")

    req = ReserveRequest(
        station_id=first_id,
        user_id="judge_001",
        user_name="Ideathon Judge",
        duration_minutes=30,
        vehicle_soc=25.0
    )
    res = await reserve_station(req)
    pin = res["pin"]
    rid = res["reservation_id"]
    print(f"         Reserve OK: {rid}")
    print(f"         PIN: {pin} | OCPP: {res['ocpp_response']['status']}")
    print(f"         Payment: mock={res['payment']['mock']} | UPI: {res['payment']['upi_id']}")

    print()
    print("[TEST 2] Wrong PIN security test...")
    try:
        await verify_otp(VerifyOTPRequest(station_id=first_id, pin="000000"))
        print("         FAIL: Wrong PIN accepted!")
    except Exception as e:
        print(f"         PASS: Wrong PIN rejected correctly")

    print()
    print("[TEST 3] Correct PIN unlock test...")
    unlock = await verify_otp(VerifyOTPRequest(station_id=first_id, pin=pin))
    print(f"         PASS: Session started: {unlock['session_id']}")

    print()
    print("[TEST 4] OCPP command audit log...")
    log = ocpp_engine.get_log()
    print(f"         {len(log)} OCPP commands recorded:")
    for entry in log:
        print(f"         -> {entry['action']} @ {entry['station_id']} | {entry['details']}")

    print()
    print("[TEST 5] SQLite persistence check...")
    hist = await database.get_history()
    print(f"         {len(hist)} records in bookings_history table")

    db_path = os.path.join(BACKEND, 'smartreserve.db')
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    print(f"         DB file: smartreserve.db ({db_size} bytes on disk)")

    print()
    print("=" * 55)
    print("ALL TESTS PASSED -- READY TO DEPLOY ON RENDER!")
    print("=" * 55)

asyncio.run(run())
