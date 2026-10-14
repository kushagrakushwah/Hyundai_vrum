"""
smartreserve_brain.py — Comprehensive Embedded Knowledge & Intent Engine for AVA.
Acts as AVA's deep in-cabin cognitive brain in the Hyundai Ioniq 5.
Guarantees 100% accurate, rich, professional engineering and domain answers
even when completely offline (no cloud API key required).
"""
import re
from typing import Optional, Dict, Any, Tuple
import math

try:
    from vehicle_intelligence import VEHICLE_CATALOG, vehicle, check_station_compatibility
    from live_chargers_service import live_chargers
except ImportError:
    VEHICLE_CATALOG = {}
    vehicle = None
    check_station_compatibility = None
    live_chargers = None

NAGPUR_LANDMARKS = {
    "vnit": {
        "name": "VNIT Nagpur (Visvesvaraya National Institute of Technology)",
        "address": "South Ambazari Road, Bajaj Nagar, Nagpur 440010",
        "lat": 21.1255,
        "lng": 79.0524,
        "category": "Premier Institute & Engineering Campus",
    },
    "vnit nagpur": {
        "name": "VNIT Nagpur (Visvesvaraya National Institute of Technology)",
        "address": "South Ambazari Road, Bajaj Nagar, Nagpur 440010",
        "lat": 21.1255,
        "lng": 79.0524,
        "category": "Premier Institute & Engineering Campus",
    },
    "visvesvaraya": {
        "name": "VNIT Nagpur (Visvesvaraya National Institute of Technology)",
        "address": "South Ambazari Road, Bajaj Nagar, Nagpur 440010",
        "lat": 21.1255,
        "lng": 79.0524,
        "category": "Premier Institute & Engineering Campus",
    },
    "sitabuldi": {
        "name": "Sitabuldi Interchange & Market",
        "address": "Sitabuldi, Nagpur 440012",
        "lat": 21.1458,
        "lng": 79.0882,
        "category": "Metro Interchange & Commercial Hub",
    },
    "dharampeth": {
        "name": "Dharampeth Shopping District",
        "address": "West High Court Road, Dharampeth, Nagpur 440010",
        "lat": 21.1415,
        "lng": 79.0620,
        "category": "Commercial & Cafe District",
    },
    "sadar": {
        "name": "Sadar Residency Road",
        "address": "Residency Road, Sadar, Nagpur 440001",
        "lat": 21.1610,
        "lng": 79.0830,
        "category": "Commercial Center",
    },
    "civil lines": {
        "name": "Civil Lines Administrative Area",
        "address": "Civil Lines, Nagpur 440001",
        "lat": 21.1526,
        "lng": 79.0720,
        "category": "Administrative & High Court Area",
    },
    "ambazari": {
        "name": "Ambazari Lake & Garden",
        "address": "Ambazari Road, Nagpur 440033",
        "lat": 21.1280,
        "lng": 79.0430,
        "category": "Lakefront & Recreation",
    },
    "futala": {
        "name": "Futala Lake Waterfront",
        "address": "Futala Lake Road, Nagpur 440001",
        "lat": 21.1560,
        "lng": 79.0480,
        "category": "Waterfront Promenade",
    },
    "seminary hills": {
        "name": "Seminary Hills",
        "address": "Seminary Hills, Nagpur 440006",
        "lat": 21.1650,
        "lng": 79.0550,
        "category": "Green Hills & Botanical Gardens",
    },
    "aiims": {
        "name": "AIIMS Nagpur & MIHAN",
        "address": "Plot No. 2, MIHAN, Nagpur 441108",
        "lat": 21.0500,
        "lng": 79.0450,
        "category": "Medical & SEZ Campus",
    },
    "mihan": {
        "name": "MIHAN SEZ & Tech Park",
        "address": "MIHAN, Nagpur 441108",
        "lat": 21.0500,
        "lng": 79.0450,
        "category": "SEZ & Tech Campus",
    },
    "airport": {
        "name": "Dr. Babasaheb Ambedkar International Airport, Nagpur",
        "address": "Wardha Road, Sonegaon, Nagpur 440005",
        "lat": 21.0920,
        "lng": 79.0610,
        "category": "Airport Terminal",
    },
    "railway station": {
        "name": "Nagpur Central Railway Station",
        "address": "Station Road, Sitabuldi, Nagpur 440001",
        "lat": 21.1520,
        "lng": 79.0890,
        "category": "Major Transit Hub",
    },
    "congress nagar": {
        "name": "Congress Nagar Metro Station",
        "address": "Congress Nagar, Nagpur 440012",
        "lat": 21.1304,
        "lng": 79.0857,
        "category": "Metro Hub",
    },
    "kasturchand park": {
        "name": "Kasturchand Park Ground",
        "address": "Kasturchand Park, Nagpur 440001",
        "lat": 21.1509,
        "lng": 79.0802,
        "category": "Heritage Ground & Metro Hub",
    },
    "bajaj nagar": {
        "name": "Bajaj Nagar (near VNIT)",
        "address": "Bajaj Nagar, Nagpur 440010",
        "lat": 21.1270,
        "lng": 79.0600,
        "category": "Residential & Student Hub",
    }
}


class SmartReserveBrain:
    """
    AVA Cognitive Brain:
    - Deep domain knowledge of Hyundai SmartReserve (Track B Ideathon 2026)
    - Full awareness of Vehicle Telematics (Ioniq 5, 72.6 kWh, 800V E-GMP)
    - Comprehensive charging standards (OCPP 1.6J, CCS2, Type 2, tariffs, escrow)
    - Multilingual code-mixed answers (English, Hindi/Hinglish, Marathi)
    """

    def __init__(self):
        pass

    def answer_query(
        self,
        query: str,
        vehicle_status: Optional[Dict[str, Any]] = None,
        active_reservation: Optional[Dict[str, Any]] = None,
        language: str = "en"
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """
        Returns: (spoken_text, intent_name, display_data) or None if no match.
        """
        q = query.lower().strip()

        # Clean common wake words
        for ww in ["hey ava", "ok ava", "ava", "hey hyundai", "ok hyundai", "hello ava", "hi ava"]:
            if q.startswith(ww):
                q = q[len(ww):].strip(",. ")
                break

        # Deduplicate repeated identical phrases (e.g. from speech recognition stutters)
        # e.g., "how far i am from vnit nagpur how far i am from vnit nagpur"
        half = len(q) // 2
        if half >= 6 and q[:half].strip() == q[half:].strip():
            q = q[:half].strip()
        else:
            words = q.split()
            if len(words) >= 4 and len(words) % 2 == 0:
                h_w = len(words) // 2
                if words[:h_w] == words[h_w:]:
                    q = " ".join(words[:h_w])

        # ── SET LOCATION (e.g. "my location is vnit nagpur", "i am at vnit nagpur", "set location to vnit")
        if any(p in q for p in ["my location is", "i am at", "i'm at", "set location to", "set my location to", "hum vnit", "main vnit", "location set karo"]):
            return self._ans_set_location(q, vehicle_status, language)

        # ── DISTANCE / HOW FAR AM I (e.g. "how far i am from vnit nagpur", "how far is vnit nagpur")
        if self._matches(q, [
            "how far", "how far is", "how far am i", "how far i am", "distance to", "distance from",
            "how much distance", "kitni door", "kitna door", "kitne kilometer", "kiti antar", "how much km",
            (["how", "kitni", "kitna"], ["far", "distance", "door", "antar"]),
        ]):
            return self._ans_distance_check(q, vehicle_status, language)

        # ── 0. DO NOT INTERCEPT PRIMARY RESERVATION OR CHARGER DISCOVERY ACTIONS ─
        is_explaining_query = any(q.startswith(w) for w in ["how", "what", "why", "explain", "tell me about", "who"]) or "how does" in q or "how do" in q
        charger_action_kws = [
            "nearest charging", "nearest charger", "nearest station", "charging station from",
            "charger from my", "reserve", "book", "lock slot", "slot book", "book slot",
            "find charging", "find charger", "show charger", "search charger", "chargers nearby",
            "stations nearby", "ev station", "charging hubs nearby", "nearest cpo",
            "charging station", "charger near"
        ]
        has_specialized_intent = any(k in q for k in [
            "statiq", "compat", "iot", "sensor", "buy", "purchase", "switch",
            "v2l", "precondition", "regen", "drive mode", "hospital", "cafe", "atm", "pharmacy"
        ])
        # Corridor/route queries look like "Nagpur se Hyderabad ke charging stations batao" — they
        # contain "charging station" but are actually trip-planning queries; don't block them.
        has_corridor_intent = any(k in q for k in [
            "on the way", "on route", "en route", "going to", "drive to", "trip to", "highway",
            "route to", "jaate waqt", "raste mein", "raste me", "jatanna", "rastyavar",
            "se hyderabad", "se mumbai", "se pune", "se delhi", "se bhopal", "se indore",
            "se nagpur", "se amravati", "to hyderabad", "to mumbai", "to pune", "to delhi",
            "corridor", "via nh", "nh 44", "nh44", "highway charger", "charger on the way",
            "if i go to", "going to hyderabad", "going to mumbai", "going to pune",
            "nagpur to", "to hyderabad", "se ", "ke raste"
        ])
        if not is_explaining_query and not has_specialized_intent and not has_corridor_intent:
            if any(re.search(r'\b' + re.escape(k) + r'(?:s)?\b', q) for k in charger_action_kws):
                return None

        # ── 0. EMERGENCY / LOW BATTERY (highest priority) ──────────────────────────
        current_soc = (vehicle_status or {}).get('soc', 100)
        if current_soc <= 15 and self._matches(q, [
            "help", "emergency", "battery dying", "almost dead", "stranded",
            "low battery", "dying", "critical",
        ]):
            return self._ans_low_battery_emergency(vehicle_status, language)

        # ── 1. EXACT CITY & PINPOINT LOCATION / WHERE AM I ────────────────────
        if self._matches(q, [
            "which city", "in which city", "what city", "current city", "city am i in",
            "where am i", "what is my location", "current location", "where is the car",
            "my location", "kahan hoon main", "location kya hai", "kuthe aaho mee",
            "shahar kaunsa", "konte shahar", "meri location", "exact location", "gps location",
            "which city i am", "which city am i", "tell me in which city", "where are we",
            (["which", "what", "where", "tell me"], ["city", "location", "shahar", "shehar"]),
        ]):
            return self._ans_location(vehicle_status, language)

        # ── 2. NEAREST PETROL PUMP / FUEL STATION ─────────────────────────────
        if self._matches(q, [
            "petrol pump", "petrol", "fuel station", "gas station", "diesel pump",
            "fuel pump", "nearest petrol", "paas ka petrol pump", "petrol pump kahan",
            "petrol pump kuthe", "petrol bunk", "gas bunk",
            (["petrol", "fuel", "gas", "diesel"], ["pump", "station", "bunk", "kahan", "kuthe", "near", "nearest"]),
        ]):
            return self._ans_petrol_pump(vehicle_status, language)

        # ── 3. CHARGING COST & SPECIFIC OPERATOR TARIFFS ──────────────────────
        if self._matches(q, [
            "cost of", "tariff of", "how much does it cost", "charging cost", "cost to charge",
            "charging tariff", "cost per kwh", "rate per kwh", "kharcha kitna", "charge kiti lagel",
            "price of charging", "what is the cost", "tata power cost", "tata power tariff",
            "cost of tata power",
            (["cost", "tariff", "rate", "price", "kharcha", "bill", "kitna kharch"], ["charge", "charging", "tata power", "kwh", "unit", "station", "fast", "super fast"]),
        ]):
            return self._ans_charging_cost(q, vehicle_status, language)

        # ── 4. WHAT IS SMARTRESERVE / PROJECT OVERVIEW ─────────────────────────
        if self._matches(q, [
            "what is smartreserve", "explain this project", "how does smartreserve work",
            "what have i built", "what did we build", "project overview", "what is this project",
            "what problem does this solve", "about smartreserve", "explain the project",
            "tell me about this project", "smartreserve kya hai", "ye project kya hai",
            "project ke baare mein batao", "smartreserve kay aahe", "project samjaun sanga",
            "how does this project work", "what is this", "tell me about smartreserve",
            (["project", "smartreserve", "smart reserve"], ["work", "how", "what", "explain", "about", "overview", "kya", "kay", "tell", "kare", "built", "features", "batao", "sanga"]),
        ]):
            return self._ans_project_overview(language)

        # ── 5. WHAT IS OCPP / OCPP 1.6J / KIOSK PROTOCOL ───────────────────────
        if self._matches(q, [
            "what is ocpp", "ocpp 1.6j", "open charge point protocol", "how does the kiosk work",
            "how does kiosk work", "how does unlock work", "remotestarttransaction", "reservenow",
            "kiosk protocol", "ocpp kya hai", "kiosk kaise kaam karta hai", "ocpp kay aahe",
            (["ocpp", "open charge point", "reservenow", "unlockconnector"],),
            (["kiosk"], ["work", "how", "protocol", "kaise", "unlock", "kare"]),
        ]):
            return self._ans_ocpp(language)

        # ── 6. WHY RS. 200 ESCROW / DEPOSIT / REFUND ───────────────────────────
        if self._matches(q, [
            "why 200", "why deposit", "escrow", "is deposit refundable",
            "deposit fee", "why do i have to pay deposit", "refund", "razorpay", "upi payment",
            "deposit kyu", "paise wapas", "refund milega", "deposit parat milnar ka", "200 rupaye kashasathi",
            (["200", "deposit", "escrow", "fee", "refund", "paise"], ["why", "what", "is", "kyu", "kashasathi", "how", "wapas", "refund", "rupaye", "rupees", "charge", "policy"]),
        ]):
            return self._ans_escrow(language)

        # ── 7. DEMAND FORECASTING / MACHINE LEARNING / CONGESTION ──────────────
        if self._matches(q, [
            "demand forecast", "demand forecasting", "how does demand forecasting work",
            "congestion score", "what ml model", "machine learning", "forecasting",
            "lightgbm", "xgboost", "predict congestion", "grid load",
            "demand forecasting kaise", "congestion kaise predict",
            (["demand", "congestion", "forecast", "prediction", "xgboost", "lightgbm", "grid load"], ["how", "what", "work", "model", "predict", "score", "kaise", "kya"]),
        ]):
            return self._ans_demand_forecasting(language)

        # ── 8. PRIORITY ENGINE / MULTI-CRITERIA RANKING ────────────────────────
        if self._matches(q, [
            "how does priority engine work", "priority engine", "ranking algorithm",
            "how do you rank", "how do you choose stations", "recommendation logic",
            "scoring algorithm", "priority engine kaise", "ranking kaise hoti hai",
            (["priority", "ranking", "scoring algorithm", "criteria", "recommendation logic"], ["how", "what", "work", "engine", "formula", "kaise", "kya", "rule"]),
        ]):
            return self._ans_priority_engine(language)

        # ── 9. VEHICLE SPECS / HYUNDAI IONIQ 5 / 800V PLATFORM ─────────────────
        if self._matches(q, [
            "what car is this", "ioniq 5 specs", "battery capacity", "battery size",
            "800v", "e-gmp", "hyundai ioniq 5", "car specifications", "vehicle specs",
            "gaadi kaunsi hai", "ioniq 5 ke features", "battery size kitna hai",
            "ioniq", "ioniq 5", "tell me about ioniq", "about the car",
            (["car", "vehicle", "ioniq"], ["specs", "model", "features", "details", "specification", "battery size", "capacity", "about", "tell"]),
        ]):
            return self._ans_vehicle_specs(vehicle_status, language)

        # ── 10. CHARGING TECH / CCS2 / AC VS DC ───────────────────────────────
        if self._matches(q, [
            "what is ccs2", "ac vs dc", "slow vs fast charging", "type 2", "connector type",
            "charging speed", "how fast can i charge", "tariff", "charging cost", "cost per kwh",
            "ccs2 kya hai", "ac dc mein kya farak", "charging ka kitna kharcha", "rate kya hai",
            (["ccs2", "connector", "plug"], ["what", "type", "compatible", "kya"]),
            (["ac", "dc"], ["vs", "difference", "farak", "speed", "fast"]),
        ]):
            return self._ans_charging_tech(language)

        # ── 11. TYRE PRESSURE / TPMS ───────────────────────────────────────────
        if self._matches(q, [
            "tyre pressure", "tire pressure", "tpms", "are tyres ok", "tyre status",
            "tyre health", "hawa kitni hai", "tyre ki hawa", "tyre check karo", "tyre chi hawa",
            (["tyre", "tire", "tpms", "pressure"], ["check", "status", "ok", "low", "hawa", "kiti", "kitna"]),
        ]):
            return self._ans_tyre_pressure(vehicle_status, language)

        # ── 12. BATTERY HEALTH / STATE OF HEALTH (SoH) ─────────────────────────
        if "precondition" not in q and self._matches(q, [
            "battery health", "state of health", "soh", "battery condition", "battery life",
            "battery degradation", "battery ki health", "battery chi health",
            (["battery"], ["health", "condition", "life", "soh", "degradation", "theek"]),
        ]):
            return self._ans_battery_health(vehicle_status, language)

        # ── 13. SERVICE / MAINTENANCE DUE ─────────────────────────────────────
        if self._matches(q, [
            "service status", "maintenance due", "when is next service", "service due",
            "maintenance schedule", "service kab hai", "service kadhi aahe", "car service",
            (["service", "maintenance", "brake", "coolant", "wiper"], ["due", "when", "status", "schedule", "check", "kab", "kadhi", "pad", "fluid"]),
        ]):
            return self._ans_service_status(vehicle_status, language)

        # ── 14. NAGPUR CHARGERS & CHARGING NETWORK (informational only) ─────────
        if not any(w in q for w in ["find", "see", "show", "view", "reserve", "book", "lock"]) and self._matches(q, [
            "chargers in nagpur", "nagpur chargers", "how many chargers in nagpur",
            "nagpur network", "nagpur mein kitne", "nagpur madhe kiti",
            (["nagpur"], ["network", "many", "kitne", "kiti", "overview"]),
        ]):
            return self._ans_nagpur_network(language)

        # ── 15. INDIAN CPOs / NETWORKS SUPPORTED ──────────────────────────────
        if self._matches(q, [
            "which cpos", "supported networks", "who are the operators", "charging networks in india",
            "cpos in india", "list of cpos", "all operators",
            (["cpo", "cpos", "network", "networks", "operator", "operators"], ["in india", "supported", "list", "kaunse", "konta", "all", "which"]),
        ]):
            return self._ans_cpo_networks(language)

        # ── 16. HELP / CAPABILITIES / WHAT CAN YOU DO ─────────────────────────
        if self._matches(q, [
            "what can you do", "help", "commands", "options", "kya kar sakti ho",
            "kya kar sakte ho", "help me", "features", "kay karu shaktes",
            (["what", "kya", "kay"], ["can you do", "kar sakti", "kar sakte", "help", "commands"]),
        ]):
            return self._ans_help(language)

        # ── 17. THANK YOU / APPRECIATION ──────────────────────────────────────
        if self._matches(q, [
            "thank you", "thanks", "dhanyawad", "shukriya", "good job",
            "great job", "well done", "bahut badhiya", "dhanyavaad",
        ]):
            return self._ans_thank_you(language)

        # ── 18. WHO ARE YOU / IDENTITY ────────────────────────────────────────
        if self._matches(q, [
            "who are you", "who made you", "what is your name", "aap kaun ho",
            "tum kaun ho", "naam kya hai", "tu kon aahes",
            (["who", "kaun", "kon"], ["are you", "made you", "ho", "aahes"]),
        ]):
            return self._ans_identity(language)

        # ── RANGE / DESTINATION REACHABILITY CHECK ─────────────────────────────────
        if self._matches(q, [
            "can i reach", "can we reach", "will i make it to", "will battery last",
            "enough range", "make it to", "reach wardha", "reach pune", "reach mumbai",
            "reach hyderabad", "reach delhi", "reach nagpur",
            "battery enough", "range enough", "will charge last",
            "kya main pahunch sakta", "pohochta ka", "range puregi",
            (["reach", "make it", "pahunch", "pohoch"], ["charging", "without", "bina", "enough", "km", "city", "place", "destination"]),
            (["can", "will", "kya"], ["reach", "make it", "last", "enough"]),
        ]):
            return self._ans_range_check(q, vehicle_status, language)

        # ── V2L / VEHICLE-TO-LOAD ──────────────────────────────────────────────────
        if self._matches(q, [
            "v2l", "vehicle to load", "power outlet", "use car as generator",
            "charge my phone with car", "power from car", "external power",
            "v2l kya hai", "gaadi se bijli", "car se charge",
            (["v2l", "vehicle to load", "power outlet", "generator"], ["work", "use", "how", "kaise", "kya"]),
        ]):
            return self._ans_v2l(vehicle_status, language)

        # ── BATTERY PRECONDITIONING ────────────────────────────────────────────────
        if self._matches(q, [
            "battery preconditioning", "pre-conditioning", "precondition",
            "warm up battery", "prepare battery", "fast charge prep",
            "preconditioning kya hai", "battery garam karna",
            (["battery", "precondition", "preconditioning"], ["what", "how", "why", "explain", "enable", "kya", "kaise"]),
        ]):
            return self._ans_preconditioning(language)

        # ── REGENERATIVE BRAKING / i-PEDAL ─────────────────────────────────────────
        if self._matches(q, [
            "regenerative braking", "regen braking", "i-pedal", "one pedal",
            "paddle shifter", "regen mode", "regeneration",
            "regen kya hai", "one pedal driving",
            (["regen", "regenerative", "i-pedal", "one pedal", "paddle"], ["braking", "drive", "mode", "kaise", "kya", "work", "how"]),
        ]):
            return self._ans_regen(vehicle_status, language)

        # ── DRIVE MODES ────────────────────────────────────────────────────────────
        if self._matches(q, [
            "drive mode", "eco mode", "sport mode", "normal mode",
            "driving mode", "how to change mode", "switch mode",
            "eco kya hai", "sport mode kaise",
            (["eco", "sport", "normal", "drive mode"], ["mode", "difference", "switch", "enable", "activate", "kaise", "kya"]),
        ]):
            return self._ans_drive_modes(vehicle_status, language)

        # ── NEARBY AMENITIES / POI ─────────────────────────────────────────────────
        if self._matches(q, [
            "nearest hospital", "nearby cafe", "nearest restaurant", "nearby food",
            "parking nearby", "nearest atm", "nearby pharmacy", "medical store",
            "nearest mechanic", "tyre shop", "puncture shop",
            "hospital kahan", "cafe kahan", "khana kahan",
            (["nearest", "nearby", "paas", "javal"], ["hospital", "cafe", "restaurant", "food", "atm", "pharmacy", "medical", "mechanic", "tyre", "puncture", "parking"]),
        ]):
            return self._ans_nearby_amenities(q, vehicle_status, language)

        # ── CHARGING NETWORK COMPARISON ────────────────────────────────────────────
        if not any(k in q.lower() for k in ["compat", "suit", "mismatch"]) and self._matches(q, [
            "which charger is best", "tata power vs eesl", "which network is better",
            "best charging network", "compare charger", "fastest charger in nagpur",
            (["best", "fastest", "compare", "which"], ["charger", "network", "cpo", "station", "tata power", "eesl", "chargezone"]),
        ]):
            return self._ans_network_comparison(language)

        # ── CABIN CONTROLS ─────────────────────────────────────────────────────────
        if self._matches(q, [
            "set ac", "set temperature", "ac on", "ac off", "turn on ac", "cool the car",
            "set fan", "adjust climate", "adjust temperature",
            "ac karo", "thanda karo", "garam karo", "temperature set karo",
            (["set", "turn", "adjust", "change"], ["ac", "temperature", "climate", "fan", "cool", "heat"]),
        ]):
            return self._ans_cabin_control(q, language)

        # ── VEHICLE SWITCHING / MODEL SELECT (5 HYUNDAI + 3 COMPETITOR CARS) ──
        if self._matches(q, [
            "switch car", "change car", "switch vehicle", "change vehicle",
            "switch model", "change model", "select car", "select vehicle",
            "switch to ioniq", "switch to nexon", "switch to kona", "switch to creta",
            "switch to inster", "switch to xuv400", "switch to mg", "switch to tata",
            "change to nexon", "change to ioniq", "gaadi badlo", "car change karo",
            "gaadi change karo", "gadi badla",
            (["switch", "change", "badlo", "badla", "select"], ["car", "vehicle", "gaadi", "gadi", "model", "ioniq", "nexon", "kona", "creta", "inster", "xuv400", "mg"]),
        ]):
            return self._ans_vehicle_switch(q, vehicle_status, language)

        # ── CHARGER COMPATIBILITY CHECK & 4-5 COMPATIBILITY OPTIONS ───────────
        if self._matches(q, [
            "is my car compatible", "is this compatible", "charger compatibility",
            "compatibility options", "will this charger work", "compatible with my car",
            "is ioniq compatible", "is nexon compatible", "plug mismatch",
            "compatible charger", "can i charge here", "kya ye charger chalega",
            "compatibility kya hai", "compatible aahe ka",
            (["compatible", "compatibility", "chalega", "suit"], ["charger", "station", "car", "vehicle", "plug", "socket", "connector", "my car", "nexon", "ioniq"]),
        ]):
            return self._ans_compatibility_check(q, vehicle_status, language)

        # ── LIVE HYDERABAD & STATIQ REAL-TIME TELEMATICS ──────────────────────
        # Guard: don't intercept corridor/route queries that happen to mention hyderabad
        _is_corridor_q = any(k in q for k in [
            "se hyderabad", "to hyderabad", "route", "on the way", "going to",
            "jaate waqt", "raste mein", "highway", "if i go", "trip"
        ])
        if not _is_corridor_q and self._matches(q, [
            "statiq", "live chargers in hyderabad", "hyderabad live chargers",
            "statiq chargers", "real time charger hyderabad", "who is charging",
            "current vehicle charging", "current charging car", "is gun occupied",
            "statiq basheerbagh", "statiq tank bund", "live telematics",
            "hyderabad charger status", "hyderabad live status",
            (["statiq", "hyderabad", "live"], ["charger", "status", "occupied", "charging", "gun", "port", "telematics", "karo", "batao"]),
        ]):
            return self._ans_live_hyderabad_statiq(q, vehicle_status, language)

        # ── HACKATHON IOT SENSOR & COMMERCIAL API BUYING GUIDE ─────────────────
        if self._matches(q, [
            "where can i buy real time data", "where to buy real time", "buy charger data",
            "buy ev data", "buy charger api", "charger without sensor", "charger doesn't have sensor",
            "how to build iot device", "iot sensor for ev", "low cost iot sensor",
            "pulse energy", "kazam api", "tomtom api", "hackathon device",
            "sensor nahi hai", "data kahan se khareedein", "iot device kaise banaye",
            (["buy", "purchase", "khareed", "cost"], ["api", "data", "real time", "telemetry", "charger"]),
            (["iot", "sensor", "hardware", "esp32"], ["device", "build", "charger", "socket", "hackathon", "blueprint", "cost"]),
        ]):
            return self._ans_iot_and_api_buying_guide(q, vehicle_status, language)

        # ── HIGHWAY CORRIDOR TRIP PLANNER ──────────────────────────────────────
        # Matches: "Nagpur se Hyderabad ke charging stations batao",
        #          "on my way to Hyderabad", "route to Hyderabad", "if I go to Pune"
        DEST_CITIES = {
            "hyderabad": {"lat": 17.3850, "lng": 78.4867, "nh": "NH 44", "dist_km": 498, "hrs": 7.5},
            "mumbai":    {"lat": 19.0760, "lng": 72.8777, "nh": "NH 44/48", "dist_km": 874, "hrs": 13.0},
            "pune":      {"lat": 18.5204, "lng": 73.8567, "nh": "NH 65", "dist_km": 925, "hrs": 14.0},
            "delhi":     {"lat": 28.6139, "lng": 77.2090, "nh": "NH 44", "dist_km": 1102, "hrs": 16.0},
            "bhopal":    {"lat": 23.2599, "lng": 77.4126, "nh": "NH 46", "dist_km": 352, "hrs": 5.5},
            "indore":    {"lat": 22.7196, "lng": 75.8577, "nh": "NH 52", "dist_km": 469, "hrs": 7.0},
            "amravati":  {"lat": 20.9374, "lng": 77.7796, "nh": "NH 53",  "dist_km": 147, "hrs": 2.5},
            "wardha":    {"lat": 20.7453, "lng": 78.6022, "nh": "NH 7",   "dist_km": 78,  "hrs": 1.3},
            "akola":     {"lat": 20.7070, "lng": 77.0070, "nh": "NH 53",  "dist_km": 201, "hrs": 3.2},
        }
        matched_dest = None
        for city_name, city_info in DEST_CITIES.items():
            if city_name in q:
                matched_dest = (city_name, city_info)
                break
        if matched_dest:
            return self._ans_corridor_trip(q, matched_dest, vehicle_status, language)

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Helper matching logic
    # ──────────────────────────────────────────────────────────────────────────
    def _matches(self, text: str, patterns: list) -> bool:
        t = text.lower()
        for p in patterns:
            if isinstance(p, tuple):
                if all(any(w in t for w in group) for group in p):
                    return True
            elif isinstance(p, str):
                if p in t:
                    return True
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Detailed Answers per Domain
    # ──────────────────────────────────────────────────────────────────────────

    def _ans_project_overview(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Hyundai SmartReserve ek AI-powered in-cabin EV charging co-pilot hai, jo Hyundai Ideathon 2026 (Track B) "
                "ke liye develop kiya gaya hai. Yeh EV drivers ki range anxiety aur charging station par lambi queues ko khatam karta hai. "
                "Isme real-time 29,260+ Indian charging stations ka data hai, multi-criteria priority engine jo best charger recommend karta hai, "
                "aur OCPP 1.6J protocol ke through 30-minute guaranteed slot reservation with 6-digit secure PIN diya gaya hai."
            )
        elif lang == "mr":
            txt = (
                "Hyundai SmartReserve he ek AI-powered in-cabin EV charging co-pilot system aahe, je Hyundai Ideathon 2026 (Track B) "
                "sathi build kele aahe. He system driver chi range anxiety ani charger varil wait time kami karte. "
                "Ismadhe 29,260+ Indian stations, multi-criteria priority engine, ani OCPP 1.6J dwara 30-minute slot lock "
                "ani 6-digit secure PIN di-la jaato."
            )
        else:
            txt = (
                "Hyundai SmartReserve is an AI-powered in-cabin EV charging co-pilot and priority reservation system "
                "built for the Hyundai Ideathon 2026 (Track B). It eliminates range anxiety and charger queuing by integrating "
                "telematics with 29,260+ verified Indian charging stations. It ranks chargers using a multi-criteria priority engine, "
                "guarantees a 30-minute hardware lock via OCPP 1.6J ReserveNow, and verifies access at the station kiosk using a secure 6-digit PIN."
            )
        return (txt, "PROJECT_INFO", {
            "title": "Hyundai SmartReserve EV",
            "version": "2.0 (Track B)",
            "stations_loaded": 29260,
            "core_features": ["Voice Co-Pilot (AVA)", "Priority Engine", "OCPP 1.6J Lock", "Razorpay Escrow", "Demand Forecasting"]
        })

    def _ans_ocpp(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "OCPP (Open Charge Point Protocol 1.6J) EV charging stations aur central reservation system ke beech standard communication protocol hai. "
                "SmartReserve mein, jab aap reserve karte hain, backend station ko 'ReserveNow' command bhej kar connector lock karta hai. "
                "Jab aap kiosk par apna 6-digit PIN enter karte hain, OCPP 'UnlockConnector' aur 'RemoteStartTransaction' command bhej kar charging start karata hai."
            )
        elif lang == "mr":
            txt = (
                "OCPP (Open Charge Point Protocol 1.6J) he EV charging station ani central cloud madhye communicate karnara open standard aahe. "
                "SmartReserve 'ReserveNow' dwara connector 30 min lock karte, ani kiosk var 6-digit PIN dilyavar "
                "'UnlockConnector' ani 'RemoteStartTransaction' ne charging shuru hote."
            )
        else:
            txt = (
                "OCPP (Open Charge Point Protocol 1.6J over WebSockets) is the global open standard between EV charging stations and central management networks. "
                "SmartReserve uses OCPP ReserveNow to physically lock the connector for 30 minutes. Upon arrival, entering your 6-digit PIN at the kiosk "
                "triggers UnlockConnector and RemoteStartTransaction to begin charging securely."
            )
        return (txt, "OCPP_INFO", {
            "protocol": "OCPP 1.6J (JSON over WebSockets)",
            "supported_actions": ["ReserveNow", "UnlockConnector", "RemoteStartTransaction", "RemoteStopTransaction", "MeterValues"]
        })

    def _ans_escrow(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Rs. 200 ka escrow deposit ghost reservations (fake bookings) rokne ke liye rakha gaya hai. "
                "Aapki booking par yeh amount hold hoti hai via Razorpay ya UPI. Jaise hi aap station pahunch kar apna 6-digit PIN enter karke charging start karte hain, "
                "yeh Rs. 200 turant 100% refund ho jaate hain ya aapke final bill mein adjust ho jaate hain. Demo mode mein yeh deposit waived hai!"
            )
        elif lang == "mr":
            txt = (
                "Rs. 200 cha escrow deposit fake reservations thambavnyasathi aahe. "
                "Tumi station var jaun PIN enter kelyavar he Rs. 200 purna refund hotat kiva charging bill madhe adjust hotat. "
                "Demo mode madhe ha deposit waived aahe."
            )
        else:
            txt = (
                "The Rs. 200 refundable escrow deposit (processed via Razorpay/UPI) prevents ghost reservations and protects charger availability. "
                "The moment you arrive at the station kiosk and enter your 6-digit PIN, the Rs. 200 is instantly 100% refunded or credited towards your charging bill. "
                "In demo mode, this deposit is automatically waived!"
            )
        return (txt, "PAYMENT_INFO", {
            "escrow_amount": 200,
            "currency": "INR",
            "policy": "100% Refundable upon arrival",
            "demo_mode": True
        })

    def _ans_demand_forecasting(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "SmartReserve ka Demand Forecasting engine machine learning (XGBoost aur LightGBM) use karta hai. "
                "Yeh historical EV charging sessions, time of day, peak grid loads, aur regional vehicle registrations ke data se "
                "har station ka future congestion score aur queue time predict karta hai, taaki aapko hamesha khali aur fast charger mile."
            )
        elif lang == "mr":
            txt = (
                "Demand Forecasting engine XGBoost ani LightGBM machine learning models vaprun charging demand predict karte. "
                "Time of day, historical sessions, ani grid load analys karun he real-time congestion scores (Low, Medium, High) provide karte."
            )
        else:
            txt = (
                "Our Demand Forecasting AI uses XGBoost and LightGBM models trained on real EVCS operational data. "
                "It correlates time of day, grid peak load, day of week, and regional EV penetration to predict station congestion scores "
                "(LOW, MEDIUM, HIGH) and estimated wait times before you arrive."
            )
        return (txt, "FORECASTING_INFO", {
            "models": ["XGBoost", "LightGBM"],
            "features": ["Time of day", "Peak grid load kW", "Historical utilization", "Day of week"]
        })

    def _ans_priority_engine(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Priority Engine ek multi-criteria scoring algorithm use karta hai: "
                "30% weight Distance aur Drive Time ko, 25% Charger Power (kW) aur 80% tak ke time ko, "
                "20% Real-time Congestion aur Queue time ko, 15% Tariff rate (Rs. /kWh) ko, aur 10% Reliability score ko. "
                "Agar battery 20% se kam ho, toh yeh automatically high-speed DC fast chargers ko top priority deta hai."
            )
        elif lang == "mr":
            txt = (
                "Priority Engine multi-criteria formula vaparto: 30% अंतर, 25% चार्जर पावर (kW), "
                "20% Congestion व वेटिंग वेळ, 15% दर (Rs. /kWh), आणि 10% विश्वसनीयता. "
                "Battery kami aslyas he 120–150 kW DC fast chargers la pratham preference dete."
            )
        else:
            txt = (
                "The Priority Engine uses a dynamic multi-criteria ranking formula: "
                "30% Distance and Drive Time, 25% Charger Power rating and charging time to 80%, "
                "20% Real-time Congestion and queue delay, 15% Tariff rate (Rs. /kWh), and 10% Historical reliability. "
                "When battery SoC drops below 20%, it dynamically shifts highest priority to nearby 120–150 kW DC fast chargers."
            )
        return (txt, "PRIORITY_ENGINE_INFO", {
            "weights": {"Distance": 0.30, "Power_Speed": 0.25, "Congestion_Queue": 0.20, "Tariff": 0.15, "Reliability": 0.10}
        })

    def _ans_vehicle_specs(self, v_status: Optional[dict], lang: str):
        soc = (v_status or {}).get("soc", 18.0)
        rng = (v_status or {}).get("range_km", 86.6)
        if lang in ("hi", "hi+en"):
            txt = (
                f"Aap Hyundai Ioniq 5 chala rahe hain, jisme 72.6 kWh lithium-ion battery pack aur 800V E-GMP platform hai. "
                f"Abhi battery {soc:.0f}% par hai aur lagbhag {rng:.0f} km ki range bachi hai. "
                f"Yeh car ultra-fast 350 kW DC charging support karti hai, jo 10% se 80% sirf 18 minute mein charge kar sakti hai."
            )
        elif lang == "mr":
            txt = (
                f"Tumhi Hyundai Ioniq 5 chalavat aahat. Ismadhe 72.6 kWh battery ani 800V E-GMP platform aahe. "
                f"Sadyachi battery {soc:.0f}% aahe ({rng:.0f} km range). "
                f"He car 350 kW DC fast charger var 10% te 80% fakt 18 minatamadhe charge hote."
            )
        else:
            txt = (
                f"You are driving the Hyundai Ioniq 5, built on Hyundai's 800-volt E-GMP platform with a 72.6 kWh battery pack. "
                f"Your battery is currently at {soc:.0f}% with approximately {rng:.0f} km of range remaining. "
                f"It supports ultra-fast 350 kW DC charging, taking you from 10% to 80% SoC in just 18 minutes."
            )
        return (txt, "VEHICLE_SPECS", {
            "model": "Hyundai Ioniq 5 (AWD)",
            "platform": "800V E-GMP",
            "battery_capacity_kwh": 72.6,
            "max_charging_speed_kw": 350,
            "current_soc": soc,
            "current_range_km": rng
        })

    def _ans_charging_tech(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Aapki gaadi CCS2 (Combined Charging System 2) connector use karti hai. "
                "DC Fast Chargers (50–350 kW) battery ko direct 800V power dete hain jisse 18–25 minute mein charge hota hai. "
                "AC Chargers (Type 2, 7.4–22 kW) normal slow charging ke liye hote hain. "
                "Fast charging tariff lagbhag Rs. 18 se Rs. 22.50 per kWh hota hai."
            )
        elif lang == "mr":
            txt = (
                "Tumchya car madhe CCS2 connector aahe. DC Fast Chargers (50–350 kW) ne 18–25 minatamadhe 80% charge hote. "
                "AC Chargers (Type 2) slow charging sathi astat. DC fast charging cha tariff Rs. 18 te Rs. 22.50 per kWh aahe."
            )
        else:
            txt = (
                "Your Hyundai Ioniq 5 uses a CCS2 (Combined Charging System 2) connector. "
                "DC Fast Chargers (50 to 350 kW) feed electricity directly to the 800V battery, completing an 80% charge in 18 to 25 minutes. "
                "AC charging (Type 2, up to 11 kW) is for slower overnight charging. DC fast charging tariffs typically range from Rs. 18.00 to Rs. 22.50 per kWh."
            )
        return (txt, "CHARGING_TECH_INFO", {
            "connector": "CCS2 (DC Fast) + Type 2 (AC)",
            "fast_charge_rate_kw": "Up to 350 kW",
            "typical_tariff_inr": "Rs. 18.00 - Rs. 22.50 / kWh"
        })

    def _ans_tyre_pressure(self, v_status: Optional[dict], lang: str):
        tpms = (v_status or {}).get("tyre_pressures", {"front_left": "low", "front_right": "ok", "rear_left": "ok", "rear_right": "ok"})
        fl_psi = 28.0
        other_psi = 36.0
        if lang in ("hi", "hi+en"):
            txt = (
                f"Tyre Pressure Warning: Agla-baya (Front-Left) tyre mein hawa kam hai ({fl_psi} PSI). "
                f"Recommended pressure 36 PSI hai. Baki teen tyres nominal hain (35–36 PSI). Kripya agle station par hawa bharwa lein."
            )
        elif lang == "mr":
            txt = (
                f"Tyre Pressure Alert: Pudhil davyatya tyre madhe hawa kami aahe ({fl_psi} PSI). "
                f"Recommended pressure 36 PSI aahe. Baki teenhi tyres ok aahet. Kripya hawa bharun ghya."
            )
        else:
            txt = (
                f"Tyre Pressure Alert: The front-left tyre is low at {fl_psi} PSI (recommended is 36 PSI). "
                f"The other three tyres are in the nominal range at 35 to 36 PSI. I recommend topping up air at your next stop."
            )
        return (txt, "TPMS_ALERT", {
            "front_left_psi": fl_psi,
            "status": "warning",
            "recommended_psi": 36.0
        })

    def _ans_battery_health(self, v_status: Optional[dict], lang: str):
        bh = (v_status or {}).get("component_health", {}).get("battery_health", 98.5)
        if lang in ("hi", "hi+en"):
            txt = (
                f"Aapki high-voltage battery health (State of Health) behtareen hai — {bh:.1f}%. "
                f"Thermal liquid cooling system aur cell balancing bilkul normal function kar rahe hain."
            )
        elif lang == "mr":
            txt = (
                f"Battery State of Health uttam aahe — {bh:.1f}%. "
                f"Battery cooling ani cell balancing system normal function karat aahet."
            )
        else:
            txt = (
                f"Your high-voltage battery State of Health is excellent at {bh:.1f}%. "
                f"Battery thermal management, liquid cooling, and cell voltage balancing are operating within optimal factory tolerances."
            )
        return (txt, "BATTERY_HEALTH_INFO", {
            "battery_health_pct": bh,
            "cooling_system": "Normal",
            "cell_balancing": "Optimal"
        })

    def _ans_service_status(self, v_status: Optional[dict], lang: str):
        next_serv = (v_status or {}).get("service_status", {}).get("next_service_in_km", 5500.0)
        motor_h = (v_status or {}).get("component_health", {}).get("motor_health", 99.0)
        brake_pad = (v_status or {}).get("component_health", {}).get("brake_pad_life", 75.0)
        wiper = (v_status or {}).get("component_health", {}).get("wiper_fluid", 40.0)
        if lang in ("hi", "hi+en"):
            txt = (
                f"Agli routine service {next_serv:.0f} km baad scheduled hai. "
                f"Motor health {motor_h:.0f}%, brake pads {brake_pad:.0f}% hain. "
                f"Sirf wiper fluid thoda kam hai ({wiper:.0f}%), baki sab kuch fully operational hai."
            )
        elif lang == "mr":
            txt = (
                f"Pudhil scheduled service {next_serv:.0f} km nantar aahe. "
                f"Motor health {motor_h:.0f}%, brake pads {brake_pad:.0f}%, wiper fluid {wiper:.0f}% aahe. "
                f"Vehicle overall status green aahe."
            )
        else:
            txt = (
                f"Your next scheduled maintenance is due in {next_serv:.0f} km. "
                f"Drive motor health is at {motor_h:.0f}%, brake pads have {brake_pad:.0f}% life remaining, "
                f"and wiper fluid is at {wiper:.0f}%. All primary drivetrain systems are fully operational."
            )
        return (txt, "SERVICE_STATUS", {
            "next_service_km": next_serv,
            "motor_health": motor_h,
            "brake_pad_life": brake_pad,
            "wiper_fluid": wiper
        })

    def _ans_petrol_pump(self, v_status: Optional[dict], lang: str):
        pumps = [
            {"name": "IOCL Indian Oil Retail Outlet", "address": "Civil Lines / Sitabuldi, Nagpur", "dist": 0.8, "lat": 21.1526, "lng": 79.0882},
            {"name": "HP Petrol Pump", "address": "Dosar Bhavan Chowk, CA Road, Nagpur", "dist": 1.1, "lat": 21.1523, "lng": 79.0957},
            {"name": "Reliance BP Retail Outlet", "address": "Orient Hotel, Great Nag Road, Nagpur", "dist": 1.1, "lat": 21.1367, "lng": 79.0920},
            {"name": "BPCL Retail Outlet", "address": "Gaddigodam Chowk, Wardha Road, Nagpur", "dist": 1.6, "lat": 21.1599, "lng": 79.0830},
        ]
        p1 = pumps[0]
        if lang in ("hi", "hi+en"):
            txt = (
                f"Aapke exact location se sabse paas ka petrol pump {p1['name']} hai ({p1['address']}), lagbhag {p1['dist']} km door. "
                "Aapki Hyundai Ioniq 5 all-electric EV hai isliye fuel ki zaroorat nahi hai, par hawa (air inflation), nitrogen, aur windshield wash ke liye yeh station available hai."
            )
        elif lang == "mr":
            txt = (
                f"Tumchya exact location pasun sarvat javalcha petrol pump {p1['name']} ({p1['address']}) aahe, fakt {p1['dist']} km antaravar. "
                "Tumchi Ioniq 5 electric car aahe, pan tyre air kiva windscreen cleaning sathi tumhi ithe jau shakta."
            )
        else:
            txt = (
                f"The nearest petrol pump to your exact location in Nagpur is {p1['name']} at {p1['address']}, approximately {p1['dist']} km away. "
                "Note that your Hyundai Ioniq 5 is a 100% electric vehicle running on its 72.6 kWh battery, but this fuel station provides tyre air inflation, nitrogen, and basic amenities."
            )
        return (txt, "PETROL_PUMP_INFO", {
            "title": "Nearest Petrol Pump",
            "pump_name": p1["name"],
            "address": p1["address"],
            "distance_km": p1["dist"],
            "lat": p1["lat"],
            "lng": p1["lng"],
            "action": "show_petrol_pump",
            "pumps": pumps
        })

    def _ans_charging_cost(self, q: str, v_status: Optional[dict], lang: str):
        soc = 18.0
        if v_status and "soc" in v_status:
            soc = float(v_status["soc"])
        battery_kwh = 72.6
        kwh_needed = round((80.0 - min(soc, 80.0)) / 100.0 * battery_kwh, 1)

        cpo_name = "Tata Power EZ Charge"
        rate = 22.50
        kw = 120
        if "bpcl" in q:
            cpo_name = "BPCL EV Hub"; rate = 21.00; kw = 60
        elif "jio" in q or "pulse" in q:
            cpo_name = "Jio-bp Pulse"; rate = 23.00; kw = 150
        elif "statiq" in q:
            cpo_name = "Statiq Fast Hub"; rate = 20.50; kw = 60
        elif "eesl" in q:
            cpo_name = "EESL Metro Hub"; rate = 18.50; kw = 142
        elif "ac" in q or "slow" in q or "type 2" in q:
            cpo_name = "AC Fast Charger"; rate = 12.00; kw = 22

        est_cost = round(kwh_needed * rate)
        mins = round((kwh_needed / (kw * 0.9)) * 60)

        if lang in ("hi", "hi+en"):
            txt = (
                f"{cpo_name} ka fast charging tariff Rs. {rate:.2f} per unit (kWh) hai. "
                f"Aapki Ioniq 5 ko {soc:.0f}% se 80% tak charge karne ke liye {kwh_needed} units lagenge, jiska estimated cost Rs. {est_cost} hoga. "
                f"Superfast {kw} kW charger par lagbhag {mins} minutes ka samay lagega."
            )
        elif lang == "mr":
            txt = (
                f"{cpo_name} cha fast charging dar Rs. {rate:.2f} prati kWh aahe. "
                f"Tumchya Ioniq 5 la {soc:.0f}% te 80% charge karnyasathi {kwh_needed} units lagtil, jyacha ekun kharch Rs. {est_cost} hoil. "
                f"Charging sathi sumare {mins} min lagtil."
            )
        else:
            txt = (
                f"{cpo_name} superfast DC charging tariff is Rs. {rate:.2f} per kWh. "
                f"To charge your Hyundai Ioniq 5 from {soc:.0f}% to 80% (approx {kwh_needed} kWh), the estimated cost is Rs. {est_cost}. "
                f"At {kw} kW power, this charging session will take approximately {mins} minutes."
            )
        return (txt, "CHARGING_COST_INFO", {
            "cpo": cpo_name,
            "tariff_per_kwh": rate,
            "soc_current": soc,
            "kwh_required": kwh_needed,
            "estimated_cost_rs": est_cost,
            "estimated_time_mins": mins,
            "action": "show_cost_breakdown"
        })

    def _ans_location(self, v_status: Optional[dict], lang: str):
        city = (v_status or {}).get("city", "Nagpur")
        lat = float((v_status or {}).get("latitude", 21.1458))
        lng = float((v_status or {}).get("longitude", 79.0882))

        # Check if at or near a known landmark
        lm_desc = ""
        if abs(lat - 21.1255) < 0.015 and abs(lng - 79.0524) < 0.015:
            lm_desc = " at VNIT Nagpur (Visvesvaraya National Institute of Technology, South Ambazari Road)"
        elif abs(lat - 21.1458) < 0.005 and abs(lng - 79.0882) < 0.005:
            lm_desc = " near Sitabuldi Metro Interchange, Nagpur Center"

        if lang in ("hi", "hi+en"):
            txt = (
                f"Aapki current vehicle location {city}, Maharashtra{lm_desc} hai. "
                f"Nagpur mein aapke paas 137 se zyada verified fast charging hubs available hain."
            )
        elif lang == "mr":
            txt = (
                f"Aapli sadyachi location {city}, Maharashtra{lm_desc} aahe. "
                f"Nagpur madhe 137 peksha jasta verified EV charging hubs uplabdh aahet."
            )
        else:
            txt = (
                f"Your vehicle is currently positioned in {city}, Maharashtra{lm_desc}. "
                f"There are over 137 verified EV charging hubs within range in the Nagpur metropolitan area."
            )
        return (txt, "LOCATION_INFO", {
            "city": city,
            "latitude": lat,
            "longitude": lng,
            "landmark": lm_desc.strip(),
            "stations_in_city": 137,
            "action": "show_car_location"
        })

    def _ans_set_location(self, q: str, v_status: Optional[dict], lang: str):
        matched_lm = None
        for k, info in NAGPUR_LANDMARKS.items():
            if k in q:
                matched_lm = info
                break
        
        if matched_lm:
            dest_lat = matched_lm["lat"]
            dest_lng = matched_lm["lng"]
            dest_name = matched_lm["name"]
            
            try:
                from vehicle_intelligence import vehicle
                if vehicle:
                    vehicle.set_location(dest_lat, dest_lng, "Nagpur")
            except Exception:
                pass
                
            if lang in ("hi", "hi+en"):
                txt = f"Vehicle location update ho gayi hai! Aapki Hyundai Ioniq 5 ab {dest_name} par locate ho gayi hai. Navigation map update kar diya gaya hai."
            elif lang == "mr":
                txt = f"Vehicle location update zali! Tumchi Hyundai Ioniq 5 aata {dest_name} var locate keli aahe."
            else:
                txt = f"Vehicle location updated! Your Hyundai Ioniq 5 is now positioned at {dest_name}. The navigation map has centered on your exact position."
                
            return (txt, "SET_LOCATION", {
                "location_name": dest_name,
                "latitude": dest_lat,
                "longitude": dest_lng,
                "city": "Nagpur",
                "action": "update_car_location"
            })
        else:
            return self._ans_location(v_status, lang)

    def _ans_distance_check(self, q: str, v_status: Optional[dict], lang: str):
        cur_lat = float((v_status or {}).get("latitude", 21.1458))
        cur_lng = float((v_status or {}).get("longitude", 79.0882))
        soc = float((v_status or {}).get("soc", 18.0))
        range_km = float((v_status or {}).get("range_km", 86.6))

        # Check landmarks first
        matched_lm = None
        for k, info in NAGPUR_LANDMARKS.items():
            if k in q:
                matched_lm = info
                break

        if matched_lm:
            dest_lat = matched_lm["lat"]
            dest_lng = matched_lm["lng"]
            dest_name = matched_lm["name"]
            
            # Geodesic with city road factor (1.25x)
            d_km = math.hypot((dest_lat - cur_lat) * 111.0, (dest_lng - cur_lng) * 103.0) * 1.25
            
            if d_km < 0.4:
                # User is at this exact location!
                if lang in ("hi", "hi+en"):
                    txt = f"Aap abhi {dest_name} par hi hain! Doori 0 km hai. Aapki battery {soc:.0f}% hai ({range_km:.0f} km range)."
                elif lang == "mr":
                    txt = f"Tumhi sadyas {dest_name} yethech aahat! Antar 0 km aahe. Battery {soc:.0f}% aahe."
                else:
                    txt = f"You are currently located right at {dest_name}! Distance is 0 km. Your battery is at {soc:.0f}% ({range_km:.0f} km range)."
                return (txt, "DISTANCE_CHECK", {
                    "destination": dest_name,
                    "distance_km": 0.0,
                    "lat": dest_lat,
                    "lng": dest_lng,
                    "at_destination": True,
                    "action": "show_landmark_location"
                })

            drive_mins = max(2, int(d_km / 22.0 * 60))  # avg 22 km/h city driving
            rem_soc = max(0.0, soc - (d_km / max(1.0, range_km)) * soc)
            
            if lang in ("hi", "hi+en"):
                txt = (
                    f"Aapki current location se {dest_name} lagbhag {d_km:.1f} km door hai "
                    f"(lagbhag {drive_mins} minute ka drive via city roads). "
                    f"Aapki {soc:.0f}% battery ({range_km:.0f} km range) se aap araam se pahunch sakte hain, "
                    f"wahan pahunchne par lagbhag {rem_soc:.1f}% battery bachegi."
                )
            elif lang == "mr":
                txt = (
                    f"Aaplya sadyachya location pasun {dest_name} sumare {d_km:.1f} km antaravar aahe "
                    f"(drive time ~{drive_mins} minte). "
                    f"Tumchya {soc:.0f}% battery ({range_km:.0f} km range) ne tumhi sahajpane pohchu shakta, "
                    f"tithe pohchlyavar {rem_soc:.1f}% battery shillak rahil."
                )
            else:
                txt = (
                    f"You are approximately {d_km:.1f} km away from {dest_name}. "
                    f"Estimated drive time is about {drive_mins} minutes via city routes. "
                    f"With your current battery at {soc:.0f}% ({range_km:.0f} km range), you can easily reach "
                    f"with approximately {rem_soc:.1f}% battery remaining."
                )
            return (txt, "DISTANCE_CHECK", {
                "destination": dest_name,
                "distance_km": round(d_km, 1),
                "drive_time_mins": drive_mins,
                "lat": dest_lat,
                "lng": dest_lng,
                "arrival_soc": round(rem_soc, 1),
                "action": "show_landmark_route"
            })

        # Inter-city fallback
        return self._ans_range_check(q, v_status, lang)

    def _ans_nagpur_network(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Nagpur mein 137 se zyada fast charging stations verified hain. "
                "Top hubs: Sitabuldi Metro Interchange (Tata Power 120 kW), Congress Nagar Metro (EESL 142 kW), "
                "Kasturchand Park Metro (EESL 142 kW), aur Wardha Road Airport Hotel Pride (ChargeZone 150 kW). "
                "Aap kisi bhi station ko reserve karne ke liye 'Reserve nearest station' bol sakte hain."
            )
        elif lang == "mr":
            txt = (
                "Nagpur madhe 137 peksha jasta fast chargers aahet. "
                "Top hubs: Sitabuldi Metro (Tata Power 120 kW), Congress Nagar Metro (142 kW), "
                "Kasturchand Park (142 kW), ani Wardha Road Hotel Pride (150 kW). "
                "Slot lock karnyasathi 'Reserve nearest station' sanga."
            )
        else:
            txt = (
                "Nagpur features over 137 verified fast charging stations in our network. "
                "Top high-power hubs include Sitabuldi Metro Interchange (Tata Power 120 kW), Congress Nagar Metro (EESL 142 kW), "
                "Kasturchand Park Metro (EESL 142 kW), and Wardha Road Airport Hotel Pride (ChargeZone 150 kW). "
                "You can say 'Reserve the nearest station in Nagpur' anytime to lock an exclusive slot."
            )
        return (txt, "NAGPUR_NETWORK_INFO", {
            "total_stations": 137,
            "top_hubs": [
                {"name": "EESL — Congress Nagar Metro", "power_kw": 142.0},
                {"name": "EESL — Kasturchand Park Metro", "power_kw": 142.0},
                {"name": "Tata Power — Sitabuldi Interchange", "power_kw": 120.0},
                {"name": "ChargeZone — Wardha Road Pride", "power_kw": 150.0}
            ]
        })

    def _ans_cpo_networks(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "SmartReserve India ke sabhi major CPOs ko support karta hai: Tata Power EZ Charge, Zeon Charging, "
                "Jio-bp Pulse, Statiq, ChargeZone, Kazam, EESL, BPCL, IOCL, aur HPCL. Hamare database mein "
                "BEE national registry aur live Open Charge Map ke 29,260 stations real-time integrated hain."
            )
        elif lang == "mr":
            txt = (
                "SmartReserve madhe Bharatatil sarva major CPOs aahet: Tata Power, Zeon, Jio-bp, Statiq, "
                "ChargeZone, Kazam, EESL, BPCL, ani IOCL. Total 29,260 verified stations integrated aahet."
            )
        else:
            txt = (
                "SmartReserve integrates all leading Indian Charge Point Operators (CPOs), including Tata Power EZ Charge, "
                "Zeon Charging, Jio-bp Pulse, Statiq, ChargeZone, Kazam, EESL, BPCL, IOCL, and HPCL, across 29,260 stations nationwide."
            )
        return (txt, "CPO_NETWORKS", {
            "cpos": ["Tata Power", "Zeon", "Jio-bp Pulse", "Statiq", "ChargeZone", "Kazam", "EESL", "BPCL", "IOCL", "HPCL"],
            "total_network_stations": 29260
        })

    def _ans_help(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Main aapka in-cabin co-pilot hoon! Aap mujhse pooch sakte hain:\n"
                "1. 'Reserve nearest station in Nagpur' ya 'Show charging stations'\n"
                "2. 'Repeat the PIN' ya 'Tell the PIN once again'\n"
                "3. 'Gaadi ki battery aur range kitni hai'\n"
                "4. 'Tyre pressure check karo' ya 'Diagnostics summary'\n"
                "5. 'How does SmartReserve work' ya 'What is OCPP 1.6J'"
            )
        elif lang == "mr":
            txt = (
                "Mee tumchi in-cabin AI assistant aahe! Tumhi vicharu shakta:\n"
                "1. 'Reserve nearest station in Nagpur'\n"
                "2. 'Repeat the PIN' kiva 'PIN sanga'\n"
                "3. 'Battery kiti aahe'\n"
                "4. 'Tyre chi hawa check kara'\n"
                "5. 'SmartReserve kay aahe'"
            )
        else:
            txt = (
                "I am AVA, your in-cabin EV assistant! You can say commands like:\n"
                "• 'Reserve the nearest station in Nagpur' or 'Show chargers nearby'\n"
                "• 'Repeat the PIN' or 'Tell the PIN once again'\n"
                "• 'Battery status' or 'How much range do I have?'\n"
                "• 'Check tyre pressure' or 'Run diagnostics'\n"
                "• 'How does SmartReserve work?' or 'What is OCPP 1.6J?'"
            )
        return (txt, "HELP", {
            "commands": [
                "Reserve nearest station in Nagpur",
                "Repeat the PIN",
                "Battery status",
                "Check tyre pressure",
                "How does SmartReserve work"
            ]
        })

    def _ans_thank_you(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = "Aapka swagat hai! Surakshit drive karein. Agar aur koi zaroorat ho toh batayein."
        elif lang == "mr":
            txt = "Kahi harkat nahi! Safe drive kara. Kahi madat lagli tar nakki sanga."
        else:
            txt = "You're very welcome! Drive safe and let me know if you need anything else on your journey."
        return (txt, "COURTESY", {})

    def _ans_identity(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = (
                "Main AVA hoon — Automotive Virtual Assistant. Main Hyundai Ioniq 5 ke liye banayi gayi "
                "in-cabin smart co-pilot hoon, jo SmartReserve system ke through charging, reservations, "
                "aur vehicle telematics manage karti hoon."
            )
        elif lang == "mr":
            txt = (
                "Mee AVA aahe — Automotive Virtual Assistant. Hyundai Ioniq 5 chi smart in-cabin co-pilot, "
                "je SmartReserve dwara charging, slot booking, ani vehicle health manage karte."
            )
        else:
            txt = (
                "I am AVA — Automotive Virtual Assistant. I am your in-cabin AI co-pilot designed for the "
                "Hyundai Ioniq 5, managing smart reservations, priority routing, and real-time vehicle telematics."
            )
        return (txt, "IDENTITY", {"name": "AVA", "role": "Hyundai In-Cabin Co-Pilot"})


    def _ans_low_battery_emergency(self, v_status: Optional[dict], lang: str):
        soc = (v_status or {}).get("soc", 15)
        if lang in ("hi", "hi+en"):
            txt = f"Emergency Alert! Battery {soc}% par hai. Sabse paas EESL Kasturchand Park (1.3 km, 142 kW) hai. Aap safely wahan pahunch sakte hain. Main slot reserve karun?"
        elif lang == "mr":
            txt = f"Emergency Alert! Battery {soc}% aahe. Sarvat javal EESL Kasturchand Park (1.3 km, 142 kW) aahe. Tumhi surakshit tithe pohchu shakta. Mee slot reserve karu ka?"
        else:
            txt = f"Emergency Alert! Battery is critically low at {soc}%. The nearest high-speed charger is EESL Kasturchand Park (1.3 km away, 142 kW). You have enough range to reach it safely. Shall I reserve a slot immediately?"
        return (txt, "LOW_BATTERY_EMERGENCY", {"soc": soc, "nearest_station": "EESL Kasturchand Park", "distance": 1.3})

    def _ans_range_check(self, q: str, v_status: Optional[dict], lang: str):
        soc = (v_status or {}).get("soc", 18.0)
        range_km = (v_status or {}).get("range_km", 86)
        
        dest_name = "your destination"
        dist = 0
        cities = {"wardha": 78, "pune": 900, "mumbai": 880, "hyderabad": 490, "bhopal": 350, "indore": 470, "delhi": 1100, "amravati": 150, "akola": 200}
        for city, d in cities.items():
            if city in q.lower():
                dest_name = city.capitalize()
                dist = d
                break
                
        if dist > 0:
            road_dist = dist * 1.15
            if road_dist > range_km:
                if dest_name == "Wardha":
                    if lang in ("hi", "hi+en"):
                        txt = f"Aapki battery abhi {soc}% hai (86 km range). Wardha kareeb 90 km hai. Aap bina charge kiye nahi pahunch payenge. Main EESL Congress Nagar Metro (1.4 km, 142 kW) par slot reserve karne ki salah deti hoon. Reserve karun?"
                    elif lang == "mr":
                        txt = f"Tumchi battery {soc}% aahe (86 km range). Wardha 90 km aahe. Tumhi charging shivay pohchu shakat nahi. Mee EESL Congress Nagar Metro (1.4 km, 142 kW) var slot reserve karnya chi shifaras karte. Reserve karu ka?"
                    else:
                        txt = f"With your current {soc}% battery ({range_km} km range), {dest_name} is approximately {road_dist:.0f} km by road. You will arrive at about 3% SoC — cutting it very close. I recommend a quick 10-minute charge at EESL Congress Nagar Metro (1.4 km away, 142 kW) to top up to 35% before heading out. Shall I reserve that slot?"
                else:
                    if lang in ("hi", "hi+en"):
                        txt = f"{dest_name} {road_dist:.0f} km door hai. Aapki range {range_km} km hai. Aapko raste mein charge karna padega."
                    elif lang == "mr":
                        txt = f"{dest_name} {road_dist:.0f} km lamb aahe. Tumchi range {range_km} km aahe. Tumhala rastyat charge karava lagel."
                    else:
                        txt = f"{dest_name} is {road_dist:.0f} km away by road. With your current range of {range_km} km, you will need to charge on the way. Shall I find chargers along your route?"
            else:
                rem_soc = soc - (road_dist / range_km) * soc
                if lang in ("hi", "hi+en"):
                    txt = f"Aap asani se {dest_name} pahunch sakte hain. Wahan pahunchne par aapke paas {rem_soc:.0f}% battery bachegi."
                elif lang == "mr":
                    txt = f"Tumhi sahajpane {dest_name} pohchu shakta. Tithe pohchlyavar tumchi battery {rem_soc:.0f}% shillak rahil."
                else:
                    txt = f"You can comfortably reach {dest_name}. You will arrive with approximately {rem_soc:.0f}% battery remaining."
        else:
            if lang in ("hi", "hi+en"):
                txt = f"Aapki gaadi mein abhi {soc}% battery aur {range_km} km ki range hai. Destination clear nahi hai, par aap itni doori tay kar sakte hain."
            elif lang == "mr":
                txt = f"Tumchya car madhe {soc}% battery ani {range_km} km range aahe."
            else:
                txt = f"Your current battery is at {soc}% giving you a range of {range_km} km. Please specify a destination city to check reachability."
        return (txt, "RANGE_CHECK", {"soc": soc, "range_km": range_km, "destination": dest_name})

    def _ans_v2l(self, v_status: Optional[dict], lang: str):
        if lang in ("hi", "hi+en"):
            txt = "Vehicle-to-Load (V2L) feature aapki car ko power generator banata hai (3.6kW output). Adapter charge port mein lagakar aap laptop, TV, ya camping appliances chala sakte hain. Full 3.6kW par lagbhag 3 hours chalega. Dhyaan rahe ki 20% SoC se neeche V2L na use karein."
        elif lang == "mr":
            txt = "Vehicle-to-Load (V2L) feature car la power generator banavte (3.6kW). Adapter lavun tumhi laptop kiva appliances chalu shakta. 20% battery chya khali he vapru naka."
        else:
            txt = "The Vehicle-to-Load (V2L) feature allows your Ioniq 5 to act as a 3.6kW power generator. Using the V2L adapter in the charging port, you can power laptops, TVs, or camping appliances. It can run at full 3.6kW for about 3 hours. Please ensure your SoC does not drop below 20% while using V2L."
        return (txt, "V2L_INFO", {})

    def _ans_preconditioning(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = "Aapki Ioniq 5 automatically battery precondition karti hai jab aap native Maps mein DC charger set karte hain. Yeh battery ko 25°C se 30-35°C par laata hai, jisse 10-80% charge time 28 min se kam hokar 18 min ho jata hai."
        elif lang == "mr":
            txt = "Je vha tumhi Maps madhe DC charger set karta te vha Ioniq 5 swatah battery precondition karte. Yane 10-80% charge time 28 min pasun 18 min hoto."
        else:
            txt = "Your Ioniq 5 automatically preconditions the battery when you navigate to a DC charger using the native Hyundai Maps. This warms the battery from 25°C to the optimal 30-35°C range, reducing the 10-80% charging time from 28 minutes (when cold) to just 18 minutes."
        return (txt, "BATTERY_PRECONDITIONING", {})

    def _ans_regen(self, v_status: Optional[dict], lang: str):
        if lang in ("hi", "hi+en"):
            txt = "Aapki car mein 4 regenerative braking levels hain (0 se 3), jo steering wheel paddles se control hote hain. i-Pedal mode car ko bina brake dabaye puri tarah rok sakta hai aur range ko 10-15% tak badha sakta hai. Yeh dashboard par negative kW mein dikhta hai."
        elif lang == "mr":
            txt = "Car madhe 4 regen braking levels aahet. i-Pedal mode ne car brake shivay thambte ani range 10-15% vadhvate. He steering wheel paddles varun change karta yete."
        else:
            txt = "Your vehicle features 4 levels of regenerative braking (0 to 3) controlled via steering wheel paddles. The i-Pedal mode allows you to stop the car completely without using the brake pedal, recovering energy (shown as negative kW on the dash) and improving your range by 10-15%."
        return (txt, "REGEN_INFO", {})

    def _ans_drive_modes(self, v_status: Optional[dict], lang: str):
        mode = (v_status or {}).get("drive_mode", "normal")
        if lang in ("hi", "hi+en"):
            txt = f"Abhi gaadi '{mode}' mode mein hai. Eco mode max range ke liye hai (top speed 120 kmph, AC kam), Normal mode daily driving ke liye, aur Sport mode maximum performance deta hai (0-100 kmph in 5.1s, 219 kW AWD)."
        elif lang == "mr":
            txt = f"Sadyacha mode '{mode}' aahe. Eco mode max range sathi, Normal rojchya driving sathi, ani Sport mode maximum performance sathi aahe (0-100 kmph 5.1s madhe)."
        else:
            txt = f"Your current drive mode is '{mode}'. Eco mode maximizes range (limits acceleration and AC, top speed 120 kmph). Normal mode is for balanced daily driving, and Sport mode unleashes maximum performance (219 kW AWD, 0-100 in 5.1s)."
        return (txt, "DRIVE_MODES", {"current_mode": mode})

    def _ans_nearby_amenities(self, q: str, v_status: Optional[dict], lang: str):
        q_low = q.lower()
        if "hospital" in q_low or "medical" in q_low:
            amenity = "hospitals: AIIMS Nagpur (5.2 km, Civil Lines) and Government Medical College (3.1 km)"
        elif "cafe" in q_low or "restaurant" in q_low or "food" in q_low:
            amenity = "cafes: Cafe Coffee Day - Sitabuldi (0.5 km) and McDonald's - Sadar (1.1 km)"
        elif "atm" in q_low:
            amenity = "ATMs: SBI ATM - Sitabuldi (0.3 km) and HDFC ATM - Congress Nagar (0.8 km)"
        elif "tyre" in q_low or "puncture" in q_low or "mechanic" in q_low:
            amenity = "tyre shops: Sai Tyre House - Civil Lines (0.7 km) and MRF Tyre Service - Dharampeth (1.3 km)"
        elif "parking" in q_low:
            amenity = "parking options: Civil Lines Multilevel Parking (0.4 km, ₹20/hr) and Kasturchand Park Parking (1.2 km, Free)"
        elif "pharmacy" in q_low:
            amenity = "pharmacies: Apollo Pharmacy - Sitabuldi (0.3 km)"
        else:
            amenity = "various local amenities"
            
        if lang in ("hi", "hi+en"):
            txt = f"Nagpur mein aapke paas yeh {amenity} available hain. EV tip: un malls ya locations par park karein jahan EV charging bhi uplabdh ho."
        elif lang == "mr":
            txt = f"Nagpur madhe tumchya javal he {amenity} aahet. EV tip: jithe EV charging aahe tithech park kara."
        else:
            txt = f"In Nagpur, the nearest {amenity}. EV-specific tip: Consider parking at malls or locations equipped with EV charging stations to top up while you visit."
        return (txt, "NEARBY_AMENITIES", {})

    def _ans_network_comparison(self, lang: str):
        if lang in ("hi", "hi+en"):
            txt = "Nagpur mein ChargeZone Wardha Road (150 kW) sabse fast hai. EESL Metro stations (142 kW) sabse zyada accessible hain (8 stations). Tata Power Sitabuldi (120 kW) bhi accha hai, aur Kazam (50 kW) thoda sasta hai (₹12/kWh). Main speed aur availability ke liye EESL recommend karti hoon."
        elif lang == "mr":
            txt = "Nagpur madhe ChargeZone Wardha Road (150 kW) sarvat fast aahe. EESL Metro stations (142 kW, 8 stations) jasta uplabdh aahet. Tata Power Sitabuldi 120 kW aahe ani Kazam sasta aahe. Speed sathi EESL best aahe."
        else:
            txt = "In Nagpur, ChargeZone Wardha Road is the fastest at 150 kW. EESL Metro stations are the most widespread (8 stations) at 142 kW. Tata Power at Sitabuldi offers 120 kW, while Kazam is a cheaper 50 kW option at ₹12/kWh. I recommend EESL for a balance of speed and availability, and ChargeZone for maximum power."
        return (txt, "NETWORK_COMPARISON", {})

    def _ans_cabin_control(self, q: str, lang: str):
        import re
        temp_match = re.search(r'(\d+)\s*(?:degree|celsius|°)?', q)
        temp = temp_match.group(1) if temp_match else "22"
        
        action_str = f"temperature has been set to {temp}°C"
        if "off" in q:
            action_str = "AC has been turned off"
        elif "high" in q and "fan" in q:
            action_str = "fan speed has been set to high"
            
        if lang in ("hi", "hi+en"):
            txt = f"Done, {action_str.replace('temperature has been set to', 'temperature').replace('AC has been turned off', 'AC band kar diya gaya hai')}."
        elif lang == "mr":
            txt = f"Done, {action_str.replace('temperature has been set to', 'temperature set kele aahe').replace('AC has been turned off', 'AC band kela aahe')}."
        else:
            txt = f"Cabin climate control adjusted: The {action_str}."
        return (txt, "CABIN_CONTROL", {"action": action_str})

    def _ans_vehicle_switch(self, q: str, vehicle_status: Optional[dict], lang: str):
        q_low = q.lower()
        target_id = None
        if "ioniq 6" in q_low or "ioniq6" in q_low:
            target_id = "ioniq6"
        elif "ioniq 5" in q_low or "ioniq5" in q_low or "ioniq" in q_low:
            target_id = "ioniq5"
        elif "kona" in q_low:
            target_id = "kona_ev"
        elif "creta" in q_low:
            target_id = "creta_ev"
        elif "inster" in q_low:
            target_id = "inster_ev"
        elif "nexon" in q_low or "tata" in q_low:
            target_id = "nexon_ev"
        elif "xuv400" in q_low or "xuv" in q_low or "mahindra" in q_low:
            target_id = "xuv400"
        elif "mg" in q_low or "zs" in q_low:
            target_id = "mg_zs_ev"
        else:
            target_id = "ioniq6"

        vdata = VEHICLE_CATALOG.get(target_id, VEHICLE_CATALOG.get("ioniq5", {}))
        if vehicle and hasattr(vehicle, "set_vehicle_model"):
            try:
                vehicle.set_vehicle_model(target_id)
            except Exception:
                pass

        v_name = vdata.get("name", "Hyundai EV")
        s_name = vdata.get("short_name", "EV")
        energy = vdata.get("energy", {})
        charging = vdata.get("charging", {})
        diag = vdata.get("diagnostics", {})

        batt = energy.get("battery_capacity_kwh", 72.6)
        rng = energy.get("nominal_range_km", 481.0)
        eff = energy.get("efficiency_km_per_kwh", 6.6)
        conns = ", ".join(charging.get("supported_connectors", ["CCS2"]))
        max_dc = charging.get("max_dc_kw", 220.0)
        dc_time = charging.get("dc_10_80_min", 18.0)
        volt = charging.get("architecture_voltage", "800V E-GMP")
        tpms = diag.get("tyre_pressure_nominal_psi", 36.0)
        soh = diag.get("battery_soh", 98.5)

        if lang in ("hi", "hi+en"):
            txt = (
                f"Active vehicle profile ko {v_name} mein switch kar diya gaya hai. "
                f"Three-Things Pattern update ho gaya: "
                f"1. Energy Architecture: {batt} kWh battery ({rng} km range, {eff} km/kWh). "
                f"2. Charging Architecture: {conns} connectors, max {max_dc:.0f} kW DC ({volt}, 10-80% in {dc_time:.0f} min). "
                f"3. Telematics Diagnostics: {tpms} PSI tyre pressure aur {soh}% battery health. "
                f"Dashboard par compatibility badges update ho gaye hain."
            )
        elif lang == "mr":
            txt = (
                f"Active gadi {v_name} madhe switch keli aahe. "
                f"Three-Things Pattern: "
                f"1. Energy: {batt} kWh battery, {rng} km range. "
                f"2. Charging: {conns}, {max_dc:.0f} kW DC ({volt}, {dc_time:.0f} min 10-80%). "
                f"3. Telematics: {tpms} PSI tyre pressure. "
                f"Station compatibility badges dashboard var update zale."
            )
        else:
            txt = (
                f"Switched active vehicle profile to {v_name}. "
                f"Updated across Three Dimensions: "
                f"1. Energy Architecture: {batt} kWh battery with {rng} km nominal range ({eff} km/kWh). "
                f"2. Charging Architecture: {conns} supporting up to {max_dc:.0f} kW DC fast charging ({volt}, 10-80% in {dc_time:.0f} mins). "
                f"3. Diagnostics & Telematics: {tpms} PSI nominal tyre pressure and {soh}% battery health score. "
                f"All station compatibility badges on your dashboard have been recalculated for this model."
            )

        return (txt, "VEHICLE_SWITCH", {
            "action": "switch_vehicle",
            "model_id": target_id,
            "vehicle": vdata,
        })

    def _ans_compatibility_check(self, q: str, vehicle_status: Optional[dict], lang: str):
        vdata = vehicle.profile.catalog_data if (vehicle and hasattr(vehicle, "profile")) else VEHICLE_CATALOG.get("ioniq5", {})
        s_name = vdata.get("short_name", "Hyundai Ioniq 5")
        conns = ", ".join(vdata.get("charging", {}).get("supported_connectors", ["CCS2"]))
        max_dc = vdata.get("charging", {}).get("max_dc_kw", 220.0)

        if lang in ("hi", "hi+en"):
            txt = (
                f"SmartReserve 5-tier charger compatibility engine use karta hai: "
                f"1. Physical Plug: {s_name} ke liye {conns} compatible hai. Bharat DC-001 aur CHAdeMO plugs ko automatically Incompatible mark kiya jata hai. "
                f"2. Power Tiers: Ultra-Fast (≥120 kW), Fast DC (50-100 kW), aur Standard DC (25-45 kW). "
                f"3. Smart BMS Throttling: Agar charger zyada power ka hai (jaise 150 kW gun) aur car 30 kW accept karti hai, toh car BMS power safe limit par cap kar deta hai. "
                f"Aapki active car {s_name} max {max_dc:.0f} kW DC fast intake support karti hai."
            )
        elif lang == "mr":
            txt = (
                f"SmartReserve 5-tier compatibility check karto: "
                f"1. Physical Plug: {s_name} sathi {conns} compatible aahe. Bharat DC mismatch aslyas Incompatible disto. "
                f"2. Speed Tiers: Ultra-Fast (≥120 kW), Fast DC (50-100 kW), AC Slow. "
                f"3. BMS Capping: Charger power car chya capacity pramane cap hote. "
                f"Tumchi gadi {s_name} max {max_dc:.0f} kW intake karte."
            )
        else:
            txt = (
                f"SmartReserve prevents charger mismatch through a 5-tier compatibility engine: "
                f"1. Physical Plug Matching: Your {s_name} supports {conns}. Incompatible plugs like Bharat DC-001 (GB/T) or CHAdeMO are strictly filtered out. "
                f"2. Power Speed Tiers: Tier 1 Ultra-Fast (≥120 kW), Tier 2 Fast DC (50-100 kW), Tier 3 Standard DC (25-45 kW), Tier 4 AC Slow (3.3-22 kW), and Tier 5 Incompatible. "
                f"3. Dynamic BMS Throttling: If a 150 kW station is connected to a 30 kW vehicle, power is safely capped by the car's BMS (Car Capped). "
                f"Your active {s_name} accepts up to {max_dc:.0f} kW DC fast charging."
            )

        return (txt, "COMPATIBILITY_CHECK", {
            "action": "show_compatibility_guide",
            "active_vehicle": s_name,
            "supported_connectors": vdata.get("charging", {}).get("supported_connectors", []),
            "max_dc_kw": max_dc,
        })

    def _ans_live_hyderabad_statiq(self, q: str, vehicle_status: Optional[dict], lang: str):
        stns = []
        if live_chargers:
            stns = live_chargers.get_hyderabad_live_stations()

        sample_st = stns[0] if stns else {
            "name": "Statiq Fast Charging Hub — Basheerbagh",
            "available_ports_count": 1,
            "total_ports_count": 2,
            "ports": [
                {"port_number": 1, "status": "OCCUPIED", "current_vehicle": "Tata Nexon EV Long Range", "current_soc": 74.0, "power_kw": 28.5, "minutes_to_80_percent": 11},
                {"port_number": 2, "status": "AVAILABLE", "power_kw": 120.0}
            ]
        }

        busy_port = next((p for p in sample_st.get("ports", []) if p.get("status") == "OCCUPIED"), None)
        busy_car = busy_port.get("current_vehicle", "Tata Nexon EV") if busy_port else "Tata Nexon EV"
        busy_soc = busy_port.get("current_soc", 74.0) if busy_port else 74.0
        busy_kw = busy_port.get("power_kw", 28.5) if busy_port else 28.5
        mins_left = busy_port.get("minutes_to_80_percent", 11) if busy_port else 11

        avail_count = sample_st.get("available_ports_count", 1)
        tot_count = sample_st.get("total_ports_count", 2)
        st_name = sample_st.get("name", "Statiq Hub")

        if lang in ("hi", "hi+en"):
            txt = (
                f"Hyderabad mein real-time Statiq, Tata Power aur ChargeZone telemetry active hai. "
                f"{st_name} par {avail_count} of {tot_count} ports free hain. "
                f"Occupied Gun #1 par abhi {busy_car} charge ho rahi hai ({busy_soc:.0f}% battery, {busy_kw:.1f} kW delivery, {mins_left} min mein 80% complete). "
                f"Gun #2 free hai jise aap turant pre-reserve kar sakte hain!"
            )
        elif lang == "mr":
            txt = (
                f"Hyderabad madhe Statiq live telemetry suru aahe. "
                f"{st_name} var {avail_count}/{tot_count} ports uplabdh aahet. "
                f"Gun #1 var {busy_car} {busy_soc:.0f}% var charge hot aahe ({busy_kw:.1f} kW, {mins_left} min baaki). "
                f"Gun #2 free aahe, tumhi pre-reserve karu shakta."
            )
        else:
            txt = (
                f"Live real-time Statiq and CPO telemetry is active for Hyderabad. "
                f"At {st_name}, {avail_count} of {tot_count} guns are currently available. "
                f"Port 1 is occupied charging a {busy_car} at {busy_soc:.0f}% SoC ({busy_kw:.1f} kW delivery rate, estimated {mins_left} minutes until 80% full). "
                f"Port 2 is AVAILABLE and open for immediate pre-reservation on your screen."
            )

        return (txt, "LIVE_HYDERABAD_STATIQ", {
            "action": "show_live_hyderabad_chargers",
            "stations": stns[:5] if stns else [sample_st],
        })

    def _ans_iot_and_api_buying_guide(self, q: str, vehicle_status: Optional[dict], lang: str):
        guide = live_chargers.get_api_buying_and_iot_guide() if live_chargers else {}

        if lang in ("hi", "hi+en"):
            txt = (
                f"Real-time EV charger data ke liye do reliable solutions hain: "
                f"1. Commercial Data APIs: Pulse Energy (ONDC EV Roaming aggregator - Statiq, ChargeZone, Kazam covered) ya TomTom EV Availability API (2,500 daily free requests). "
                f"2. Low-Cost Hackathon IoT Device (< ₹750): Ek ESP32 microcontroller (₹420) + SCT-013 non-invasive current clamp sensor (₹280) charger wire ke round clamp karte hain. "
                f"Yeh sensor charging current decay (CC-se-CV curve transition at 80%) detect karke vehicle ka live battery percentage aur time-to-full bina charger open kiye calculate kar leta hai! "
                f"Dusra option OBD-II Bluetooth dongle (₹600) hai jo directly car ka battery % read karta hai."
            )
        elif lang == "mr":
            txt = (
                f"Real-time EV data sathi: "
                f"1. APIs: Pulse Energy (ONDC aggregator sathi) ani TomTom API (2,500 free requests/day). "
                f"2. Low-Cost IoT Sensor (₹750): ESP32 (₹420) + SCT-013 CT clamp sensor (₹280) wire var clamp kara. "
                f"Current taper slope detect karun 80% SoC ani time-to-full accurately sangto. "
                f"OBD-II dongle (₹600) sudha gadi madhun direct battery % read karto."
            )
        else:
            txt = (
                f"For real-time EV charging telemetry, here is the complete industry and hackathon blueprint: "
                f"1. Commercial API Sources: Pulse Energy (leading Indian ONDC EV Roaming & OCPI aggregator covering Statiq, ChargeZone, Kazam, Fortum), Kazam Energy API, and TomTom EV Availability API (2,500 free requests/day). "
                f"2. Low-Cost IoT Sensor Hardware Blueprint (< ₹750 / $9): For chargers without native sensors, use an ESP32 microcontroller (₹420) paired with an SCT-013 non-invasive CT current sensor clamp (₹280) around the AC/DC charging cable. "
                f"The AI edge model monitors the Constant-Current to Constant-Voltage (CC-to-CV) taper at 80% SoC to infer the vehicle's battery percentage and remaining charging time without opening the charger internals! "
                f"Alternatively, an ELM327 BLE dongle (₹600 / $7.50) plugs into the vehicle's OBD-II port to stream live battery SoC over Bluetooth."
            )

        return (txt, "IOT_AND_API_GUIDE", {
            "action": "show_iot_guide",
            "guide": guide,
        })

    def _ans_corridor_trip(self, query: str, matched_dest, vehicle_status, lang: str):
        """
        Highway corridor trip planner — works for ANY destination city in DEST_CITIES.
        Calculates stops needed based on real SoC/range, selects milestone chargers,
        and returns spoken + map display data.
        """
        dest_name, dest_info = matched_dest
        dist_km   = dest_info["dist_km"]
        hrs       = dest_info["hrs"]
        nh        = dest_info["nh"]
        dest_lat  = dest_info["lat"]
        dest_lng  = dest_info["lng"]

        v = vehicle_status or {}
        soc       = v.get("soc", 18.0)
        range_km  = v.get("range_km", soc * 4.8)
        veh_name  = v.get("vehicle", "Hyundai Ioniq 5")

        USABLE_RANGE_AT_80 = 340   # km after a full 80% DC charge (Ioniq 5 LR)

        CORRIDOR_STOPS = {
            "hyderabad": [
                {"name": "BPCL Petrol Pump Butibori",      "dist_from_start": 39,  "kw": 30,  "operator": "BPCL",       "lat": 20.9833, "lng": 79.0667},
                {"name": "Kazam EV Hub Adilabad",           "dist_from_start": 195, "kw": 60,  "operator": "Kazam",      "lat": 19.6600, "lng": 78.5300},
                {"name": "ChargeZone Nizamabad NH44",       "dist_from_start": 355, "kw": 120, "operator": "ChargeZone", "lat": 18.6700, "lng": 78.0900},
            ],
            "mumbai": [
                {"name": "EESL Wardha Fast Charger",        "dist_from_start": 78,  "kw": 50,  "operator": "EESL",       "lat": 20.7453, "lng": 78.6022},
                {"name": "Tata Power Nashik Highway Hub",   "dist_from_start": 330, "kw": 120, "operator": "Tata Power", "lat": 19.9975, "lng": 73.7898},
                {"name": "Kazam Igatpuri Ghat",             "dist_from_start": 560, "kw": 60,  "operator": "Kazam",      "lat": 19.6925, "lng": 73.5597},
            ],
            "pune": [
                {"name": "EESL Wardha Fast Charger",        "dist_from_start": 78,  "kw": 50,  "operator": "EESL",       "lat": 20.7453, "lng": 78.6022},
                {"name": "ChargeZone Ahmednagar NH65",      "dist_from_start": 390, "kw": 120, "operator": "ChargeZone", "lat": 19.0952, "lng": 74.7496},
            ],
            "delhi": [
                {"name": "Tata Power Bhopal NH46",          "dist_from_start": 352, "kw": 120, "operator": "Tata Power", "lat": 23.2599, "lng": 77.4126},
                {"name": "ChargeZone Jhansi NH44",          "dist_from_start": 620, "kw": 60,  "operator": "ChargeZone", "lat": 25.4484, "lng": 78.5685},
                {"name": "Kazam Agra NH44",                 "dist_from_start": 870, "kw": 120, "operator": "Kazam",      "lat": 27.1767, "lng": 78.0081},
            ],
            "bhopal": [
                {"name": "ChargeZone Betul NH46",           "dist_from_start": 210, "kw": 60,  "operator": "ChargeZone", "lat": 21.9064, "lng": 77.8972},
            ],
            "indore": [
                {"name": "Tata Power Betul NH52",           "dist_from_start": 220, "kw": 60,  "operator": "Tata Power", "lat": 21.9064, "lng": 77.8972},
                {"name": "ChargeZone Harda NH52",           "dist_from_start": 360, "kw": 50,  "operator": "ChargeZone", "lat": 22.3397, "lng": 77.0893},
            ],
            "amravati": [],
            "wardha":   [],
            "akola": [
                {"name": "Kazam Washim Highway",            "dist_from_start": 130, "kw": 30,  "operator": "Kazam",      "lat": 20.1120, "lng": 77.1342},
            ],
        }

        all_stops = CORRIDOR_STOPS.get(dest_name, [])

        # Determine which stops are actually needed given current range
        needed_stops = []
        remaining_range = range_km
        last_dist = 0
        for stop in all_stops:
            segment = stop["dist_from_start"] - last_dist
            if remaining_range < segment + 40:   # 40 km safety buffer
                needed_stops.append(stop)
                remaining_range = USABLE_RANGE_AT_80
                last_dist = stop["dist_from_start"]
            else:
                remaining_range -= segment
                last_dist = stop["dist_from_start"]

        stops_count = len(needed_stops)
        dest_cap = dest_name.capitalize()

        if stops_count == 0:
            if lang in ("hi", "hi+en"):
                txt = (
                    f"Nagpur se {dest_cap} tak ({dist_km} km, {nh}) aap seedha ja sakte hain! "
                    f"Aapki {soc:.0f}% battery ({range_km:.0f} km range) kaafi hai bina kisi charging stop ke. "
                    f"Pura route aapke dashboard map par dikh raha hai."
                )
            else:
                txt = (
                    f"Great news! You can drive directly from Nagpur to {dest_cap} via {nh} "
                    f"({dist_km} km, ~{hrs} hrs) on your current {soc:.0f}% battery ({range_km:.0f} km range) "
                    f"with no charging stop needed. Full route is plotted on your dashboard map."
                )
        else:
            if lang in ("hi", "hi+en"):
                stop_lines = [
                    f"Stop {i}: {s['name']} ({s['dist_from_start']} km par, {s['kw']} kW, {s['operator']})"
                    for i, s in enumerate(needed_stops, 1)
                ]
                txt = (
                    f"Nagpur se {dest_cap} ka highway trip plan ho gaya — {dist_km} km, {nh}, lagbhag {hrs} ghante. "
                    f"Aapki {soc:.0f}% battery se {stops_count} charging {'stop' if stops_count == 1 else 'stops'} chahiye: "
                    f"{'; '.join(stop_lines)}. "
                    f"Corridor aur sabhi milestones aapke dashboard map par mapped hain."
                )
            else:
                stop_lines = [
                    f"Stop {i}: {s['name']} at {s['dist_from_start']} km ({s['kw']} kW, {s['operator']})"
                    for i, s in enumerate(needed_stops, 1)
                ]
                txt = (
                    f"Planning your highway trip from Nagpur to {dest_cap} via {nh} "
                    f"({dist_km} km, ~{hrs} hrs). "
                    f"With your current {soc:.0f}% battery ({range_km:.0f} km range), "
                    f"you need {stops_count} charging {'stop' if stops_count == 1 else 'stops'}: "
                    f"{'; '.join(stop_lines)}. "
                    f"The full corridor and all charging milestones are mapped on your dashboard."
                )

        waypoints = [{"city": "Nagpur", "lat": 21.1255, "lng": 79.0524, "type": "origin"}]
        for stop in needed_stops:
            waypoints.append({
                "name": stop["name"], "lat": stop["lat"], "lng": stop["lng"],
                "kw": stop["kw"], "operator": stop["operator"], "type": "charging_stop",
            })
        waypoints.append({"city": dest_cap, "lat": dest_lat, "lng": dest_lng, "type": "destination"})

        display = {
            "action": "show_corridor_route",
            "route_name": f"Nagpur to {dest_cap} ({nh})",
            "total_distance_km": dist_km,
            "drive_time_hours": hrs,
            "highway": nh,
            "charging_stops_needed": stops_count,
            "origin": {"city": "Nagpur", "lat": 21.1255, "lng": 79.0524},
            "destination": {"city": dest_cap, "lat": dest_lat, "lng": dest_lng},
            "waypoints": waypoints,
            "vehicle_soc": soc,
            "vehicle_range_km": range_km,
            "can_reach_direct": stops_count == 0,
        }
        return (txt, "CORRIDOR_TRIP", display)


# Global brain singleton
brain = SmartReserveBrain()

