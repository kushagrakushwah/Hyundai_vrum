"""
corridor_planner.py — Universal EV Highway Corridor Co-Pilot for Hyundai SmartReserve.
Plans real-world interstate/intercity EV highway trips between any two points in India.
Calculates road distance, drive time, highway polyline geometry, EV battery reachability,
and optimal DC fast-charging stops along the corridor.
"""

import math
import logging
from typing import Dict, List, Tuple, Optional, Any

from road_routing import get_road_route, haversine_crow
from stations_db import StationsDB

logger = logging.getLogger("smartreserve.corridor")

_STATIONS_DB: Optional[StationsDB] = None

def get_stations_db() -> StationsDB:
    global _STATIONS_DB
    if _STATIONS_DB is None:
        _STATIONS_DB = StationsDB()
    return _STATIONS_DB

# Vehicle technical constants (Hyundai Ioniq 5 E-GMP Platform)
BATTERY_CAPACITY_KWH = 72.6
EFFICIENCY_KM_PER_KWH = 6.6     # ~15.15 kWh / 100 km real-world highway driving
SAFE_HIGHWAY_LEG_KM = 260.0     # Maximum recommended highway leg between fast chargers at 80% SoC


def _point_to_line_segment_distance(
    p_lat: float, p_lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float
) -> Tuple[float, float]:
    """
    Calculate perpendicular distance (in km) from point P to line segment A-B,
    and the normalized progress ratio t in [0.0, 1.0] along the segment.
    """
    # Equirectangular projection in kilometers
    mid_lat_rad = math.radians((a_lat + b_lat) / 2.0)
    cos_lat = math.cos(mid_lat_rad)
    
    dx = (b_lon - a_lon) * 111.32 * cos_lat
    dy = (b_lat - a_lat) * 110.57
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 0.0001:
        # Segment is essentially a single point
        d_km = haversine_crow(p_lat, p_lon, a_lat, a_lon)
        return d_km, 0.0

    px = (p_lon - a_lon) * 111.32 * cos_lat
    py = (p_lat - a_lat) * 110.57

    # Parameter t of the projection on the segment
    t = max(0.0, min(1.0, (px * dx + py * dy) / seg_len_sq))

    proj_x = t * dx
    proj_y = t * dy
    dist_km = math.hypot(px - proj_x, py - proj_y)

    return dist_km, t


def plan_corridor_trip(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    origin_city: str = "Nagpur",
    dest_city: str = "Hyderabad",
    current_soc: float = 18.0,
    stations_db: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Plan a full EV highway trip between any origin and destination in India.
    Returns route metadata, downsampled polyline geometry for Leaflet,
    and planned charging stops along the highway corridor.
    """
    if stations_db is None:
        stations_db = get_stations_db()

    # 1. Fetch real highway driving route from OSRM / Indian road network model
    route_info = get_road_route(origin_lat, origin_lon, dest_lat, dest_lon)
    total_road_km = float(route_info.get("road_distance_km", 100.0))
    drive_time_min = float(route_info.get("drive_time_min", 120.0))
    full_geometry = route_info.get("geometry", [])  # [[lon, lat], ...]

    # Downsample geometry for lightweight JSON transmission and smooth Leaflet rendering
    max_pts = 160
    if len(full_geometry) > max_pts:
        step = max(1, len(full_geometry) // max_pts)
        sampled_geometry = full_geometry[::step]
        if full_geometry[-1] not in sampled_geometry:
            sampled_geometry.append(full_geometry[-1])
    else:
        sampled_geometry = full_geometry

    # 2. EV Battery Range Calculations
    usable_kwh = (max(5.0, min(100.0, current_soc)) / 100.0) * BATTERY_CAPACITY_KWH
    current_range_km = round(usable_kwh * EFFICIENCY_KM_PER_KWH, 1)

    # 3. Find Candidate Stations along the Highway Corridor
    # Bounding box filter with a 35 km buffer
    min_lat = min(origin_lat, dest_lat) - 0.35
    max_lat = max(origin_lat, dest_lat) + 0.35
    min_lon = min(origin_lon, dest_lon) - 0.35
    max_lon = max(origin_lon, dest_lon) + 0.35

    all_stations = {**stations_db.stations, **stations_db.live_ocm_stations} if hasattr(stations_db, "stations") else {}
    corridor_candidates: List[Dict[str, Any]] = []

    for sid, s in all_stations.items():
        st_lat = float(s.get("lat") or s.get("latitude") or 0.0)
        st_lon = float(s.get("lng") or s.get("longitude") or 0.0)
        if not (min_lat <= st_lat <= max_lat and min_lon <= st_lon <= max_lon):
            continue

        # Compute perpendicular distance to direct highway vector
        dist_from_hwy, t_progress = _point_to_line_segment_distance(
            st_lat, st_lon,
            origin_lat, origin_lon,
            dest_lat, dest_lon
        )

        # Include stations within 25 km of the highway corridor
        if dist_from_hwy <= 28.0:
            power_kw = float(s.get("power_kw") or s.get("rated_power_kw") or 15.0)
            corridor_km = round(t_progress * total_road_km, 1)
            corridor_candidates.append({
                "station_id": sid,
                "name": s.get("name") or "Highway EV Fast Hub",
                "operator": s.get("operator") or "CPO Network",
                "power_kw": power_kw,
                "lat": st_lat,
                "lng": st_lon,
                "address": s.get("address") or f"{s.get('city', '')}, Highway Corridor",
                "city": s.get("city") or s.get("district") or "",
                "corridor_progress": t_progress,
                "corridor_km": corridor_km,
                "detour_km": round(dist_from_hwy, 1)
            })

    # Sort candidate stations in order of route progression (from start to destination)
    corridor_candidates.sort(key=lambda x: x["corridor_progress"])

    # 4. Multi-Stage EV Charging Milestone Selection
    charging_stops: List[Dict[str, Any]] = []

    if total_road_km > current_range_km:
        # We need charging stops!
        # Step A: If starting with low/moderate battery (< 120 km range), select Stop 1
        # within safe distance of current location (e.g. 30 km to 75 km mark)
        target_milestones = []
        
        if current_range_km < 140.0 and total_road_km > 100.0:
            # Need an early top-up before deep highway
            first_milestone = max(25.0, min(70.0, current_range_km * 0.70))
            target_milestones.append((first_milestone, "Initial Highway Top-up"))
            
            # Remaining journey after Stop 1 (charged to ~80% = ~380 km range)
            remaining_km = total_road_km - first_milestone
            legs = max(1, math.ceil((remaining_km - 150.0) / SAFE_HIGHWAY_LEG_KM))
            for i in range(1, legs + 1):
                m_km = first_milestone + (i * SAFE_HIGHWAY_LEG_KM)
                if m_km < total_road_km - 40.0:
                    target_milestones.append((m_km, "Corridor Fast Charge"))
        else:
            # Starting with good battery
            legs = max(1, math.ceil((total_road_km - current_range_km) / SAFE_HIGHWAY_LEG_KM))
            spacing = (total_road_km - 50.0) / (legs + 1)
            for i in range(1, legs + 1):
                target_milestones.append((i * spacing, "Corridor Fast Charge"))

        # Match each target milestone to the best fast charger in that zone
        chosen_ids = set()
        for idx, (target_km, purpose) in enumerate(target_milestones, 1):
            # Find candidate chargers near this milestone (within ±60 km)
            zone_candidates = [
                c for c in corridor_candidates
                if abs(c["corridor_km"] - target_km) <= 75.0 and c["station_id"] not in chosen_ids
            ]
            if not zone_candidates:
                # Fallback to closest overall candidate along route
                zone_candidates = [c for c in corridor_candidates if c["station_id"] not in chosen_ids]

            if zone_candidates:
                # Rank candidates by: Power (higher is better) and Detour (lower is better)
                best_stop = max(
                    zone_candidates,
                    key=lambda c: (
                        min(c["power_kw"], 120.0) * 1.5 -
                        (c["detour_km"] * 2.0) -
                        abs(c["corridor_km"] - target_km) * 0.5
                    )
                )
                chosen_ids.add(best_stop["station_id"])
                
                # Estimated charge duration to 80%
                kw = max(20.0, best_stop["power_kw"])
                charge_mins = round((45.0 / kw) * 60.0, 0)
                charge_mins = max(18.0, min(55.0, charge_mins))

                charging_stops.append({
                    "stop_number": idx,
                    "id": best_stop["station_id"],
                    "station_id": best_stop["station_id"],
                    "name": best_stop["name"],
                    "operator": best_stop["operator"],
                    "milestone_km": round(best_stop["corridor_km"], 0),
                    "power_kw": best_stop["power_kw"],
                    "detour_km": best_stop["detour_km"],
                    "charge_time_min": int(charge_mins),
                    "city": best_stop["city"] or dest_city,
                    "address": best_stop["address"],
                    "lat": best_stop["lat"],
                    "lng": best_stop["lng"],
                    "purpose": purpose,
                    "target_soc": 80
                })

    # Destination stations (top 3 in destination city for arrival)
    norm_dest = dest_city.lower().strip()
    dest_stations = [
        c for c in corridor_candidates
        if norm_dest in c.get("city", "").lower() or norm_dest in c.get("address", "").lower()
    ]
    if not dest_stations:
        dest_stations = corridor_candidates[-3:] if len(corridor_candidates) >= 3 else corridor_candidates

    return {
        "action": "show_corridor_route",
        "route_name": f"{origin_city.capitalize()} to {dest_city.capitalize()}",
        "total_distance_km": total_road_km,
        "drive_time_hours": round(drive_time_min / 60.0, 1),
        "drive_time_min": round(drive_time_min, 0),
        "origin": {
            "city": origin_city.capitalize(),
            "lat": origin_lat,
            "lng": origin_lon
        },
        "destination": {
            "city": dest_city.capitalize(),
            "lat": dest_lat,
            "lng": dest_lon
        },
        "current_soc": current_soc,
        "current_range_km": current_range_km,
        "direct_reachable": total_road_km <= current_range_km,
        "stops_needed": len(charging_stops),
        "charging_stops": charging_stops,
        "route_geometry": sampled_geometry,
        "destination_stations": dest_stations[:3]
    }
