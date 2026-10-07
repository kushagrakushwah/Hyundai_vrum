import math
import random
from datetime import datetime, timedelta

# Deterministic simulation
random.seed(42)

class VehicleProfile:
    def __init__(self):
        self.battery_capacity_kwh = 72.6
        self.max_dc_charge_rate_kw = 220
        self.connector_type = "CCS2"
        self.range_at_100_percent_km = 481
        self.efficiency_km_per_kwh_nominal = 6.6
        self.tyre_pressure_nominal_psi = 36
        self.service_interval_km = 15000
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
        self.gps_lat = 17.385
        self.gps_lng = 78.487
        self.fuel_type = 'electric'

class VehicleIntelligence:
    def __init__(self):
        self.profile = VehicleProfile()
        self.state = VehicleState()
    
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
            'insights': insights
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

vehicle = VehicleIntelligence()
