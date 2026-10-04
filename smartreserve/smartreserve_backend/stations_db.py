"""
Stations Database — loads real Telangana EV station data
In production, reads from nodes_master.csv (934 real BEE stations)
For demo, generates realistic synthetic data matching the real dataset shape
"""
import csv
import json
import os
import random
from typing import List, Optional, Dict

# Telangana districts with approximate coordinates
TELANGANA_DISTRICTS = [
    ("Hyderabad", 17.3850, 78.4867),
    ("Rangareddy", 17.2403, 78.1483),
    ("Medchal-Malkajgiri", 17.6099, 78.4820),
    ("Sangareddy", 17.6247, 78.0846),
    ("Nizamabad", 18.6725, 78.0941),
    ("Karimnagar", 18.4386, 79.1288),
    ("Warangal Urban", 17.9784, 79.5941),
    ("Nalgonda", 17.0575, 79.2671),
    ("Khammam", 17.2473, 80.1514),
    ("Mahbubnagar", 16.7449, 77.9852),
    ("Adilabad", 19.6640, 78.5320),
    ("Suryapet", 17.1441, 79.6223),
    ("Vikarabad", 17.3363, 77.9042),
    ("Yadadri Bhuvanagiri", 17.5779, 78.9245),
    ("Siddipet", 18.1018, 78.8520),
    ("Jangaon", 17.7264, 79.1520),
    ("Mancherial", 18.8734, 79.4510),
    ("Nirmal", 19.0963, 78.3440),
    ("Peddapalli", 18.6115, 79.3628),
    ("Jayashankar Bhupalpally", 18.4392, 80.2210),
    ("Bhadradri Kothagudem", 17.5523, 80.6200),
    ("Mulugu", 18.1944, 80.0684),
    ("Nagarkurnool", 16.4804, 78.3241),
    ("Wanaparthy", 16.3657, 78.0568),
    ("Gadwal", 16.2261, 77.8006),
    ("Narayanpet", 16.7449, 77.4952),
    ("Mahabubabad", 17.5990, 80.0010),
    ("Bhoopalapally", 18.0982, 80.1520),
    ("Rajanna Sircilla", 18.3869, 78.8230),
    ("Medak", 18.0467, 78.2620),
    ("Kamareddy", 18.3202, 78.3330),
    ("Asifabad", 19.3576, 79.2760),
    ("Hanamkonda", 18.0035, 79.5756),
]

CONNECTOR_TYPES = ["CCS2", "CHAdeMO", "AC Type 2", "CCS2+CHAdeMO"]
POWER_RATINGS = [7.2, 22.0, 50.0, 60.0, 120.0, 150.0]
STATION_NAMES_PREFIXES = [
    "Tata Power", "ChargeZone", "Statiq", "BPCL EV", "Zeon Charging",
    "Chargegrid", "Volttic", "ElectricPe", "Exicom", "EESL",
]
LOCATION_TYPES = [
    "Shopping Mall", "Petrol Pump", "Hotel", "Corporate Park",
    "Hospital Campus", "IT Park", "Metro Station", "Highway Plaza",
    "Dealership", "Government Office",
]


class StationsDB:
    def __init__(self):
        self.stations: Dict[str, dict] = {}
        self._load_or_generate()

    def _load_or_generate(self):
        """Try to load from CSV, else generate synthetic data."""
        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "demo_data", "nodes_master.csv"
        )
        if os.path.exists(csv_path):
            self._load_from_csv(csv_path)
        else:
            self._generate_synthetic()

    def _load_from_csv(self, path: str):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row.get("station_id", row.get("id", ""))
                if sid:
                    self.stations[sid] = {
                        "station_id": sid,
                        "name": row.get("name", "EV Station"),
                        "district": row.get("district", "Hyderabad"),
                        "address": row.get("address", ""),
                        "lat": float(row.get("lat", row.get("latitude", 17.38))),
                        "lng": float(row.get("lng", row.get("longitude", 78.48))),
                        "power_kw": float(row.get("power_kw", 60)),
                        "connector_type": row.get("connector_type", "CCS2"),
                        "total_ports": int(row.get("total_ports", 2)),
                        "operator": row.get("operator", "Tata Power"),
                        "location_type": row.get("location_type", "Public"),
                    }

    def _generate_synthetic(self):
        """Generate 934 synthetic stations matching real Telangana geography."""
        random.seed(42)
        target = 934
        stations_per_district = {}

        # Weight districts by population/EV density
        weights = [0.20, 0.15, 0.12, 0.05] + [0.02] * (len(TELANGANA_DISTRICTS) - 4)
        total_w = sum(weights)
        weights = [w / total_w for w in weights]

        for i in range(target):
            dist_idx = random.choices(range(len(TELANGANA_DISTRICTS)), weights=weights)[0]
            district_name, base_lat, base_lng = TELANGANA_DISTRICTS[dist_idx]

            # Scatter around district center
            lat = base_lat + random.uniform(-0.4, 0.4)
            lng = base_lng + random.uniform(-0.4, 0.4)

            power_kw = random.choices(
                POWER_RATINGS, weights=[5, 15, 20, 30, 20, 10]
            )[0]
            connector = random.choice(CONNECTOR_TYPES)
            operator = random.choice(STATION_NAMES_PREFIXES)
            loc_type = random.choice(LOCATION_TYPES)
            ports = random.choices([1, 2, 4], weights=[30, 50, 20])[0]

            sid = f"TG{i+1:04d}"
            self.stations[sid] = {
                "station_id": sid,
                "name": f"{operator} {loc_type} — {district_name}",
                "district": district_name,
                "address": f"{loc_type}, {district_name}, Telangana",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "power_kw": power_kw,
                "connector_type": connector,
                "total_ports": ports,
                "operator": operator,
                "location_type": loc_type,
                "monthly_units_kwh": round(random.uniform(500, 8000), 1),
                "peak_load_kw": round(power_kw * random.uniform(0.6, 0.95), 1),
            }

    def get_stations(self, district: Optional[str] = None, limit: int = 200) -> List[dict]:
        stations = list(self.stations.values())
        if district:
            stations = [s for s in stations if district.lower() in s["district"].lower()]
        return stations[:limit]

    def get_station(self, station_id: str) -> Optional[dict]:
        return self.stations.get(station_id)

    def save_to_csv(self, path: str):
        if not self.stations:
            return
        keys = list(next(iter(self.stations.values())).keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.stations.values())


if __name__ == "__main__":
    db = StationsDB()
    print(f"Loaded {len(db.stations)} stations")
    db.save_to_csv("../demo_data/nodes_master.csv")
    print("Saved to demo_data/nodes_master.csv")
