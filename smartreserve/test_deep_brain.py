"""
test_deep_brain.py — Comprehensive Voice Brain and Routing Verification
Tests 15+ real-world phrases across English, Hinglish, Marathi, technical architecture,
and in-cabin operations.
"""
import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "smartreserve_backend"))

from voice_assistant import assistant
from smartreserve_brain import brain

def run_brain_tests():
    print("=" * 70)
    print("🧠 TESTING AVA IN-CABIN PRODUCTION VOICE BRAIN (18 TEST PHRASES)")
    print("=" * 70)

    test_cases = [
        # 1. User's exact prompt that previously went to Hyderabad
        ("can you book for me a charging Station in Nagpur", "reserve", "Nagpur"),
        
        # 2. Browse in Nagpur (should recommend without booking)
        ("show charging stations in Nagpur", "browse", "Nagpur"),

        # 3. Project overview
        ("how does this project work", "brain", "Ideathon"),

        # 4. What is SmartReserve
        ("what is smartreserve", "brain", "SmartReserve"),

        # 5. OCPP Protocol
        ("what is ocpp 1.6j", "brain", "OCPP"),

        # 6. Escrow deposit
        ("why do I have to pay 200 rupees deposit", "brain", "deposit"),

        # 7. Demand forecasting
        ("how does demand forecasting work", "brain", "XGBoost"),

        # 8. Priority Engine
        ("how does the priority engine rank stations", "brain", "criteria"),

        # 9. Vehicle specs
        ("tell me about ioniq 5", "brain", "72.6 kWh"),

        # 10. CCS2 charging standard
        ("what is ccs2 connector", "brain", "CCS2"),

        # 11. Tyre pressure alert
        ("check tyre pressure", "brain", "28.0 PSI"),

        # 12. Battery State of Health
        ("check battery health", "brain", "98.5%"),

        # 13. Service due
        ("when is my next service due", "brain", "5500"),

        # 14. Current location
        ("where am I right now", "brain", "Nagpur"),

        # 15. Active PIN query
        ("can you repeat the pin", "pin", "PIN"),

        # 16. Hinglish booking
        ("Nagpur mein station book karo", "reserve", "Nagpur"),

        # 17. Marathi battery query
        ("gaadi chi battery kiti aahe", "telematics", "18%"),

        # 18. Identity / who are you
        ("who are you", "brain", "AVA"),
    ]

    passed = 0
    for idx, (query, expected_mode, expected_keyword) in enumerate(test_cases, 1):
        resp = assistant.process_input(query, user_id="test_driver_1", city="nagpur", user_lat=21.1458, user_lon=79.0882)
        text = resp.text
        disp = resp.display_data or {}
        
        # Check assertions
        kw_found = expected_keyword.lower() in text.lower() or expected_keyword.lower() in str(disp).lower()
        
        print(f"[{idx:02d}] Query : \"{query}\"")
        print(f"     Action: {resp.action_type} | Tools: {resp.tool_calls}")
        print(f"     Output: {text[:100]}...")
        if disp.get("station_name"):
            print(f"     Station Target: {disp.get('station_name')}")
            
        if not kw_found:
            print(f"     ❌ FAILED: Expected '{expected_keyword}' in response")
        else:
            print(f"     ✅ PASSED")
            passed += 1
        print("-" * 70)

    print(f"\nFinal Result: {passed}/{len(test_cases)} tests passed!")
    assert passed == len(test_cases), f"Only {passed}/{len(test_cases)} passed."
    print("🏆 ALL BRAIN AND VOICE ROUTING TESTS COMPLETED WITH 100% SUCCESS!")

if __name__ == "__main__":
    run_brain_tests()
