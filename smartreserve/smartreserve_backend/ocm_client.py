"""
Open Charge Map (OCM) API Client
Fetches real-time public EV charging station telemetry and operational status.
API Key: 1f545914-8daa-4fa6-9d7b-4a6819f2f7cc
"""
import json
import os
import time
import urllib.request
from typing import Dict, List, Optional

DEFAULT_API_KEY = "1f545914-8daa-4fa6-9d7b-4a6819f2f7cc"
OCM_BASE_URL = "https://api.openchargemap.io/v3/poi/"


class OpenChargeMapClient:
    def __init__(self, api_key: str = DEFAULT_API_KEY, cache_ttl_seconds: int = 300):
        self.api_key = api_key
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, dict] = {}

    def fetch_stations(
        self,
        country_code: str = "IN",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        distance_km: Optional[float] = None,
        max_results: int = 100,
    ) -> List[dict]:
        """
        Fetch real-time EV charging stations from Open Charge Map.
        Cached for cache_ttl_seconds.
        """
        cache_key = f"{country_code}_{latitude}_{longitude}_{distance_km}_{max_results}"
        now = time.time()

        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if now - cached_time < self.cache_ttl:
                return cached_data

        params = [
            ("output", "json"),
            ("maxresults", str(max_results)),
            ("key", self.api_key),
            ("compact", "true"),
            ("verbose", "false"),
        ]

        if country_code:
            params.append(("countrycode", country_code))
        if latitude is not None and longitude is not None:
            params.append(("latitude", str(latitude)))
            params.append(("longitude", str(longitude)))
            if distance_km:
                params.append(("distance", str(distance_km)))
                params.append(("distanceunit", "KM"))

        query_string = "&".join(f"{k}={v}" for k, v in params)
        url = f"{OCM_BASE_URL}?{query_string}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HyundaiSmartReserve/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                normalized = [self._normalize_station(item) for item in raw]
                self._cache[cache_key] = (normalized, now)
                return normalized
        except Exception as e:
            print(f"  [OCM] API Request failed: {e}")
            # If cache has old data, return it as fallback
            if cache_key in self._cache:
                return self._cache[cache_key][0]
            return []

    def _normalize_station(self, item: dict) -> dict:
        """Transform Open Charge Map POI schema to SmartReserve unified schema."""
        ai = item.get("AddressInfo") or {}
        st = item.get("StatusType") or {}
        op = item.get("OperatorInfo") or {}
        conns = item.get("Connections") or []

        # Calculate max power rating from all connections
        powers = [float(c.get("PowerKW") or 0) for c in conns if c.get("PowerKW")]
        max_power = max(powers) if powers else 60.0
        if max_power <= 0:
            max_power = 60.0  # sensible DC default

        # Extract connector types
        connector_types = [
            c.get("ConnectionType", {}).get("Title")
            for c in conns
            if c.get("ConnectionType") and c.get("ConnectionType", {}).get("Title")
        ]
        conn_str = ", ".join(connector_types[:2]) if connector_types else "CCS2 (DC Fast)"

        # Live operational status
        is_operational = st.get("IsOperational", True)
        status_title = st.get("Title", "Operational")

        sid = f"OCM-{item.get('ID')}"

        return {
            "station_id": sid,
            "ocm_id": item.get("ID"),
            "name": ai.get("Title") or f"EV Station {sid}",
            "district": ai.get("Town") or ai.get("StateOrProvince") or "Telangana",
            "state": ai.get("StateOrProvince") or "Telangana",
            "address": ai.get("AddressLine1") or f"{ai.get('Town', '')}, India",
            "lat": float(ai.get("Latitude") or 17.3850),
            "lng": float(ai.get("Longitude") or 78.4867),
            "power_kw": round(max_power, 1),
            "connector_type": conn_str,
            "total_ports": max(1, len(conns)),
            "operator": op.get("Title") or "Open Charge Network",
            "location_type": "Public Charging Station",
            "is_live_ocm": True,
            "live_status_title": status_title,
            "status": "AVAILABLE" if is_operational else "UNAVAILABLE",
            "monthly_units_kwh": 3500.0,
            "peak_load_kw": round(max_power * 0.75, 1),
        }


# Quick test routine
if __name__ == "__main__":
    client = OpenChargeMapClient()
    print("Testing OpenChargeMapClient...")
    stations = client.fetch_stations(country_code="IN", max_results=5)
    print(f"Retrieved {len(stations)} stations successfully!")
    for s in stations:
        print(f" - [{s['station_id']}] {s['name']} ({s['district']}) | {s['power_kw']}kW | Status: {s['status']}")
