"""
test_corridor_planner.py — Comprehensive tests for Highway Corridor Co-Pilot.
Tests interstate routing, milestone selection, SoC consumption physics,
voice responses (EN/HI/MR), and Leaflet payload format.
"""

import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'smartreserve_backend'))

from voice_assistant import VoiceAssistant
from corridor_planner import plan_corridor_trip

def test_corridor_suite():
    va = VoiceAssistant()
    print("=" * 70)
    print("🛣️ TESTING HIGHWAY CORRIDOR TRIP PLANNER & VOICE COPILOT")
    print("=" * 70)

    # 1. Exact User Query: "what will be ev stations i will get if i go to hydradabad now like on the way"
    q1 = "what will be ev stations i will get if i go to hydradabad now like on the way"
    res1 = va.process_input(q1, "test_user_1", city="Nagpur", user_lat=21.1255, user_lon=79.0524)
    print(f"\n[Test 1] Exact User Query: '{q1}'")
    print(f"  Action Type: {res1.action_type}")
    assert res1.action_type == "corridor_route", f"Expected corridor_route, got {res1.action_type}"
    disp1 = res1.display_data
    assert disp1.get("action") == "show_corridor_route"
    assert disp1.get("origin", {}).get("city") == "Nagpur"
    assert disp1.get("destination", {}).get("city") == "Hyderabad"
    assert disp1.get("total_distance_km", 0) > 400
    assert len(disp1.get("charging_stops", [])) >= 2
    assert len(disp1.get("route_geometry", [])) > 10
    print(f"  Route: {disp1['origin']['city']} -> {disp1['destination']['city']} ({disp1['total_distance_km']:.0f} km)")
    print(f"  Charging Stops: {len(disp1['charging_stops'])} DC fast hubs")
    for s in disp1['charging_stops']:
        print(f"    - Stop {s['stop_number']}: {s['name'][:40]}... @ {s['milestone_km']:.0f} km ({s['power_kw']:.0f} kW)")
    print(f"  Spoken Output: {res1.text[:100]}...")
    print("  ✅ TEST 1 PASSED")

    # 2. Hindi Query: "nagpur se hyderabad jaate waqt raste mein charging stations dikhao"
    q2 = "nagpur se hyderabad jaate waqt raste mein charging stations dikhao"
    res2 = va.process_input(q2, "test_user_2", city="Nagpur", user_lat=21.1255, user_lon=79.0524)
    print(f"\n[Test 2] Hindi Corridor Query: '{q2}'")
    assert res2.action_type == "corridor_route"
    assert "highway route plan kiya hai" in res2.text or "charging stop" in res2.text
    print(f"  Hindi Spoken: {res2.text[:120]}...")
    print("  ✅ TEST 2 PASSED")

    # 3. Mumbai to Pune Route
    q3 = "mumbai se pune highway chargers on the way"
    res3 = va.process_input(q3, "test_user_3", city="Mumbai", user_lat=19.0760, user_lon=72.8777)
    print(f"\n[Test 3] Mumbai to Pune: '{q3}'")
    assert res3.action_type == "corridor_route"
    disp3 = res3.display_data
    assert disp3.get("origin", {}).get("city") == "Mumbai"
    assert disp3.get("destination", {}).get("city") == "Pune"
    assert 120 < disp3.get("total_distance_km", 0) < 200
    print(f"  Route: {disp3['origin']['city']} -> {disp3['destination']['city']} ({disp3['total_distance_km']:.0f} km)")
    print(f"  Charging Stops: {len(disp3['charging_stops'])} stops")
    print("  ✅ TEST 3 PASSED")

    # 4. Delhi to Jaipur Route
    q4 = "delhi to jaipur highway chargers"
    res4 = va.process_input(q4, "test_user_4", city="Delhi", user_lat=28.6139, user_lon=77.2090)
    print(f"\n[Test 4] Delhi to Jaipur: '{q4}'")
    assert res4.action_type == "corridor_route"
    disp4 = res4.display_data
    assert disp4.get("origin", {}).get("city") == "Delhi"
    assert disp4.get("destination", {}).get("city") == "Jaipur"
    assert 240 < disp4.get("total_distance_km", 0) < 320
    print(f"  Route: {disp4['origin']['city']} -> {disp4['destination']['city']} ({disp4['total_distance_km']:.0f} km)")
    print("  ✅ TEST 4 PASSED")

    # 5. Full Battery direct reachable test (95% SoC on Mumbai -> Pune 145km)
    plan5 = plan_corridor_trip(19.0760, 72.8777, 18.5204, 73.8567, "Mumbai", "Pune", current_soc=95.0)
    print(f"\n[Test 5] Direct reachable test with 95% battery:")
    assert plan5["direct_reachable"] is True
    print(f"  Direct Reachable: {plan5['direct_reachable']} (Range: {plan5['current_range_km']:.0f} km > Distance: {plan5['total_distance_km']:.0f} km)")
    print("  ✅ TEST 5 PASSED")

    # 6. Bug 1 Regression Check: in-city search must not trigger corridor route
    q6 = "Hyderabad ke pass EV charging station batao"
    res6 = va.process_input(q6, "test_user_6")
    print(f"\n[Test 6] In-city search check: '{q6}'")
    assert res6.action_type != "corridor_route"
    assert res6.display_data.get("city") == "hyderabad"
    print(f"  Action: {res6.action_type}, City: {res6.display_data.get('city')}")
    print("  ✅ TEST 6 PASSED")

    print("\n" + "=" * 70)
    print("🏆 ALL 6 HIGHWAY CORRIDOR TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    test_corridor_suite()
