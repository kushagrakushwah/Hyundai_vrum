import math
import random
from datetime import datetime, timedelta

# Deterministic simulation
random.seed(42)

# ══════════════════════════════════════════════════════════════════════════════
# 🚗 VEHICLE CATALOG — 5 HYUNDAI + 3 COMPETITOR CARS (THREE-THINGS PATTERN)
# ══════════════════════════════════════════════════════════════════════════════
VEHICLE_CATALOG = {
    # ── 1. Hyundai Ioniq 5 ──────────────────────────────────────────────────
    "ioniq5": {
        "model_id": "ioniq5",
        "name": "Hyundai Ioniq 5 Long Range AWD",
        "short_name": "Ioniq 5 AWD",
        "brand": "Hyundai",
        "category": "Premium E-GMP CUV",
        "is_hyundai": True,
        # Pattern 1: Energy & Range Architecture
        "energy": {
            "battery_capacity_kwh": 72.6,
            "usable_capacity_kwh": 70.0,
            "nominal_range_km": 481.0,
            "efficiency_km_per_kwh": 6.6,
            "consumption_wh_per_km": 151.5,
        },
        # Pattern 2: Charging & Connector Architecture
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 220.0,
            "max_ac_kw": 11.0,
            "architecture_voltage": "800V E-GMP",
            "dc_10_80_min": 18.0,
            "v2l_supported": True,
            "v2l_max_kw": 3.6,
        },
        # Pattern 3: Health & Diagnostics Telematics
        "diagnostics": {
            "tyre_pressure_nominal_psi": 36.0,
            "service_interval_km": 15000,
            "battery_soh": 98.5,
            "fast_charge_curve_type": "800v_flat_high",
        },
        "badge_color": "#00AAD4",
    },
    # ── 2. Hyundai Ioniq 6 ──────────────────────────────────────────────────
    "ioniq6": {
        "model_id": "ioniq6",
        "name": "Hyundai Ioniq 6 Streamliner AWD",
        "short_name": "Ioniq 6 AWD",
        "brand": "Hyundai",
        "category": "Aerodynamic E-GMP Sedan",
        "is_hyundai": True,
        "energy": {
            "battery_capacity_kwh": 77.4,
            "usable_capacity_kwh": 74.0,
            "nominal_range_km": 545.0,
            "efficiency_km_per_kwh": 7.0,
            "consumption_wh_per_km": 142.8,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 233.0,
            "max_ac_kw": 11.0,
            "architecture_voltage": "800V E-GMP",
            "dc_10_80_min": 18.0,
            "v2l_supported": True,
            "v2l_max_kw": 3.6,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 36.0,
            "service_interval_km": 15000,
            "battery_soh": 99.2,
            "fast_charge_curve_type": "800v_flat_high",
        },
        "badge_color": "#00D2BE",
    },
    # ── 3. Hyundai Kona Electric ────────────────────────────────────────────
    "kona_ev": {
        "model_id": "kona_ev",
        "name": "Hyundai Kona Electric",
        "short_name": "Kona Electric",
        "brand": "Hyundai",
        "category": "Urban Electric CUV",
        "is_hyundai": True,
        "energy": {
            "battery_capacity_kwh": 39.2,
            "usable_capacity_kwh": 37.5,
            "nominal_range_km": 305.0,
            "efficiency_km_per_kwh": 7.8,
            "consumption_wh_per_km": 128.2,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 50.0,
            "max_ac_kw": 7.2,
            "architecture_voltage": "400V Standard",
            "dc_10_80_min": 47.0,
            "v2l_supported": False,
            "v2l_max_kw": 0.0,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 36.0,
            "service_interval_km": 12000,
            "battery_soh": 97.8,
            "fast_charge_curve_type": "400v_stepped",
        },
        "badge_color": "#0099CC",
    },
    # ── 4. Hyundai Creta EV ─────────────────────────────────────────────────
    "creta_ev": {
        "model_id": "creta_ev",
        "name": "Hyundai Creta EV",
        "short_name": "Creta EV",
        "brand": "Hyundai",
        "category": "Midsize Electric SUV",
        "is_hyundai": True,
        "energy": {
            "battery_capacity_kwh": 45.0,
            "usable_capacity_kwh": 43.2,
            "nominal_range_km": 380.0,
            "efficiency_km_per_kwh": 8.4,
            "consumption_wh_per_km": 119.0,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 60.0,
            "max_ac_kw": 7.4,
            "architecture_voltage": "400V Standard",
            "dc_10_80_min": 45.0,
            "v2l_supported": False,
            "v2l_max_kw": 0.0,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 35.0,
            "service_interval_km": 15000,
            "battery_soh": 99.0,
            "fast_charge_curve_type": "400v_stepped",
        },
        "badge_color": "#0077B6",
    },
    # ── 5. Hyundai Inster EV ────────────────────────────────────────────────
    "inster_ev": {
        "model_id": "inster_ev",
        "name": "Hyundai Inster EV",
        "short_name": "Inster EV",
        "brand": "Hyundai",
        "category": "Compact Urban EV",
        "is_hyundai": True,
        "energy": {
            "battery_capacity_kwh": 42.0,
            "usable_capacity_kwh": 40.0,
            "nominal_range_km": 355.0,
            "efficiency_km_per_kwh": 8.5,
            "consumption_wh_per_km": 117.6,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 85.0,
            "max_ac_kw": 11.0,
            "architecture_voltage": "400V Standard",
            "dc_10_80_min": 30.0,
            "v2l_supported": True,
            "v2l_max_kw": 3.6,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 34.0,
            "service_interval_km": 15000,
            "battery_soh": 99.5,
            "fast_charge_curve_type": "400v_stepped",
        },
        "badge_color": "#00B4D8",
    },
    # ── 6. Tata Nexon EV Long Range (Competitor) ───────────────────────────
    "nexon_ev": {
        "model_id": "nexon_ev",
        "name": "Tata Nexon EV Long Range",
        "short_name": "Nexon EV LR",
        "brand": "Tata Motors",
        "category": "Competitor Electric SUV",
        "is_hyundai": False,
        "energy": {
            "battery_capacity_kwh": 40.5,
            "usable_capacity_kwh": 39.0,
            "nominal_range_km": 325.0,
            "efficiency_km_per_kwh": 8.0,
            "consumption_wh_per_km": 125.0,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 30.0,
            "max_ac_kw": 7.2,
            "architecture_voltage": "320V Ziptron",
            "dc_10_80_min": 56.0,
            "v2l_supported": False,
            "v2l_max_kw": 0.0,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 34.0,
            "service_interval_km": 10000,
            "battery_soh": 96.5,
            "fast_charge_curve_type": "budget_curved",
        },
        "badge_color": "#FF9900",
    },
    # ── 7. Mahindra XUV400 EV (Competitor) ─────────────────────────────────
    "xuv400": {
        "model_id": "xuv400",
        "name": "Mahindra XUV400 EV",
        "short_name": "Mahindra XUV400",
        "brand": "Mahindra",
        "category": "Competitor Electric SUV",
        "is_hyundai": False,
        "energy": {
            "battery_capacity_kwh": 39.4,
            "usable_capacity_kwh": 37.8,
            "nominal_range_km": 310.0,
            "efficiency_km_per_kwh": 7.9,
            "consumption_wh_per_km": 126.5,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 50.0,
            "max_ac_kw": 7.2,
            "architecture_voltage": "350V Standard",
            "dc_10_80_min": 50.0,
            "v2l_supported": False,
            "v2l_max_kw": 0.0,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 34.0,
            "service_interval_km": 10000,
            "battery_soh": 97.0,
            "fast_charge_curve_type": "400v_stepped",
        },
        "badge_color": "#E63946",
    },
    # ── 8. MG ZS EV Long Range (Competitor) ────────────────────────────────
    "mg_zs_ev": {
        "model_id": "mg_zs_ev",
        "name": "MG ZS EV Long Range",
        "short_name": "MG ZS EV",
        "brand": "MG Motor",
        "category": "Competitor Electric SUV",
        "is_hyundai": False,
        "energy": {
            "battery_capacity_kwh": 50.3,
            "usable_capacity_kwh": 48.0,
            "nominal_range_km": 360.0,
            "efficiency_km_per_kwh": 7.2,
            "consumption_wh_per_km": 138.8,
        },
        "charging": {
            "supported_connectors": ["CCS2", "AC Type 2"],
            "max_dc_kw": 50.0,
            "max_ac_kw": 7.4,
            "architecture_voltage": "400V Standard",
            "dc_10_80_min": 42.0,
            "v2l_supported": False,
            "v2l_max_kw": 0.0,
        },
        "diagnostics": {
            "tyre_pressure_nominal_psi": 35.0,
            "service_interval_km": 10000,
            "battery_soh": 97.5,
            "fast_charge_curve_type": "400v_stepped",
        },
        "badge_color": "#9D4EDD",
    },
}

class VehicleProfile:
    def __init__(self, model_id: str = "ioniq5"):
        self.load_model(model_id)

    def load_model(self, model_id: str):
        self.model_id = model_id if model_id in VEHICLE_CATALOG else "ioniq5"
        self.catalog_data = VEHICLE_CATALOG[self.model_id]

        self.name = self.catalog_data["name"]
        self.short_name = self.catalog_data["short_name"]
        self.brand = self.catalog_data["brand"]
        self.is_hyundai = self.catalog_data["is_hyundai"]

        # Three-things pattern unpacking:
        energy = self.catalog_data["energy"]
        self.battery_capacity_kwh = energy["battery_capacity_kwh"]
        self.usable_capacity_kwh = energy["usable_capacity_kwh"]
        self.range_at_100_percent_km = energy["nominal_range_km"]
        self.efficiency_km_per_kwh_nominal = energy["efficiency_km_per_kwh"]

        charging = self.catalog_data["charging"]
        self.connector_type = charging["supported_connectors"][0]  # Primary: CCS2
        self.supported_connectors = charging["supported_connectors"]
        self.max_dc_charge_rate_kw = charging["max_dc_kw"]
        self.max_ac_charge_rate_kw = charging["max_ac_kw"]
        self.architecture_voltage = charging["architecture_voltage"]
        self.v2l_supported = charging["v2l_supported"]
        self.v2l_output_kw = charging["v2l_max_kw"]

        diag = self.catalog_data["diagnostics"]
        self.tyre_pressure_nominal_psi = diag["tyre_pressure_nominal_psi"]
        self.service_interval_km = diag["service_interval_km"]
        self.service_interval_months = 12

class VehicleState:
    def __init__(self):
        self.soc = 18.0
        self.range_km = 86.58
        self.odometer_km = 24500.0
        self.speed_kmh = 45.0
        self.battery_temp_celsius = 25.0
        self.ambient_temp_celsius = 30.0
        self.tyre_pressures = {
            'front_left': 28.0,
            'front_right': 35.0,
            'rear_left': 36.0,
            'rear_right': 36.0
        }
        self.ac_on = True
        self.headlights_on = False
        self.seat_heating = False
        self.drive_mode = 'normal'
        self.last_service_km = 15000.0
        self.last_service_date = datetime.now() - timedelta(days=200)
        self.trailing_wh_per_km = 151.5  # ~6.6 km/kWh
        self.gps_lat = 21.1458  # Default: Nagpur, Maharashtra (User Location)
        self.gps_lng = 79.0882
        self.city = 'Nagpur'
        self.fuel_type = 'electric'
        self.cabin_temp_celsius = 22.0
        self.v2l_active = False

    def set_location(self, lat: float, lng: float, city: str = 'Nagpur'):
        self.gps_lat = float(lat)
        self.gps_lng = float(lng)
        self.city = city

class VehicleIntelligence:
    def __init__(self, default_model: str = "ioniq5"):
        self.profile = VehicleProfile(default_model)
        self.state = VehicleState()
        self._recalculate_range()

    def set_vehicle_model(self, model_id: str) -> dict:
        """Switch active vehicle model across the 5 Hyundai + 3 competitor cars."""
        if model_id not in VEHICLE_CATALOG:
            model_id = "ioniq5"
        self.profile.load_model(model_id)
        self._recalculate_range()
        return self.get_full_telematics_context()

    def _recalculate_range(self):
        """Update range based on active battery capacity and SoC."""
        energy_rem = (self.state.soc / 100.0) * self.profile.battery_capacity_kwh
        self.state.range_km = round(energy_rem * self.profile.efficiency_km_per_kwh_nominal, 1)
        self.state.trailing_wh_per_km = round(1000.0 / self.profile.efficiency_km_per_kwh_nominal, 1)

    def get_available_models(self) -> list:
        """Return all 8 vehicles in catalog with three-things pattern."""
        res = []
        for mid, data in VEHICLE_CATALOG.items():
            entry = dict(data)
            entry["is_active"] = (mid == self.profile.model_id)
            res.append(entry)
        return res

    def check_station_compatibility(self, station: dict) -> dict:
        """
        Evaluate compatibility between a charging station and the active vehicle profile.
        Determines connector match, effective charging rate (kW), and charging speed tier.
        """
        return check_station_compatibility(station, self.profile.catalog_data, current_soc=self.state.soc)

    def set_location(self, lat: float, lng: float, city: str = 'Nagpur'):
        self.state.set_location(lat, lng, city)
    
    def set_soc(self, soc: float):
        self.state.soc = soc
        self._recalculate_range()
        self.state.soc = soc

    def get_full_telematics_context(self):
        tpms_alerts = []
        for tyre, pressure in self.state.tyre_pressures.items():
            if pressure < 30:
                tpms_alerts.append(f"{tyre} is LOW ({pressure} PSI, should be {self.profile.tyre_pressure_nominal_psi} PSI)")
            elif pressure > 42:
                tpms_alerts.append(f"{tyre} is HIGH ({pressure} PSI, should be {self.profile.tyre_pressure_nominal_psi} PSI)")
        
        return {
            'vehicle': getattr(self.profile, 'name', 'Hyundai Ioniq 5 Long Range AWD'),
            'model_id': getattr(self.profile, 'model_id', 'ioniq5'),
            'soc': self.state.soc,
            'range_km': self.state.range_km,
            'battery_capacity_kwh': self.profile.battery_capacity_kwh,
            'battery_temp_celsius': self.state.battery_temp_celsius,
            'max_dc_kw': self.profile.max_dc_charge_rate_kw,
            'location': {'city': self.state.city, 'lat': self.state.gps_lat, 'lon': self.state.gps_lng, 'pin': '440001'},
            'tpms': {
                'fl': self.state.tyre_pressures['front_left'],
                'fr': self.state.tyre_pressures['front_right'],
                'rl': self.state.tyre_pressures['rear_left'],
                'rr': self.state.tyre_pressures['rear_right']
            },
            'tpms_alerts': tpms_alerts,
            'cabin_temp_celsius': self.state.cabin_temp_celsius,
            'drive_mode': self.state.drive_mode,
            'speed_kmh': self.state.speed_kmh,
            'odometer_km': self.state.odometer_km,
            'efficiency_km_per_kwh': self.profile.efficiency_km_per_kwh_nominal,
            'connector_type': self.profile.connector_type,
            'v2l_output_kw': 3.6 if self.state.v2l_active else 0.0,
            'health': {'battery': 98.5, 'motor': 99.0, 'brake_pads': 75.0}
        }

    def can_reach_destination(self, dest_name: str, dest_lat: float, dest_lng: float):
        try:
            from road_routing import get_road_distance
            distance_km = get_road_distance(self.state.gps_lat, self.state.gps_lng, dest_lat, dest_lng)
        except Exception:
            distance_straight = self.get_range_to_destination(dest_lat, dest_lng)
            distance_km = distance_straight * 1.25 # Road factor for destination

        energy_needed = distance_km / self.profile.efficiency_km_per_kwh_nominal
        soc_needed = (energy_needed / self.profile.battery_capacity_kwh) * 100
        
        arrival_soc = self.state.soc - soc_needed
        can_reach = arrival_soc > 10.0
        
        if can_reach:
            recommendation = f"You can reach {dest_name} and will arrive with approximately {arrival_soc:.1f}% battery."
        else:
            recommendation = f"You don't have enough battery to reach {dest_name}. Please charge before heading out."
            
        return can_reach, distance_km, arrival_soc, recommendation

    def get_vehicle_status(self):
        # Calculate battery percentage and range estimate
        soc = self.state.soc
        range_est = self.state.range_km
        
        # Hours of driving left at current consumption rate
        hours_left = 0
        if self.state.speed_kmh > 0:
            hours_left = range_est / self.state.speed_kmh
            
        # Average efficiency
        avg_efficiency = 1000 / self.state.trailing_wh_per_km if self.state.trailing_wh_per_km > 0 else 0
        
        # Tyre pressure status
        tyre_status = {}
        for tyre, pressure in self.state.tyre_pressures.items():
            if pressure < 30:
                tyre_status[tyre] = 'low'
            elif pressure > 42:
                tyre_status[tyre] = 'high'
            else:
                tyre_status[tyre] = 'ok'
                
        # Service status
        km_since_service = self.state.odometer_km - self.state.last_service_km
        km_to_next_service = self.profile.service_interval_km - km_since_service
        service_status = {
            'next_service_in_km': km_to_next_service,
            'is_overdue': km_to_next_service < 0
        }
        
        # Component health scores
        health_scores = {
            'battery_health': 98.5,
            'motor_health': 99.0,
            'brake_pad_life': 75.0,
            'coolant_level': 90.0,
            'wiper_fluid': 40.0
        }
        
        # Insights list
        insights = []
        insights.append(f"Battery at {soc:.1f}%, approximately {hours_left:.1f} hours of range remaining at current speed")
        for tyre, status in tyre_status.items():
            if status == 'low':
                insights.append(f"{tyre.replace('_', '-').capitalize()} tyre pressure is low ({self.state.tyre_pressures[tyre]} PSI), recommend inflating to {self.profile.tyre_pressure_nominal_psi} PSI")
        if service_status['is_overdue']:
            insights.append(f"Service is overdue by {-km_to_next_service} km")
        else:
            insights.append(f"Next service due in {km_to_next_service} km")
            
        return {
            'soc': round(soc, 1),
            'range_km': round(range_est, 1),
            'range_estimate_km': round(range_est, 1),
            'hours_remaining': round(hours_left, 1),
            'hours_of_driving_left': round(hours_left, 1),
            'efficiency': round(avg_efficiency, 2),
            'average_efficiency_km_kwh': round(avg_efficiency, 2),
            'tyres': tyre_status,
            'tyre_pressure_status': tyre_status,
            'service_status': service_status,
            'component_health': health_scores,
            'insights': insights,
            'latitude': self.state.gps_lat,
            'longitude': self.state.gps_lng,
            'city': self.state.city
        }

    def get_charge_curve(self, soc, battery_temp=None):
        if battery_temp is None:
            battery_temp = self.state.battery_temp_celsius
            
        # Simplistic Ioniq 5 charge curve simulation
        # Max 220 kW, sustained well until ~50%, then drops.
        base_kw = 0
        if soc < 50:
            base_kw = 220
        elif soc < 80:
            base_kw = 150 - (soc - 50) * 2  # drops from 150 to 90
        elif soc < 90:
            base_kw = 90 - (soc - 80) * 5   # drops from 90 to 40
        else:
            base_kw = max(10, 40 - (soc - 90) * 3) # drops slowly to 10
            
        # Temp penalty
        temp_factor = 1.0
        if battery_temp < 15:
            temp_factor = 0.5 + (battery_temp / 30) # Coldgating
        elif battery_temp > 40:
            temp_factor = 0.8 # Overheating protection
            
        return base_kw * temp_factor

    def get_range_to_destination(self, dest_lat, dest_lng):
        # Haversine formula
        R = 6371.0
        lat1 = math.radians(self.state.gps_lat)
        lon1 = math.radians(self.state.gps_lng)
        lat2 = math.radians(dest_lat)
        lon2 = math.radians(dest_lng)

        dlon = lon2 - lon1
        dlat = lat2 - lat1

        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        distance = R * c
        
        # Return distance with road factor
        return distance * 1.3

    def can_reach_station(self, station_lat, station_lng, min_arrival_soc=10.0):
        distance = self.get_range_to_destination(station_lat, station_lng)
        energy_needed = distance / (1000 / self.state.trailing_wh_per_km)
        soc_needed = (energy_needed / self.profile.battery_capacity_kwh) * 100
        
        arrival_soc = self.state.soc - soc_needed
        
        return arrival_soc >= min_arrival_soc, arrival_soc

    def get_diagnostic_summary(self):
        status = self.get_vehicle_status()
        summary_parts = []
        summary_parts.append(f"The vehicle is at {status['soc']}% battery with {status['range_estimate_km']:.0f} kilometers of range remaining.")
        
        tyre_issues = [tyre for tyre, stat in status['tyre_pressure_status'].items() if stat != 'ok']
        if tyre_issues:
            summary_parts.append(f"There is a tyre pressure issue: {', '.join(tyre_issues).replace('_', ' ')}.")
        else:
            summary_parts.append("All tyre pressures are optimal.")
            
        health_issues = [comp for comp, score in status['component_health'].items() if score < 50]
        if health_issues:
            summary_parts.append(f"Attention needed for: {', '.join(health_issues).replace('_', ' ')}.")
            
        if status['service_status']['is_overdue']:
            summary_parts.append("The vehicle is overdue for service.")
            
        return " ".join(summary_parts)

def check_station_compatibility(station: dict, profile: dict = None, current_soc: float = None) -> dict:
    """
    Evaluates physical connector, power speed matching, effective charging kW,
    and estimated charging time between a station and the active vehicle profile.
    """
    if profile is None:
        profile = vehicle.profile.catalog_data
    elif hasattr(profile, "catalog_data"):
        profile = profile.catalog_data
    if current_soc is None:
        current_soc = getattr(vehicle.state, 'soc', 18.0)

    st_conn = (station.get("connector_type") or "CCS2").strip()
    st_power = float(station.get("power_kw") or 50.0)
    supported_conns = profile["charging"]["supported_connectors"]
    max_dc = profile["charging"]["max_dc_kw"]
    max_ac = profile["charging"]["max_ac_kw"]
    batt_cap = profile["energy"]["battery_capacity_kwh"]

    st_conn_upper = st_conn.upper()
    is_ccs2 = "CCS" in st_conn_upper
    is_type2 = ("TYPE 2" in st_conn_upper or "TYPE-2" in st_conn_upper or 
                "TYPE-II" in st_conn_upper or "TYPE II" in st_conn_upper or 
                "AC TYPE" in st_conn_upper)
    is_bharat_dc = "BHARAT DC" in st_conn_upper or "GB/T" in st_conn_upper

    matched_conn = None
    is_dc = True
    if is_ccs2 and "CCS2" in supported_conns:
        matched_conn = "CCS2"
        is_dc = True
    elif is_type2 and "AC Type 2" in supported_conns:
        matched_conn = "AC Type 2"
        is_dc = False
    elif is_bharat_dc and "Bharat DC" in supported_conns:
        matched_conn = "Bharat DC"
        is_dc = True

    if not matched_conn:
        return {
            "is_compatible": False,
            "status": "INCOMPATIBLE",
            "tier": "INCOMPATIBLE",
            "tier_code": "INCOMPATIBLE",
            "tier_name": "❌ Incompatible Plug",
            "power_speed_tier": "Incompatible",
            "badge": "❌ Incompatible",
            "color": "#ef4444",
            "reason": f"Plug mismatch: Station uses {st_conn}, but {profile['short_name']} requires {', '.join(supported_conns)}.",
            "effective_kw": 0.0,
            "effective_charging_kw": 0.0,
            "est_charge_time_min": 0,
            "voltage_match": "Mismatch",
        }

    # Connector matches! Calculate effective charging power
    effective_kw = min(st_power, max_dc if is_dc else max_ac)
    needed_soc = max(0.0, 80.0 - current_soc)
    energy_kwh = (needed_soc / 100.0) * batt_cap
    chg_hours = energy_kwh / effective_kw if effective_kw > 0 else 1.0
    est_min = round(chg_hours * 60 + (3 if is_dc else 0))

    if is_dc:
        if st_power >= 120 and max_dc >= 120:
            status = "OPTIMAL_ULTRA_FAST"
            badge = f"⚡ Ultra-Fast ({effective_kw:.0f} kW)"
            color = "#00AAD4"
            reason = f"800V Ultra-Fast: delivers full {effective_kw:.0f} kW to your {profile['short_name']}."
            tier_speed = "Ultra-Fast (≥120 kW)"
        elif st_power > max_dc:
            status = "VEHICLE_LIMITED"
            badge = f"⚡ Car Capped ({effective_kw:.0f} kW)"
            color = "#FFD100"
            reason = f"Compatible: station outputs {st_power:.0f} kW, vehicle intake capped at {max_dc:.0f} kW by BMS."
            tier_speed = "Fast DC (50-100 kW)" if effective_kw >= 50 else "Standard DC (25-45 kW)"
        elif st_power < max_dc:
            status = "CHARGER_LIMITED"
            badge = f"⚠️ Charger Limited ({effective_kw:.0f} kW)"
            color = "#FFD100"
            reason = f"Compatible, but charger capped at {st_power:.0f} kW (car accepts up to {max_dc:.0f} kW)."
            tier_speed = "Standard DC (25-45 kW)" if effective_kw < 50 else "Fast DC (50-100 kW)"
        else:
            status = "MAX_VEHICLE_SPEED"
            badge = f"✅ Full Speed ({effective_kw:.0f} kW)"
            color = "#22c55e"
            reason = f"Charges at maximum {profile['short_name']} intake rate ({effective_kw:.0f} kW DC)."
            tier_speed = "Fast DC (50-100 kW)"
    else:
        status = "AC_DESTINATION"
        badge = f"🔌 AC Slow ({effective_kw:.1f} kW)"
        color = "#94a3b8"
        reason = f"AC Destination Charger: charges at {effective_kw:.1f} kW OBC limit."
        tier_speed = "AC Destination (3.3-22 kW)"

    return {
        "is_compatible": True,
        "status": status,
        "tier": status,
        "tier_code": status,
        "tier_name": badge,
        "power_speed_tier": tier_speed,
        "badge": badge,
        "color": color,
        "reason": reason,
        "effective_kw": effective_kw,
        "effective_charging_kw": effective_kw,
        "est_charge_time_min": est_min,
        "matched_connector": matched_conn,
    }

vehicle = VehicleIntelligence()
