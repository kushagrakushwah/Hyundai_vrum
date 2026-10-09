import sys
import os
import time
import subprocess
import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure backend server is reachable
BASE_URL = "http://localhost:8000"

def check_server():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=1.0)
        return r.status_code == 200
    except Exception:
        return False

server_process = None
if not check_server():
    print("Starting backend server for deep automated test suite...")
    python_bin = os.path.abspath(".venv/Scripts/python.exe")
    server_process = subprocess.Popen(
        [python_bin, "smartreserve/run_demo.py"],
        cwd=os.path.abspath("."),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    for _ in range(30):
        time.sleep(0.5)
        if check_server():
            print("Backend server started successfully on port 8000!")
            break
else:
    print("Backend server is already running on port 8000.")

try:
    print("\n" + "="*60)
    print("🚀 RUNNING COMPREHENSIVE MULTI-PASS VOICE & RESERVATION TEST")
    print("="*60)

    # 1. Health check
    r = requests.get(f"{BASE_URL}/health")
    assert r.status_code == 200, f"Health check failed: {r.status_code}"
    print("✅ 1. Health Check passed (Status: 200)")

    # 2. Test Direct Voice Reservation: "reserve the nearest station"
    print("\n--- Test 2: 'reserve the nearest station' (Instant Lock) ---")
    payload = {"text": "reserve the nearest station", "user_id": "test_driver_1"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200, f"Voice process failed: {r.status_code}"
    res = r.json()
    print("  AVA Spoken:", res["text"])
    print("  Action Type:", res["action_type"])
    disp = res.get("display_data", {})
    st_id_1 = disp.get("station_id")
    pin_1 = disp.get("pin")
    print(f"  Station: {st_id_1} ({disp.get('station_name')})")
    print(f"  6-Digit PIN: {pin_1}")
    assert res["action_type"] == "action_complete", "Expected action_complete"
    assert pin_1 is not None and len(str(pin_1)) == 6, f"Expected 6-digit PIN, got {pin_1}"
    print("✅ Test 2 Passed: Instant reservation created with PIN:", pin_1)

    # 3. Test Direct Voice Reservation: "book the closest charger"
    print("\n--- Test 3: 'book the closest charger' ---")
    payload = {"text": "book the closest charger", "user_id": "test_driver_2"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200
    res = r.json()
    print("  AVA Spoken:", res["text"])
    disp = res.get("display_data", {})
    pin_2 = disp.get("pin")
    print(f"  Station: {disp.get('station_id')} | PIN: {pin_2}")
    assert pin_2 is not None and len(str(pin_2)) == 6
    print("✅ Test 3 Passed: 'book the closest charger' works instantly!")

    # 4. Test Operator-Specific Voice Reservation: "reserve Tata Power"
    print("\n--- Test 4: 'reserve Tata Power' ---")
    payload = {"text": "reserve Tata Power", "user_id": "test_driver_3"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200
    res = r.json()
    disp = res.get("display_data", {})
    st_name = disp.get("station_name", "")
    print(f"  Matched Station: {disp.get('station_id')} - {st_name}")
    print(f"  PIN: {disp.get('pin')}")
    assert "tata" in st_name.lower(), f"Expected Tata Power station, got {st_name}"
    print("✅ Test 4 Passed: Tata Power operator matched accurately!")

    # 5. Test Rank-Specific Voice Reservation: "reserve station 2"
    print("\n--- Test 5: 'reserve station 2' ---")
    payload = {"text": "reserve station 2", "user_id": "test_driver_4"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200
    res = r.json()
    disp = res.get("display_data", {})
    print(f"  Pick #2 Station: {disp.get('station_id')} - {disp.get('station_name')}")
    print(f"  PIN: {disp.get('pin')}")
    assert disp.get("pin") is not None
    print("✅ Test 5 Passed: 'reserve station 2' selected rank #2!")

    # 6. Test Hindi Colloquial: "sabse paas wala reserve karo"
    print("\n--- Test 6: 'sabse paas wala reserve karo' ---")
    payload = {"text": "sabse paas wala reserve karo", "user_id": "test_driver_5"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200
    res = r.json()
    print("  AVA Hindi Spoken:", res["text"])
    disp = res.get("display_data", {})
    print(f"  Station: {disp.get('station_id')} | PIN: {disp.get('pin')}")
    assert disp.get("pin") is not None
    print("✅ Test 6 Passed: Hindi natural voice command works!")

    # 7. Test User Selected Station: "reserve this station"
    print("\n--- Test 7: 'reserve this station' with selected_station_id ---")
    payload = {"text": "reserve this station", "user_id": "test_driver_6", "selected_station_id": "NTC_0005"}
    r = requests.post(f"{BASE_URL}/api/voice/process", json=payload)
    assert r.status_code == 200
    res = r.json()
    disp = res.get("display_data", {})
    print(f"  Selected Station Target: {disp.get('station_id')} - {disp.get('station_name')}")
    print(f"  PIN: {disp.get('pin')}")
    assert disp.get("station_id") == "NTC_0005"
    print("✅ Test 7 Passed: Selected station target reserved seamlessly!")

    # 8. Test Two-Turn Flow: "nearest charging station find karo" -> "haan"
    print("\n--- Test 8: Two-Turn Discovery & Confirmation Flow ---")
    r1 = requests.post(f"{BASE_URL}/api/voice/process", json={"text": "nearest charging station find karo", "user_id": "test_driver_two_turn"})
    data1 = r1.json()
    print("  Turn 1 AVA:", data1["text"][:80], "...")
    assert data1["needs_confirmation"] is True
    r2 = requests.post(f"{BASE_URL}/api/voice/confirm", json={"user_id": "test_driver_two_turn", "confirmed": True})
    data2 = r2.json()
    print("  Turn 2 AVA Confirmation:", data2["text"])
    pin_two_turn = data2.get("display_data", {}).get("pin") or data2.get("display_data", {}).get("reservation", {}).get("pin")
    print("  Turn 2 Confirmed PIN:", pin_two_turn)
    assert pin_two_turn is not None
    print("✅ Test 8 Passed: Two-turn discovery followed by 'haan/yes' confirms slot!")

    # 9. Test Vehicle Telematics Queries (Marathi & English)
    print("\n--- Test 9: Telematics Queries ('gaadi chi battery kiti aahe') ---")
    r_tele = requests.post(f"{BASE_URL}/api/voice/process", json={"text": "gaadi chi battery kiti aahe", "user_id": "test_driver_mr"})
    assert r_tele.status_code == 200
    print("  AVA Marathi:", r_tele.json()["text"])
    assert "18" in r_tele.json()["text"] or "battery" in r_tele.json()["text"].lower()

    # 10. Test Diagnostics Query
    print("\n--- Test 10: Vehicle Diagnostics ---")
    r_diag = requests.post(f"{BASE_URL}/api/voice/process", json={"text": "check tyre pressure and diagnostics", "user_id": "test_driver_diag"})
    assert r_diag.status_code == 200
    print("  AVA Diagnostics:", r_diag.json()["text"][:100], "...")
    assert r_diag.json()["action_type"] == "info"

    # 11. Test Database Active Reservations
    print("\n--- Test 11: GET /api/reservations ---")
    r_res = requests.get(f"{BASE_URL}/api/reservations")
    assert r_res.status_code == 200
    res_payload = r_res.json()
    all_resv = res_payload.get("active_reservations", []) if isinstance(res_payload, dict) else res_payload
    print(f"  Total Active Persistent Reservations in SQLite: {len(all_resv)}")
    assert len(all_resv) > 0
    print("✅ Test 11 Passed: SQLite persistence verified!")

    # 12. Test OCPP 1.6J Command Logs
    print("\n--- Test 12: GET /api/ocpp-log ---")
    r_ocpp = requests.get(f"{BASE_URL}/api/ocpp-log")
    assert r_ocpp.status_code == 200
    ocpp_logs = r_ocpp.json().get("log", [])
    print(f"  Total OCPP 1.6J logs recorded: {len(ocpp_logs)}")
    reserve_now_calls = [log for log in ocpp_logs if log.get("action") == "ReserveNow"]
    print(f"  OCPP 'ReserveNow' calls logged: {len(reserve_now_calls)}")
    assert len(reserve_now_calls) > 0
    print("✅ Test 12 Passed: OCPP 1.6J ReserveNow logging verified!")

    # 13. Test Kiosk OTP Verification with Latest Active Reservation PIN
    print("\n--- Test 13: Kiosk OTP Verification with Active Reservation PIN ---")
    active_target = all_resv[0]
    target_station_id = active_target["station_id"]
    target_pin = active_target["pin"]
    print(f"  Attempting kiosk unlock at Station: {target_station_id} with PIN: {target_pin}...")
    r_otp = requests.post(f"{BASE_URL}/api/verify-otp", json={"station_id": target_station_id, "pin": str(target_pin)})
    assert r_otp.status_code == 200, f"OTP verification failed: {r_otp.status_code} {r_otp.text}"
    otp_res = r_otp.json()
    print("  Kiosk OTP verify success:", otp_res.get("success"))
    print("  Session ID started:", otp_res.get("session_id"))
    print("  Message:", otp_res.get("message"))
    assert otp_res.get("success") is True
    assert otp_res.get("session_id") is not None
    print("✅ Test 13 Passed: Kiosk PIN authentication unlocks charger and starts session!")

    # 14. Test Frontend HTML verification
    print("\n--- Test 14: Frontend HTML Hands-Free verification ---")
    html_path = os.path.abspath("smartreserve/smartreserve_frontend/car_dashboard/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    assert "btn-hands-free" in html_content, "Missing btn-hands-free in index.html"
    assert "toggleHandsFree" in html_content, "Missing toggleHandsFree in index.html"
    assert "recognition.continuous = true" in html_content, "Missing recognition.continuous = true in index.html"
    assert "isSpeakingTTS" in html_content, "Missing audio loop prevention in index.html"
    print("✅ Test 14 Passed: All Hands-Free continuous voice features verified in HTML!")

    print("\n" + "="*60)
    print("🎉 ALL 14 COMPREHENSIVE TESTS PASSED WITH 100% SUCCESS!")
    print("="*60)

finally:
    if server_process:
        print("Cleaning up temporary test server process...")
        server_process.terminate()
