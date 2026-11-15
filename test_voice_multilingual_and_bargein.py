import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "smartreserve", "smartreserve_backend"))

from voice_assistant import VoiceAssistant
from smartreserve_brain import SmartReserveBrain

class TestVoiceMultilingualAndBargeIn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.va = VoiceAssistant()
        cls.brain = SmartReserveBrain()

    def test_01_nearest_charges_station_not_hijacked_by_location(self):
        """Image 2 & 3 bug: Ensure 'tell me the nearest charges station to my location' routes to FIND_CPO, NOT LOCATION_QUERY."""
        intent_res = self.va._parse_intent("tell me the nearest charges station to my location")
        self.assertEqual(intent_res.intent, "FIND_CPO")
        self.assertNotEqual(intent_res.intent, "LOCATION_QUERY")

        # Full end-to-end process_input test
        resp = self.va.process_input("tell me the nearest charges station to my location", user_lat=21.1458, user_lon=79.0882)
        self.assertIn("Tata Power", resp.text)
        self.assertIn(resp.action_type, ["confirmation_needed", "info"])
        self.assertIn("priority_engine.recommend", resp.tool_calls)
        self.assertIsNotNone(self.va._session_memory.get("hyundai_driver_001", {}).get("last_station"))

    def test_02_pure_location_query_still_works(self):
        """Ensure 'where am i' and 'my location' still return vehicle location without charger interference."""
        intent_res = self.va._parse_intent("where am i")
        self.assertEqual(intent_res.intent, "LOCATION_QUERY")

        intent_res2 = self.va._parse_intent("what is my current location")
        self.assertEqual(intent_res2.intent, "LOCATION_QUERY")

        brain_ans = self.brain.answer_query("where am i", vehicle_status={"location": {"city": "Nagpur", "lat": 21.1458, "lon": 79.0882}, "range_km": 348})
        self.assertIsNotNone(brain_ans)
        self.assertEqual(brain_ans[1], "LOCATION_INFO")
        self.assertIn("Nagpur", brain_ans[0])

    def test_03_tell_me_the_nearest_one(self):
        """User asks 'tell me the nearest one' or 'closest one'."""
        intent_res = self.va._parse_intent("tell me the nearest one")
        self.assertEqual(intent_res.intent, "FIND_CPO")

        resp = self.va.process_input("tell me the nearest one", user_lat=21.1458, user_lon=79.0882)
        self.assertIn("Tata Power", resp.text)

    def test_04_followup_book_this(self):
        """User asks for nearest station, then says 'book this' -> immediately reserves last recommended station."""
        user_id = "test_driver_unit"
        # 1. Driver asks for station
        r1 = self.va.process_input("tell me the nearest charges station to my location", user_id=user_id, user_lat=21.1458, user_lon=79.0882)
        last_st = self.va._session_memory.get(user_id, {}).get("last_station")
        self.assertIsNotNone(last_st)
        expected_sid = last_st.get("station_id") or last_st.get("id")

        # 2. Driver says "book this"
        r2 = self.va.process_input("book this", user_id=user_id, user_lat=21.1458, user_lon=79.0882)
        self.assertEqual(r2.action_type, "action_complete")
        self.assertEqual(r2.display_data.get("action"), "reserve")
        self.assertEqual(r2.display_data.get("station_id"), expected_sid)
        self.assertTrue("slot" in r2.text.lower() and "lock" in r2.text.lower())

    def test_05_trilingual_queries_and_reservation(self):
        """Verify trilingual support in English, Hindi, and Marathi."""
        # Hindi search & book
        r_hi = self.va.process_input("mujhe sabse paas ka charging station batao", user_id="hi_driver", user_lat=21.1458, user_lon=79.0882, language="hi-IN")
        self.assertTrue(any(word in r_hi.text.lower() for word in ["tata", "power", "sitabuldi", "station", "charger"]))
        r_hi_book = self.va.process_input("yeh wala book karo", user_id="hi_driver", user_lat=21.1458, user_lon=79.0882, language="hi-IN")
        self.assertEqual(r_hi_book.action_type, "action_complete")
        self.assertIn("slot lock", r_hi_book.text.lower())

        # Marathi search & book
        r_mr = self.va.process_input("majhyapasun sarvat javalcha charging station konta aahe sanga", user_id="mr_driver", user_lat=21.1458, user_lon=79.0882, language="mr-IN")
        self.assertTrue(any(word in r_mr.text.lower() for word in ["tata", "power", "sitabuldi", "station", "charger"]))
        r_mr_book = self.va.process_input("he reserve kara", user_id="mr_driver", user_lat=21.1458, user_lon=79.0882, language="mr-IN")
        self.assertEqual(r_mr_book.action_type, "action_complete")
        self.assertIn("book kela", r_mr_book.text.lower())

    def test_06_tariff_and_connectors_followups(self):
        """Verify follow-up queries for tariff and connectors."""
        # Tariff
        r_tariff = self.va.process_input("what is the tariff?", user_lat=21.1458, user_lon=79.0882)
        self.assertTrue("tariff" in r_tariff.text.lower() or "cost" in r_tariff.text.lower() or "rs." in r_tariff.text.lower())

        # Connectors
        r_conn = self.va.process_input("what connectors does it have?", user_lat=21.1458, user_lon=79.0882)
        self.assertTrue("ccs2" in r_conn.text.lower() or "connector" in r_conn.text.lower())

if __name__ == "__main__":
    unittest.main()
