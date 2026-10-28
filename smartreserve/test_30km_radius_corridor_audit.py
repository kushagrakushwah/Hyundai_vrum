"""
test_30km_radius_corridor_audit.py — Comprehensive Verification Suite
Tests:
1. 30 km radius geospatial delivery for current location & searched places
2. Distance bounds (strictly <= 30.0 km) and proximity sorting
3. Brand/operator query separation from geographic place geocoding
4. AVA voice corridor trip & booking resolution (Nagpur <-> Pune, Hyderabad <-> Nagpur)
5. Charging stop data structures (id & station_id)
6. Complete live data & connector telemetry architecture audit
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "smartreserve_backend"))

from stations_db import StationsDB, INDIAN_CITIES
from voice_assistant import assistant
from corridor_planner import plan_corridor_trip
from live_chargers_service import live_chargers


class Test30KmRadiusAndCorridorAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StationsDB()

    # ── 1. 30 km Radius Delivery for Current Location (Location A) ──
    def test_current_location_30km_radius(self):
        # Car at Nagpur Zero Mile
        car_lat, car_lng = 21.1458, 79.0882
        stations = self.db.get_stations_in_radius(car_lat, car_lng, radius_km=30.0)
        self.assertGreater(len(stations), 50, "Should find at least 50 stations within 30 km of Nagpur")
        for s in stations:
            d = s.get("distance_km")
            self.assertIsNotNone(d)
            self.assertLessEqual(d, 30.0, f"Station {s.get('name')} distance {d} exceeds 30 km")
        # Check sorted ascending by distance
        distances = [s["distance_km"] for s in stations]
        self.assertEqual(distances, sorted(distances), "Stations must be sorted by distance ascending")

    # ── 2. 30 km Radius Delivery for Searched Places ──
    def test_searched_places_resolution_and_30km_radius(self):
        test_queries = [
            ("hinjewadi", "Pune"),
            ("vnit", "Nagpur"),
            ("sitabuldi", "Nagpur"),
            ("gachibowli", "Hyderabad"),
            ("bandra", "Mumbai"),
            ("connaught place", "Delhi"),
            ("wardha", "Wardha"),
            ("amravati", "Amravati"),
            ("aurangabad", "Chhatrapati Sambhajinagar"),
            ("hydrabad", "Hyderabad"),
        ]

        for q, expected_region in test_queries:
            loc = self.db.resolve_location(q)
            self.assertIsNotNone(loc, f"Failed to resolve place '{q}'")
            self.assertTrue(loc["lat"] != 0 and loc["lng"] != 0, f"Invalid coords for '{q}'")
            stations = self.db.get_stations_in_radius(loc["lat"], loc["lng"], radius_km=30.0)
            self.assertGreater(len(stations), 0, f"No stations found within 30 km of '{q}' ({loc['name']})")
            for s in stations:
                self.assertLessEqual(s["distance_km"], 30.0, f"Station {s['name']} at {s['distance_km']}km exceeds 30km")

    # ── 3. Brand / Operator Keyword Separation ──
    def test_non_location_keywords_not_geocoded(self):
        # Typing a CPO operator or connector type should NOT resolve as a location
        operators = ["tata power", "statiq", "chargezone", "bpcl", "ccs2", "fast charger"]
        for op in operators:
            loc = self.db.resolve_location(op)
            self.assertIsNone(loc, f"Operator/keyword '{op}' should not resolve to a geographic coordinate")

    # ── 4. Corridor Voice Queries: Nagpur to Pune & Hyderabad to Nagpur ──
    def test_corridor_voice_nagpur_to_pune(self):
        # Query 1: Direct route query
        r1 = assistant.process_input("route from nagpur to pune")
        self.assertEqual(r1.action_type, "corridor_route")
        self.assertEqual(r1.display_data["origin"]["city"], "Nagpur")
        self.assertEqual(r1.display_data["destination"]["city"], "Pune")
        self.assertGreater(len(r1.display_data["charging_stops"]), 0)

        # Query 2: Direct booking request
        r2 = assistant.process_input("book charging stations in the route from nagpur to pune")
        self.assertEqual(r2.action_type, "corridor_route")
        self.assertEqual(r2.display_data["origin"]["city"], "Nagpur")
        self.assertEqual(r2.display_data["destination"]["city"], "Pune")
        self.assertTrue(r2.display_data.get("is_booked"))
        self.assertIsNotNone(r2.display_data.get("reservation_pin"))
        self.assertEqual(len(r2.display_data["reservation_pin"]), 6)
        self.assertIn("charging.reserve_slot", r2.tool_calls)

        # Query 3: With conversational prefix "ask ava to book"
        r3 = assistant.process_input("ask ava to book charging stations in the route from nagpur to pune")
        self.assertEqual(r3.action_type, "corridor_route")
        self.assertEqual(r3.display_data["origin"]["city"], "Nagpur")
        self.assertEqual(r3.display_data["destination"]["city"], "Pune")
        self.assertTrue(r3.display_data.get("is_booked"))
        self.assertIsNotNone(r3.display_data.get("reservation_pin"))

    def test_corridor_voice_hyderabad_to_nagpur(self):
        # Query 1: Typo alias "hydrabad to nagpur"
        r1 = assistant.process_input("hydrabad to nagpur")
        self.assertEqual(r1.action_type, "corridor_route")
        self.assertEqual(r1.display_data["origin"]["city"], "Hyderabad")
        self.assertEqual(r1.display_data["destination"]["city"], "Nagpur")

        # Query 2: Booking from Hyderabad to Nagpur
        r2 = assistant.process_input("book charging stations in the route from hydrabad to nagpur")
        self.assertEqual(r2.action_type, "corridor_route")
        self.assertEqual(r2.display_data["origin"]["city"], "Hyderabad")
        self.assertEqual(r2.display_data["destination"]["city"], "Nagpur")
        self.assertTrue(r2.display_data.get("is_booked"))
        self.assertIsNotNone(r2.display_data.get("reservation_pin"))

        # Query 3: "ask ava to book charging stations in the route from hydrabad to nagpur"
        r3 = assistant.process_input("ask ava to book charging stations in the route from hydrabad to nagpur")
        self.assertEqual(r3.action_type, "corridor_route")
        self.assertEqual(r3.display_data["origin"]["city"], "Hyderabad")
        self.assertEqual(r3.display_data["destination"]["city"], "Nagpur")
        self.assertTrue(r3.display_data.get("is_booked"))

    # ── 5. Corridor Charging Stop Data Structure ──
    def test_corridor_charging_stops_has_id_and_station_id(self):
        plan = plan_corridor_trip(
            origin_lat=21.1458, origin_lon=79.0882,
            dest_lat=18.5204, dest_lon=73.8567,
            origin_city="Nagpur", dest_city="Pune",
            current_soc=18.0
        )
        self.assertGreater(len(plan["charging_stops"]), 0)
        for stop in plan["charging_stops"]:
            self.assertIn("id", stop, "Stop must contain 'id' for frontend Leaflet bindings")
            self.assertIn("station_id", stop, "Stop must contain 'station_id'")
            self.assertEqual(stop["id"], stop["station_id"])
            self.assertTrue(stop["lat"] != 0 and stop["lng"] != 0)

    # ── 6. Live Telematics & Connector Data Verification ──
    def test_live_data_and_connector_telematics_architecture(self):
        # Verify Hyderabad live telematics service
        stations = live_chargers.get_hyderabad_live_stations()
        self.assertGreaterEqual(len(stations), 5)
        st = stations[0]
        self.assertIn("ports", st)
        self.assertGreater(len(st["ports"]), 0)
        # Port metrics
        port = st["ports"][0]
        self.assertIn("status", port)
        self.assertIn("rated_power_kw", port)
        # API guide availability
        guide = live_chargers.get_api_buying_and_iot_guide()
        self.assertIn("commercial_apis", guide)
        self.assertIn("low_cost_iot_sensor_blueprint", guide)


if __name__ == "__main__":
    unittest.main()
