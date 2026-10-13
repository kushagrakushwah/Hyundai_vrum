import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'smartreserve/smartreserve_backend')
from vehicle_intelligence import vehicle, VEHICLE_CATALOG, check_station_compatibility
from live_chargers_service import live_chargers
from smartreserve_brain import brain

print("=== 1. Checking Vehicle Catalog (8 models) ===")
assert len(VEHICLE_CATALOG) == 8, f"Expected 8 models, got {len(VEHICLE_CATALOG)}"
for m_id, v in VEHICLE_CATALOG.items():
    brand = v.get('brand', 'Hyundai')
    print(f"Model: {m_id:18} | Brand: {brand:10} | Batt: {v['energy']['battery_capacity_kwh']:4.1f} kWh | Max DC: {v['charging']['max_dc_kw']:3.0f} kW | Range: {v['energy']['nominal_range_km']:3.0f} km")
    assert 'energy' in v and 'charging' in v and 'diagnostics' in v

print("\n=== 2. Testing Vehicle Switching ===")
prev = vehicle.profile.model_id
res = vehicle.set_vehicle_model('creta_ev')
print(f"Switched to: {res['vehicle']} | Max DC: {res['max_dc_kw']} kW")
assert vehicle.profile.model_id == 'creta_ev'
assert res['max_dc_kw'] == 60.0
vehicle.set_vehicle_model('ioniq5')
assert vehicle.profile.model_id == 'ioniq5'

print("\n=== 3. Testing 5-Tier Charger Compatibility Engine ===")
# Test 1: Incompatible Plug
gbt_station = {'connector_type': 'Bharat DC-001 (GB/T)', 'power_kw': 15}
c1 = check_station_compatibility(gbt_station, vehicle.profile)
print("Incompatible check:", c1['tier'], "| Reason:", c1['reason'])
assert c1['tier'] == 'INCOMPATIBLE' and not c1['is_compatible']

# Test 2: Ultra-Fast Optimal for Ioniq 5
uf_station = {'connector_type': 'CCS2', 'power_kw': 240}
c2 = check_station_compatibility(uf_station, vehicle.profile)
print("Ultra-fast check:", c2['tier'], "| Effective kW:", c2['effective_charging_kw'])
assert c2['tier'] == 'OPTIMAL_ULTRA_FAST' and c2['effective_charging_kw'] == 220

# Test 3: Vehicle limited for 30 kW Nexon EV on 150 kW station
vehicle.set_vehicle_model('nexon_ev')
c3 = check_station_compatibility(uf_station, vehicle.profile)
print("Nexon on 240kW:", c3['tier'], "| Effective kW:", c3['effective_charging_kw'])
assert c3['tier'] == 'VEHICLE_LIMITED' and c3['effective_charging_kw'] == 30.0
vehicle.set_vehicle_model('ioniq5')

print("\n=== 4. Testing Hyderabad Live Statiq Telematics ===")
hyd_stations = live_chargers.get_hyderabad_live_stations()
print(f"Loaded {len(hyd_stations)} live Hyderabad stations")
assert len(hyd_stations) >= 5
s0 = hyd_stations[0]
print(f"Station 1: {s0['name']} | Available: {s0['available_ports_count']}/{s0['total_ports_count']}")
for p in s0['ports']:
    print(f"  Port {p['port_number']}: {p['status']} | Car: {p.get('current_vehicle', '-')} | SoC: {p.get('current_soc', '-')}% | kW: {p.get('power_kw', '-')}")

print("\n=== 5. Testing IoT Blueprint & Buying Guide ===")
guide = live_chargers.get_api_buying_and_iot_guide()
assert 'commercial_apis' in guide and 'low_cost_iot_sensor_blueprint' in guide
print("Commercial APIs available:", len(guide['commercial_apis']))
print("Hardware cost:", guide['low_cost_iot_sensor_blueprint']['solution_1_charger_current_sensor']['total_hardware_cost_inr'])

print("\n=== 6. Testing Brain Voice Queries ===")
queries = [
    'switch car to creta ev',
    'which charger is compatible with my car',
    'hyderabad ke pass statiq charging stations batao live ports ke sath',
    'where can i buy real time ev data or how to build iot sensor'
]
for q in queries:
    txt, intent, ddata = brain.answer_query(q, language='en')
    print(f"Q: {q}")
    print(f"Intent: {intent} | Action: {ddata.get('action')}")
    print(f"Answer: {txt[:120]}...\n")
    assert ddata.get('action') is not None

print("🏆 ALL TARGETED VERIFICATIONS PASSED 100%!")
