import sys
import json
import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

print("=== 1. Testing Health Endpoint ===")
r = requests.get("http://localhost:8000/health")
print("GET /health:", r.status_code, r.json())
assert r.status_code == 200

print("\n=== 2. Testing Vehicle Status ===")
r = requests.get("http://localhost:8000/api/vehicle/status")
print("GET /api/vehicle/status:", r.status_code)
status = r.json()
print("  SoC:", status["status"]["soc"], "%")
print("  Range:", status["status"]["range_km"], "km")
print("  Hours Left:", status["status"]["hours_remaining"], "hrs")
print("  Efficiency:", status["status"]["efficiency"], "km/kWh")
print("  Diagnostics:", status["diagnostics_summary"])

print("\n=== 3. Testing Vehicle Diagnostics ===")
r = requests.get("http://localhost:8000/api/vehicle/diagnostics")
print("GET /api/vehicle/diagnostics:", r.status_code)
diag = r.json()
print("  Component Health:", diag["component_health"])
print("  Service Status:", diag["service_status"])

print("\n=== 4. Testing Priority Engine (Real Stations Ranking) ===")
r = requests.get("http://localhost:8000/api/voice/recommend?target_soc=80&top_k=3")
print("GET /api/voice/recommend:", r.status_code)
rec_data = r.json()
print("  Spoken Summary:", rec_data["spoken_summary"])
for i, rec in enumerate(rec_data["recommendations"]):
    print(f"  [Rank {i+1}] {rec['label']}: {rec['station_name']} ({rec['distance_km']:.1f} km, {rec['effective_power_kw']} kW, ~{rec['estimated_charge_time_min']:.0f}m charge, Cost: Rs {rec['estimated_cost_rs']:.0f})")

print("\n=== 5. Testing AVA Voice Queries (Code-Mixed) ===")
queries = [
    "Nearest charging station find karo",
    "Gaadi chi battery kiti aahe",
    "What is the status of vehicle and fuel left"
]
for q in queries:
    r = requests.post("http://localhost:8000/api/voice/process", json={"text": q, "user_id": "driver_live_test"})
    data = r.json()
    print(f"  Q: \"{q}\"")
    print(f"  AVA: \"{data['text']}\"")
    print(f"  Action: {data['action_type']} | Needs Confirmation: {data['needs_confirmation']}\n")

print("=== 6. Testing AVA Two-Turn Reservation Confirmation Flow ===")
r_conf = requests.post("http://localhost:8000/api/voice/confirm", json={"user_id": "driver_live_test", "confirmed": True})
data_conf = r_conf.json()
print("  Confirm 'haan/yes' -> AVA:", data_conf["text"])
if "pin" in data_conf.get("display_data", {}):
    print("  Generated PIN:", data_conf["display_data"]["pin"])

print("\n=== 7. Testing Speech Config ===")
r = requests.get("http://localhost:8000/api/speech/config")
print("GET /api/speech/config:", r.status_code, r.json())

print("\n=== 8. Testing Frontend Pages ===")
for path in ["/", "/car", "/kiosk"]:
    r = requests.get(f"http://localhost:3000{path}")
    print(f"GET http://localhost:3000{path} -> {r.status_code} (HTML bytes: {len(r.content)})")
    assert r.status_code == 200

print("\n=== ALL LIVE APP TESTS PASSED SUCCESSFULLY! ===")
