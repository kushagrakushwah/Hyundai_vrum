"""
road_routing.py — Real-World Road Driving Distance & Route Engine for Hyundai SmartReserve.
Replaces straight-line (crow-flies) Haversine with actual navigable road network distances,
turn-by-turn driving durations, and route polyline geometries via OSRM with an urban circuity fallback.
"""

import json
import logging
import math
import urllib.request
import urllib.error
from typing import Dict, List, Tuple, Optional, Any

logger = logging.getLogger("smartreserve.routing")

# In-memory routing cache: (round_lat1, round_lon1, round_lat2, round_lon2) -> dict
_ROUTE_CACHE: Dict[Tuple[float, float, float, float], Dict[str, Any]] = {}
_MAX_CACHE_SIZE = 2000

def haversine_crow(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate straight-line great-circle distance in kilometers."""
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat / 2) ** 2 +
         math.sin(dLon / 2) ** 2 *
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)))
    return 6371.0 * 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, a))))

def get_road_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, Any]:
    """
    Query real road driving distance, drive time, and route geometry between two coordinates.
    Uses public Open Source Routing Machine (OSRM) driving profile with cached lookups
    and graceful fallback to Indian urban/highway circuity factors if offline.
    """
    # Round to 4 decimal places (~11 meters resolution) for cache hits
    key = (round(lat1, 4), round(lon1, 4), round(lat2, 4), round(lon2, 4))
    if key in _ROUTE_CACHE:
        return _ROUTE_CACHE[key]

    crow_km = haversine_crow(lat1, lon1, lat2, lon2)

    # If practically identical location (e.g. inside campus / same spot < 40 meters)
    if crow_km < 0.04:
        res = {
            "road_distance_km": 0.1,
            "drive_time_min": 0.5,
            "geometry": [[lon1, lat1], [lon2, lat2]],
            "source": "on_premise",
            "straight_line_km": round(crow_km, 3)
        }
        _ROUTE_CACHE[key] = res
        return res

    # 1. Try real-world OSRM driving route API
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HyundaiSmartReserve/2.0 (In-Cabin Routing)"})
        with urllib.request.urlopen(req, timeout=1.8) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                routes = data.get("routes", [])
                if routes and len(routes) > 0:
                    route = routes[0]
                    dist_km = round(route["distance"] / 1000.0, 2)
                    dur_min = round(route["duration"] / 60.0, 1)
                    coords = route.get("geometry", {}).get("coordinates", [])

                    res = {
                        "road_distance_km": max(0.1, dist_km),
                        "drive_time_min": max(0.5, dur_min),
                        "geometry": coords,
                        "source": "osrm_road_network",
                        "straight_line_km": round(crow_km, 2)
                    }
                    if len(_ROUTE_CACHE) >= _MAX_CACHE_SIZE:
                        _ROUTE_CACHE.clear()
                    _ROUTE_CACHE[key] = res
                    return res
    except Exception as e:
        logger.debug(f"[Routing] OSRM live call failed ({e}), using urban circuity fallback.")

    # 2. Fallback: High-precision Road Circuity Network Model (Urban / Suburban / Highway)
    # India urban streets have ~1.30x circuity ratio compared to straight-line Euclidean distance
    if crow_km <= 12.0:
        circuity = 1.30
        avg_speed_kmh = 28.0  # City traffic / signals
    elif crow_km <= 40.0:
        circuity = 1.25
        avg_speed_kmh = 45.0  # Arterial ring road / expressway
    else:
        circuity = 1.18
        avg_speed_kmh = 70.0  # National Highway

    road_dist_km = round(crow_km * circuity, 2)
    drive_time_min = round((road_dist_km / avg_speed_kmh) * 60.0, 1)

    # Synthesize intermediate waypoints for smooth map rendering
    steps = max(3, min(20, int(crow_km * 2)))
    coords = []
    for i in range(steps + 1):
        frac = i / float(steps)
        # Add slight natural curvature offset for realistic path
        curve_offset = math.sin(frac * math.pi) * 0.0012 if steps > 4 else 0.0
        coords.append([
            round(lon1 + (lon2 - lon1) * frac + curve_offset, 6),
            round(lat1 + (lat2 - lat1) * frac - curve_offset, 6)
        ])

    res = {
        "road_distance_km": max(0.1, road_dist_km),
        "drive_time_min": max(0.5, drive_time_min),
        "geometry": coords,
        "source": "road_circuity_network_model",
        "straight_line_km": round(crow_km, 2)
    }
    if len(_ROUTE_CACHE) >= _MAX_CACHE_SIZE:
        _ROUTE_CACHE.clear()
    _ROUTE_CACHE[key] = res
    return res

def get_road_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return real navigable road distance in kilometers."""
    return get_road_route(lat1, lon1, lat2, lon2)["road_distance_km"]

def get_road_drive_time(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return realistic road driving time in minutes."""
    return get_road_route(lat1, lon1, lat2, lon2)["drive_time_min"]
