"""
voice_assistant.py — AVA in-cabin AI voice assistant for Hyundai Ioniq 5.

Intent routing:
  Simple intents  → local keyword handler (zero latency, works offline)
  Complex intents → Gemini 1.5 Flash cloud LLM (with vehicle + station context)
  Fallback        → honest "no data" message — NEVER fake/hardcoded station names
"""

import logging
import os
import time
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import re
from smartreserve_brain import brain

logger = logging.getLogger(__name__)

INDIAN_CITIES = {
    "nagpur": (21.1458, 79.0882),
    "hyderabad": (17.3850, 78.4867),
    "pune": (18.5204, 73.8567),
    "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090),
    "bhopal": (23.2599, 77.4126),
    "indore": (22.7196, 75.8577),
    "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639),
    "jaipur": (26.9124, 75.7873),
    "ahmedabad": (23.0225, 72.5714),
    "lucknow": (26.8467, 80.9462),
    "chandigarh": (30.7333, 76.7794),
    "surat": (21.1702, 72.8311),
    "visakhapatnam": (17.6868, 83.2185),
    "vizag": (17.6868, 83.2185),
    "wardha": (20.7453, 78.6022),
    "amravati": (20.9374, 77.7796),
}

def format_spoken_pin(pin: Any) -> str:
    """Format PIN digit-by-digit with both pauses and words so TTS engines never read it as lakhs/hundreds."""
    pin_str = str(pin).strip()
    words = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
    }
    digits = [c for c in pin_str if c.isdigit()]
    if not digits:
        return pin_str
    comma_digits = ", ".join(digits)
    word_digits = ", ".join(words.get(d, d) for d in digits)
    return f"{comma_digits} ({word_digits})"

def get_active_reservations_sync(user_id: Optional[str] = None) -> List[dict]:
    """Read active reservations synchronously from SQLite."""
    db_path = os.path.join(os.path.dirname(__file__), 'smartreserve.db')
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        now = time.time()
        if user_id:
            cur.execute("SELECT * FROM reservations WHERE expires_at > ? AND user_id = ? ORDER BY created_at DESC", (now, user_id))
        else:
            cur.execute("SELECT * FROM reservations WHERE expires_at > ? ORDER BY created_at DESC", (now,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"Error querying active reservations: {e}")
        return []


@dataclass
class IntentResult:
    intent: str
    confidence: float
    entities: Dict[str, Any] = field(default_factory=dict)
    language_mix: str = "en"
    raw_text: str = ""


@dataclass
class VoiceAssistantResponse:
    text: str
    display_data: Dict[str, Any] = field(default_factory=dict)
    action_type: str = "info"
    tool_calls: List[str] = field(default_factory=list)
    language: str = "en"
    needs_confirmation: bool = False
    pending_action: Optional[Dict[str, Any]] = None


class VoiceAssistant:
    def __init__(self):
        self._pending_confirmations: Dict[str, dict] = {}

    # ── Language detection ────────────────────────────────────────────────────
    def _detect_language(self, text: str) -> str:
        text_lower = text.lower()
        hindi_words = {
            "karo", "batao", "kya", "hai", "nahi", "haan", "kitna", "kitni",
            "kahan", "paas", "wala", "mein", "gaadi", "dikhao", "chahiye",
            "laga", "bolo", "kar", "karu", "karunga", "dalo",
        }
        marathi_words = {
            "kara", "sanga", "aahe", "nahi", "ho", "kiti", "kuthe", "javal",
            "chi", "che", "karel", "milel", "dya", "sangto", "shodhayche",
        }
        words = set(re.findall(r"\b\w+\b", text_lower))
        hi_count = sum(1 for w in words if w in hindi_words)
        mr_count = sum(1 for w in words if w in marathi_words)
        en_count = len(words) - (hi_count + mr_count)

        if mr_count > 0 and mr_count >= hi_count:
            return "mr"
        elif hi_count > 0:
            return "hi+en" if en_count > hi_count else "hi"
        return "en"

    # ── Intent parsing ────────────────────────────────────────────────────────
    # ── Intent parsing ────────────────────────────────────────────────────────
    def _parse_intent(self, text: str, selected_station_id: Optional[str] = None) -> IntentResult:
        text_lower = text.lower().strip()

        # Strip common wake words
        wake_words = ["hey ava", "ok ava", "ava", "hey hyundai", "ok hyundai", "hello ava", "hi ava"]
        cleaned_text = text_lower
        for ww in wake_words:
            if cleaned_text.startswith(ww):
                cleaned_text = cleaned_text[len(ww):].strip(",. ")
                break

        entities: Dict[str, Any] = {}

        # 1. Cancellation check
        cancel_kws = ["cancel", "band karo", "stop", "hatao", "nahi chahiye", "ruk jao", "cancel reservation"]
        if any(kw in cleaned_text for kw in cancel_kws):
            return IntentResult(
                intent="CANCEL",
                confidence=0.95,
                raw_text=text,
                language_mix=self._detect_language(text)
            )

        # 2. PIN repeat / query check
        repeat_pin_kws = [
            "repeat the pin", "repeat pin", "repeat my pin", "tell the pin",
            "tell me the pin", "tell the pin once again", "tell pin once again",
            "tell the pin again", "say the pin again", "say pin again", "pin again", "repeat otp",
            "what is my pin", "what's my pin", "what is the pin", "what's the pin",
            "get pin", "my pin", "show pin", "reservation pin", "kiosk pin",
            "pin code", "pin kya hai", "pin batao", "pin bolo", "dobara pin", "pin dobara",
            "pin repeat karo", "pin repeat", "pin sanga", "punha pin sanga",
            "pin once again", "can you repeat the pin", "can you tell the pin",
            "ek baar aur pin", "firse pin", "pin firse", "pin punha", "mera pin"
        ]
        has_pin_kw = any(kw in cleaned_text for kw in repeat_pin_kws)
        if not has_pin_kw and ("pin" in cleaned_text or "otp" in cleaned_text):
            query_actions = ["batao", "bolo", "sanga", "dobara", "firse", "again", "repeat", "kya", "what", "tell", "say", "show", "punha", "ek baar"]
            if any(qa in cleaned_text for qa in query_actions):
                has_pin_kw = True

        if has_pin_kw:
            return IntentResult(
                intent="REPEAT_PIN",
                confidence=0.98,
                entities=entities,
                raw_text=text,
                language_mix=self._detect_language(text),
            )

        # 3. Extract city if mentioned in text
        for city_name, coords in INDIAN_CITIES.items():
            if re.search(r'\b' + re.escape(city_name) + r'\b', cleaned_text):
                entities["city"] = city_name
                entities["user_lat"], entities["user_lon"] = coords
                try:
                    from vehicle_intelligence import vehicle
                    if vehicle and hasattr(vehicle, 'set_location'):
                        vehicle.set_location(coords[0], coords[1], city_name.capitalize())
                except Exception:
                    pass
                break

        # Fuzzy station name lookup from query
        _KNOWN_STATION_LANDMARKS = [
            ("congress nagar", "EESL — Congress Nagar Metro Station"),
            ("kasturchand", "EESL — Kasturchand Park Metro Station"),
            ("sitabuldi", "Tata Power — Sitabuldi Interchange"),
            ("wardha road", "ChargeZone — Wardha Road Hotel Pride"),
            ("airport", "ChargeZone — Airport Hotel Pride Nagpur"),
            ("jhansi rani", "EESL — Jhansi Rani Square Metro Station"),
            ("chhatrapati", "EESL — Chhatrapati Sq Metro Station"),
            ("zero mile", "EESL — Zero Mile Metro Station"),
            ("rahate colony", "EESL — Rahate Colony Metro Station"),
            ("lad colony", "EESL — Lad Colony Metro Station"),
        ]
        for landmark, full_name in _KNOWN_STATION_LANDMARKS:
            if landmark in cleaned_text:
                entities['station_landmark'] = landmark
                entities['station_name_hint'] = full_name
                break

        # 4. Browsing vs Reservation check
        is_question = cleaned_text.startswith(("what", "how", "why", "explain", "about", "who")) or any(
            cleaned_text.startswith(p) for p in ["can you explain", "tell me about", "what is", "how does", "why do"]
        )

        has_reserve_verb = bool(re.search(r'\b(reserve|book|lock|rok do|booking)\b', cleaned_text))
        is_reserve = has_reserve_verb and not is_question

        # Check if user refers to currently selected station
        if any(w in cleaned_text for w in ["this", "selected", "current", "yeh", "ye", "ye wala", "yeh wala", "it"]):
            entities["use_selected"] = True
            entities["selected_station_id"] = selected_station_id
        elif selected_station_id and is_reserve and not any(w in cleaned_text for w in ["nearest", "paas", "closest", "first", "pehla", "second", "doosra", "third", "teesra"]):
            entities["use_selected"] = True
            entities["selected_station_id"] = selected_station_id

        # Extract rank / nearest
        if any(w in cleaned_text for w in ["nearest", "paas", "closest", "first", "pehla", "pehle", "1", "one"]):
            entities["rank"] = 1
        elif any(w in cleaned_text for w in ["second", "doosra", "doosre", "2", "two"]):
            entities["rank"] = 2
        elif any(w in cleaned_text for w in ["third", "teesra", "teesre", "3", "three"]):
            entities["rank"] = 3
        else:
            entities["rank"] = 1

        # Extract operator if mentioned
        known_cpos = [
            "tata power", "reil", "iocl", "bpcl", "hpcl", "chargezone",
            "statiq", "zeon", "e-fill", "ather", "kazam", "relux",
            "fortum", "jio-bp", "jio bp", "magenta", "glida", "adani", "bolt"
        ]
        for cpo in known_cpos:
            if cpo in cleaned_text:
                entities["operator"] = cpo
                break

        # Extract station ID if mentioned
        id_match = re.search(r'\b(chg_\d+|tg\d+|ocm-\d+|df_[a-z0-9_]+|in_[a-z0-9_]+)\b', cleaned_text)
        if id_match:
            entities["station_id"] = id_match.group(1).upper()

        # Extract potential station name or location query from text
        if is_reserve:
            q_text = cleaned_text
            stop_words = [
                "reserve", "book", "lock", "slot", "slots", "the", "a", "an", "this", "that", "these",
                "it", "station", "stations", "charger", "chargers", "kardo", "karo", "please", "at", "wala", "mein",
                "for", "to", "my", "me", "yeh", "ye", "current", "selected", "one", "kar", "do",
                "in", "at", "near", "can", "you", "could", "would", "want", "like", "need", "get", "charging",
                "fast", "slow", "some", "any", "nearby", "here", "search", "searching", "find", "finding",
                "am", "i", "and", "look", "looking", "located", "live", "coz", "because"
            ]
            for kw in stop_words:
                q_text = re.sub(r'\b' + re.escape(kw) + r'\b', '', q_text)
            for c_name in INDIAN_CITIES.keys():
                q_text = re.sub(r'\b' + re.escape(c_name) + r'\b', '', q_text)
            q_text = re.sub(r'\s+', ' ', q_text).strip()
            if len(q_text) >= 3 and q_text not in ["nearest", "closest", "first", "second", "third", "pehla", "doosra", "teesra", "paas", "sabse paas"]:
                entities["target_query"] = q_text

            return IntentResult(
                intent="RESERVE_SLOT",
                confidence=0.95,
                entities=entities,
                language_mix=self._detect_language(text),
                raw_text=text,
            )

        # General intent dictionary for discovery, vehicle status, AC, etc.
        intents = {
            "NEARBY_AMENITIES": [
                "hospital", "cafe", "restaurant", "food", "atm", "pharmacy", "medical store", "mechanic", "tyre shop", "puncture shop",
                "nearest parking", "parking nearby", "parking", "car park"
            ],
            "BATTERY_PRECONDITIONING": [
                "battery preconditioning", "pre-conditioning", "preconditioning", "precondition", "warm up battery", "prepare battery"
            ],
            "NETWORK_COMPARISON": [
                "which charger is best", "vs", "which network is better", "best charging network", "compare charger", "fastest"
            ],
            "FIND_CPO": [
                "nearest", "charging station", "charger", "find station", "see", "show", "view",
                "slot", "slots", "kahan", "kuthe", "javal", "paas", "charge karna", "charger dhundo",
                "stations nearby", "ev station", "stations", "dikhao", "dekho", "dekhna",
            ],
            "VEHICLE_STATUS": [
                "status", "kitni battery", "kiti battery", "range", "fuel left",
                "charge level", "gaadi ka", "how much charge", "battery kiti",
                "battery status", "battery level", "battery percentage",
            ],
            "GET_DIAGNOSTICS": [
                "diagnostic", "health", "service", "tyre", "tire", "check",
                "condition", "maintenance", "pressure", "tpms",
            ],
            "SET_AC": [
                "ac", "temperature", "cooling", "heating", "thanda", "garam",
                "aircon", "climate",
            ],
            "LOCATION_QUERY": [
                "where am i", "which city", "what city", "current city", "city am i in",
                "my location", "current location", "where are we", "kahan hoon", "kuthe aaho",
                "meri location", "shahar kaunsa", "konte shahar", "tell me my location",
                "exact location", "gps location", "in which city", "which city i am"
            ],
            "FIND_PETROL_PUMP": [
                "petrol pump", "petrol", "fuel station", "gas station", "fuel pump", "diesel",
                "petrol pump kahan", "petrol pump kuthe", "petrol bunk", "gas bunk"
            ],
            "CHARGING_COST": [
                "cost of", "tariff of", "how much does it cost", "charging cost", "cost to charge",
                "rate per kwh", "kharcha kitna", "charge kiti lagel", "tata power cost", "tata power tariff"
            ],
            "DESTINATION_RANGE": [
                "can i reach", "can we reach", "will i make it", "enough range", "will battery last",
                "kya main pahunch", "range enough", "pohochta ka",
            ],
            "CABIN_CONTROL": [
                "set ac", "set temperature", "ac on", "ac off", "turn on ac", "thanda karo", "garam karo", "climate",
            ],
            "GREETING": ["hello", "hi", "hey", "namaste", "namaskar", "ava", "hey ava"],
        }

        best_intent = "UNKNOWN"
        best_score = 0
        for intent, keywords in intents.items():
            score = 0
            for kw in keywords:
                if " " in kw:
                    if kw in cleaned_text:
                        score += 2
                else:
                    if re.search(r'\b' + re.escape(kw) + r'\b', cleaned_text):
                        score += 1
            if score > best_score:
                best_score = score
                best_intent = intent

        confidence = min(best_score * 0.4, 1.0) if best_score > 0 else 0.0

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities=entities,
            language_mix=self._detect_language(text),
            raw_text=text,
        )

    # ── Main entry point ──────────────────────────────────────────────────────
    def process_input(
        self,
        text: str,
        user_id: str = "hyundai_driver_001",
        selected_station_id: Optional[str] = None,
        city: Optional[str] = None,
        user_lat: Optional[float] = None,
        user_lon: Optional[float] = None
    ) -> VoiceAssistantResponse:
        # Check pending confirmation first using whole-word tokens to avoid false matches (e.g. 'sure' in 'pressure')
        if user_id in self._pending_confirmations:
            words = set(re.findall(r"\b[a-zA-Z0-9_\'-]+\b", text.lower()))
            yes_words = {"haan", "yes", "ho", "ya", "yup", "sure", "ok", "okay", "confirm", "karo", "lock", "book"}
            no_words = {"nahi", "no", "nako", "nope", "nah", "mat", "band", "cancel"}
            if words.intersection(yes_words) and not words.intersection(no_words):
                return self.confirm_action(user_id, True)
            elif words.intersection(no_words) and not words.intersection(yes_words):
                return self.confirm_action(user_id, False)

        intent_res = self._parse_intent(text, selected_station_id=selected_station_id)

        # Merge city / coordinates if passed explicitly and not extracted from text
        if city and not intent_res.entities.get("city"):
            c_norm = city.lower().strip()
            intent_res.entities["city"] = c_norm
            if c_norm in INDIAN_CITIES:
                intent_res.entities["user_lat"], intent_res.entities["user_lon"] = INDIAN_CITIES[c_norm]
        if user_lat is not None and user_lon is not None and not intent_res.entities.get("user_lat"):
            intent_res.entities["user_lat"] = float(user_lat)
            intent_res.entities["user_lon"] = float(user_lon)

        if intent_res.entities.get("station_landmark") and intent_res.intent in ("FIND_CPO", "RESERVE_SLOT"):
            intent_res.entities["target_query"] = intent_res.entities["station_name_hint"]

        # 1. Primary Action Handlers: Repeat PIN, Cancel, AC, Booking, and Finding Chargers
        if intent_res.intent == "REPEAT_PIN":
            return self._handle_repeat_pin(intent_res, user_id)
        elif intent_res.intent == "CANCEL":
            return self._handle_cancel_reservation(intent_res, user_id)
        elif intent_res.intent == "SET_AC":
            return self._handle_set_ac(intent_res)
        elif intent_res.intent == "RESERVE_SLOT":
            return self._handle_reserve(intent_res, user_id)
        elif intent_res.intent == "FIND_CPO":
            return self._handle_find_cpo(intent_res, user_id)
        elif intent_res.intent == "VEHICLE_STATUS":
            return self._handle_vehicle_status(intent_res)
        elif intent_res.intent == "GET_DIAGNOSTICS":
            return self._handle_diagnostics(intent_res)
        elif intent_res.intent == "DESTINATION_RANGE":
            return self._handle_destination_range(intent_res, user_id)
        elif intent_res.intent == "CABIN_CONTROL":
            return self._handle_cabin_control(intent_res)

        # 2. Deep Cognitive Brain (Location, Petrol Pumps, Tariffs, Architecture, Telematics)
        brain_ans = brain.answer_query(text, vehicle_status=self._get_vehicle_context(), language=intent_res.language_mix)
        if brain_ans:
            spoken_txt, intent_name, display_d = brain_ans
            return VoiceAssistantResponse(
                text=spoken_txt,
                display_data=display_d,
                action_type="info",
                language=intent_res.language_mix,
                tool_calls=[f"brain.{intent_name.lower()}"]
            )

        if intent_res.intent == "GREETING":
            return self._handle_greeting(intent_res)
        else:
            return self._handle_unknown_with_llm(intent_res)

    # ── REPEAT_PIN handler ────────────────────────────────────────────────────
    def _handle_repeat_pin(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        active = get_active_reservations_sync(user_id)
        if not active:
            active = get_active_reservations_sync()

        if not active:
            if intent_res.language_mix in ("hi", "hi+en"):
                msg = "Aapka koi active reservation PIN nahi mila. Kripya pehle charging station reserve karein."
            elif intent_res.language_mix == "mr":
                msg = "Tumcha kontahi active reservation PIN nahi. Kripya aadhi charging slot reserve kara."
            else:
                msg = "You do not have an active reservation PIN right now. Would you like me to find and reserve a charger for you?"
            return VoiceAssistantResponse(text=msg, action_type="info", language=intent_res.language_mix)

        resv = active[0]
        pin = str(resv.get("pin", ""))
        station_id = resv.get("station_id", "")
        expires_at = float(resv.get("expires_at", 0))
        now = time.time()
        mins_left = max(1, int((expires_at - now) / 60))

        st_name = "your reserved charger"
        try:
            from priority_engine import get_engine
            engine = get_engine()
            if engine and hasattr(engine, 'stations_db'):
                st_obj = engine.stations_db.get_station(station_id)
                if st_obj:
                    st_name = st_obj.get("name", st_name)
        except Exception:
            pass

        spoken_pin = format_spoken_pin(pin)

        if intent_res.language_mix in ("hi", "hi+en"):
            spoken = (
                f"Aapka active reservation PIN hai: {spoken_pin}. "
                f"Station: {st_name}. Yeh slot agle {mins_left} minute tak valid hai."
            )
        elif intent_res.language_mix == "mr":
            spoken = (
                f"Tumcha active reservation PIN aahe: {spoken_pin}. "
                f"Station: {st_name}. Ha slot pudhil {mins_left} minte valid aahe."
            )
        else:
            spoken = (
                f"Your active reservation PIN is {spoken_pin}. "
                f"Reserved at {st_name}. It remains valid for another {mins_left} minutes."
            )

        return VoiceAssistantResponse(
            text=spoken,
            display_data={
                "action": "show_pin",
                "pin": pin,
                "station_id": station_id,
                "station_name": st_name,
                "expires_at": expires_at,
                "minutes_remaining": mins_left,
            },
            action_type="info",
            language=intent_res.language_mix,
            tool_calls=["database.get_active_reservations"],
        )

    # ── Confirmation flow ─────────────────────────────────────────────────────
    def confirm_action(self, user_id: str, confirmed: bool) -> VoiceAssistantResponse:
        pending = self._pending_confirmations.pop(user_id, None)
        if not pending:
            return VoiceAssistantResponse(
                text="No pending action to confirm.", action_type="info"
            )
        if confirmed:
            action = pending.get("action")
            if action == "reserve":
                st_name = pending.get("station_name", "the selected station")
                st_id = pending.get("station_id", "")
                return VoiceAssistantResponse(
                    text=f"Reservation confirmed for {st_name}. 30-minute exclusive slot is locked. Your PIN will appear on screen.",
                    display_data={
                        "action": "reserve",
                        "station_id": st_id,
                        "station_name": st_name,
                    },
                    action_type="action_complete",
                )
            return VoiceAssistantResponse(text="Action confirmed.", action_type="action_complete")
        else:
            return VoiceAssistantResponse(text="Action cancelled.", action_type="info")

    # ── FIND_CPO handler ──────────────────────────────────────────────────────
    def _handle_find_cpo(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        city = intent_res.entities.get("city")
        user_lat = intent_res.entities.get("user_lat")
        user_lon = intent_res.entities.get("user_lon")

        # 1. Try priority engine (real data)
        stations: List[dict] = []
        try:
            from priority_engine import get_engine
            engine = get_engine()
            if engine:
                recs_list = engine.recommend(top_k=3, city=city, user_lat=user_lat, user_lon=user_lon)
                stations = [vars(r) if hasattr(r, "__dict__") else r for r in recs_list]
        except Exception as e:
            logger.warning(f"[AVA] Priority engine error: {e}")

        # 2. If no real results — use Gemini or honest message, never fake data
        if not stations:
            llm_text = self._ask_gemini_for_cpo(intent_res)
            return VoiceAssistantResponse(
                text=llm_text,
                display_data={"recommendations": [], "source": "llm_only"},
                action_type="info",
                language=intent_res.language_mix,
                tool_calls=["priority_engine.recommend", "llm_service.ask_gemini"],
            )

        # 3. Build spoken response from real data
        recs_data = {"recommendations": stations, "stations": stations, "source": "priority_engine", "city": city}
        s1 = stations[0]
        s1_name = s1.get("station_name") or s1.get("name") or "the nearest station"
        s1_dist = s1.get("distance_km", 0)
        s1_kw = s1.get("effective_power_kw") or s1.get("rated_power_kw") or s1.get("power_kw", 0)
        s1_time = s1.get("estimated_charge_time_min", 0)

        # Try Gemini for richer spoken response if available
        gemini_text = None
        try:
            from llm_service import ask_gemini, is_available as llm_ok
            if llm_ok():
                v_ctx = self._get_vehicle_context()
                st_ctx = [
                    {
                        "rank": i + 1,
                        "name": s.get("station_name") or s.get("name"),
                        "distance_km": s.get("distance_km"),
                        "power_kw": s.get("effective_power_kw") or s.get("rated_power_kw"),
                        "charge_time_min": s.get("estimated_charge_time_min"),
                        "label": s.get("label", ""),
                        "reasoning": s.get("reasoning", ""),
                    }
                    for i, s in enumerate(stations)
                ]
                gemini_text = ask_gemini(
                    intent_res.raw_text,
                    vehicle_context=v_ctx,
                    station_context=st_ctx,
                    language_mix=intent_res.language_mix,
                )
        except Exception as e:
            logger.debug(f"[AVA] Gemini enrichment skipped: {e}")

        loc_str = f" in {city.capitalize()}" if city else " nearby"
        if gemini_text:
            spoken = gemini_text
        elif intent_res.language_mix in ("hi", "hi+en"):
            spoken = (
                f"Aapke paas {loc_str.strip()} {len(stations)} charging stations hain. "
                f"Pehla: {s1_name}, {s1_dist:.1f} km door, {s1_kw:.0f} kW charger, "
                f"~{s1_time:.0f} min mein 80% tak charge. "
                f"Best option yahi hai — kya isko reserve karu?"
            )
        elif intent_res.language_mix == "mr":
            spoken = (
                f"Tumchya javal {loc_str.strip()} {len(stations)} stations aahet. "
                f"Pehla {s1_name} aahe, {s1_dist:.1f} km antaravar, "
                f"{s1_kw:.0f} kW charger. ~{s1_time:.0f} min madhe 80% charge hoil. "
                f"Slot reserve karu ka?"
            )
        else:
            spoken = (
                f"Found {len(stations)} charging stations{loc_str}. "
                f"Top pick: {s1_name}, {s1_dist:.1f} km away, {s1_kw:.0f} kW "
                f"(~{s1_time:.0f} min to 80%). Shall I reserve Station 1 for you?"
            )

        # Store pending reservation for confirmation flow
        self._pending_confirmations[user_id] = {
            "action": "reserve",
            "station_id": s1.get("station_id") or s1.get("id", ""),
            "station_name": s1_name,
            "rank": 1,
            "city": city,
        }

        return VoiceAssistantResponse(
            text=spoken,
            display_data=recs_data,
            action_type="confirmation_needed",
            language=intent_res.language_mix,
            needs_confirmation=True,
            pending_action=self._pending_confirmations[user_id],
            tool_calls=["priority_engine.recommend"],
        )

    def _ask_gemini_for_cpo(self, intent_res: IntentResult) -> str:
        """Ask Gemini when priority engine has no results (network issue etc.)."""
        try:
            from llm_service import ask_gemini, is_available as llm_ok
            if llm_ok():
                v_ctx = self._get_vehicle_context()
                resp = ask_gemini(
                    intent_res.raw_text,
                    vehicle_context=v_ctx,
                    language_mix=intent_res.language_mix,
                )
                if resp:
                    return resp
        except Exception as e:
            logger.warning(f"[AVA] Gemini CPO fallback failed: {e}")

        # Last resort: honest message
        if intent_res.language_mix in ("hi", "hi+en"):
            return "Abhi station data load nahi ho paaya. Kripya network check karein aur dobara try karein."
        elif intent_res.language_mix == "mr":
            return "Station data load honya madhe adale. Network check kara ani parat try kara."
        return "Could not load station data right now. Please check your connection and try again."

    # ── VEHICLE_STATUS handler ────────────────────────────────────────────────
    def _handle_vehicle_status(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        try:
            from vehicle_intelligence import vehicle
            status = vehicle.get_vehicle_status()
        except Exception as e:
            logger.error(f"[AVA] Vehicle intelligence error: {e}")
            return VoiceAssistantResponse(
                text="Vehicle data is currently unavailable.",
                action_type="error",
                language=intent_res.language_mix,
            )

        soc = status.get("soc", 0)
        range_km = status.get("range_km") or status.get("range_estimate_km", 0)
        eff = status.get("efficiency", 0)
        hours = round(status.get("hours_remaining", 0), 1)
        next_serv = status.get("service_status", {}).get("next_service_in_km", 0)
        insights = status.get("insights", [])

        # Optionally enrich with Gemini
        gemini_text = None
        try:
            from llm_service import ask_gemini, is_available as llm_ok
            if llm_ok():
                gemini_text = ask_gemini(
                    intent_res.raw_text,
                    vehicle_context=status,
                    language_mix=intent_res.language_mix,
                )
        except Exception:
            pass

        if gemini_text:
            spoken = gemini_text
        elif intent_res.language_mix in ("hi", "hi+en"):
            spoken = (
                f"Gaadi ki battery {soc:.0f}% hai, lagbhag {range_km:.0f} km "
                f"({hours} ghante) ki range bachi hai. "
                f"Average efficiency {eff:.1f} km/kWh hai. "
                f"Agli service {next_serv:.0f} km baad due hai."
            )
        elif intent_res.language_mix == "mr":
            spoken = (
                f"Gaadi chi battery {soc:.0f}% aahe, approximately {range_km:.0f} km "
                f"range ({hours} taas) bachi aahe. "
                f"Average efficiency {eff:.1f} km/kWh aahe. "
                f"Agli service {next_serv:.0f} km nantar aahe."
            )
        else:
            spoken = (
                f"Your vehicle is at {soc:.0f}% battery with approximately "
                f"{range_km:.0f} km of range ({hours} hours at current pace). "
                f"Average efficiency is {eff:.1f} km/kWh. "
                f"Next service due in {next_serv:.0f} km."
            )
            if insights:
                spoken += " " + insights[0]

        return VoiceAssistantResponse(
            text=spoken,
            display_data={"vehicle_status": status, "soc": soc, "range_km": range_km},
            action_type="info",
            language=intent_res.language_mix,
            tool_calls=["vehicle.get_vehicle_status"],
        )

    # ── DIAGNOSTICS handler ───────────────────────────────────────────────────
    def _handle_diagnostics(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        try:
            from vehicle_intelligence import vehicle
            diag_text = vehicle.get_diagnostic_summary()
            status = vehicle.get_vehicle_status()
        except Exception as e:
            logger.error(f"[AVA] Diagnostics error: {e}")
            return VoiceAssistantResponse(
                text="Diagnostics data is currently unavailable.",
                action_type="error",
                language=intent_res.language_mix,
            )

        return VoiceAssistantResponse(
            text=diag_text,
            display_data={"diagnostics": status},
            action_type="info",
            language=intent_res.language_mix,
            tool_calls=["vehicle.get_diagnostic_summary"],
        )

    # ── RESERVE handler ───────────────────────────────────────────────────────
    def _handle_reserve(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        city = intent_res.entities.get("city")
        user_lat = intent_res.entities.get("user_lat")
        user_lon = intent_res.entities.get("user_lon")
        rank = intent_res.entities.get("rank", 1)
        target_operator = intent_res.entities.get("operator")
        target_station_id = intent_res.entities.get("station_id")
        use_selected = intent_res.entities.get("use_selected", False)
        selected_station_id = intent_res.entities.get("selected_station_id")
        target_query = intent_res.entities.get("target_query")

        st_name = None
        st_id = ""
        st_dist = 0.0
        st_kw = 60.0

        try:
            from priority_engine import get_engine
            engine = get_engine()
            if engine:
                recs = engine.recommend(top_k=15, city=city, user_lat=user_lat, user_lon=user_lon)
                
                # 1. Match by selected station on screen if requested
                if use_selected and selected_station_id:
                    matched = next((r for r in recs if (getattr(r, 'station_id', '') or '').upper() == selected_station_id.upper()), None)
                    if matched:
                        st_name = matched.station_name
                        st_id = matched.station_id
                        st_dist = matched.distance_km
                        st_kw = matched.effective_power_kw
                    elif hasattr(engine, 'stations_db'):
                        st_obj = engine.stations_db.get_station(selected_station_id)
                        if st_obj:
                            st_name = st_obj.get("name")
                            st_id = selected_station_id
                            st_dist = 1.0
                            st_kw = float(st_obj.get("power_kw", 60.0))
                    if not st_name:
                        st_id = selected_station_id
                        st_name = f"Station {selected_station_id}"
                        st_dist = 1.0
                        st_kw = 60.0

                # 2. Match by explicit station ID if provided
                if not st_name and target_station_id:
                    matched = next((r for r in recs if (getattr(r, 'station_id', '') or '').upper() == target_station_id), None)
                    if not matched and hasattr(engine, 'stations_db'):
                        st_obj = engine.stations_db.get_station(target_station_id)
                        if st_obj:
                            st_name = st_obj.get("name")
                            st_id = target_station_id
                            st_dist = 2.0
                            st_kw = float(st_obj.get("power_kw", 60.0))
                    elif matched:
                        st_name = matched.station_name
                        st_id = matched.station_id
                        st_dist = matched.distance_km
                        st_kw = matched.effective_power_kw
                        
                # 3. Match by operator if requested (e.g. "reserve Tata Power")
                elif not st_name and target_operator:
                    matched = next((r for r in recs if target_operator.lower() in (getattr(r, 'operator', '') or '').lower() or target_operator.lower() in (getattr(r, 'station_name', '') or '').lower()), None)
                    if not matched and hasattr(engine, 'stations_db'):
                        for sid, sobj in engine.stations_db.stations.items():
                            s_op = sobj.get("operator", "").lower()
                            s_nm = sobj.get("name", "").lower()
                            if target_operator.lower() in s_op or target_operator.lower() in s_nm:
                                st_name = sobj.get("name")
                                st_id = sid
                                st_dist = 2.5
                                st_kw = float(sobj.get("power_kw", 60.0))
                                break
                    elif matched:
                        st_name = matched.station_name
                        st_id = matched.station_id
                        st_dist = matched.distance_km
                        st_kw = matched.effective_power_kw

                # 4. Match by name or location query (e.g. "reserve Gachibowli", "reserve Novotel")
                elif not st_name and target_query:
                    matched = next((r for r in recs if target_query.lower() in (getattr(r, 'station_name', '') or '').lower() or target_query.lower() in (getattr(r, 'address', '') or '').lower()), None)
                    if not matched and hasattr(engine, 'stations_db'):
                        for sid, sobj in engine.stations_db.stations.items():
                            s_name = sobj.get("name", "").lower()
                            s_addr = sobj.get("address", "").lower()
                            s_city = sobj.get("city", "").lower()
                            s_distr = sobj.get("district", "").lower()
                            if target_query.lower() in s_name or target_query.lower() in s_addr or target_query.lower() in s_city or target_query.lower() in s_distr:
                                st_name = sobj.get("name")
                                st_id = sid
                                st_dist = 2.5
                                st_kw = float(sobj.get("power_kw", 60.0))
                                break
                    elif matched:
                        st_name = matched.station_name
                        st_id = matched.station_id
                        st_dist = matched.distance_km
                        st_kw = matched.effective_power_kw

                # 4b. Match by station name hint (landmark-based fuzzy search)
                if not st_name and intent_res.entities.get('station_name_hint'):
                    hint = intent_res.entities['station_name_hint'].lower()
                    for sid, sobj in (engine.stations_db.stations if hasattr(engine, 'stations_db') else {}).items():
                        s_nm = (sobj.get('name') or '').lower()
                        if hint[:15] in s_nm or any(w in s_nm for w in hint.split()[:3]):
                            st_name = sobj.get('name')
                            st_id = sid
                            st_dist = 1.5
                            st_kw = float(sobj.get('power_kw', 60.0))
                            break

                # 5. Match by rank / nearest
                if not st_name and recs:
                    pick_idx = min(len(recs) - 1, max(0, rank - 1))
                    rec = recs[pick_idx]
                    st_name = rec.station_name
                    st_id = rec.station_id
                    st_dist = rec.distance_km
                    st_kw = rec.effective_power_kw
        except Exception as e:
            logger.warning(f"[AVA] Priority engine error in reserve: {e}")

        if not st_name:
            if intent_res.language_mix in ("hi", "hi+en"):
                msg = "Reservation ke liye koi charging station nahi mila. Kripya dobara try karein."
            elif intent_res.language_mix == "mr":
                msg = "Reservation sathi station sapadle nahi. Parat try kara."
            else:
                msg = "Could not locate a suitable charging station to reserve. Please try again."
            return VoiceAssistantResponse(text=msg, action_type="info", language=intent_res.language_mix)

        # Clear any previous pending confirmation since this is an immediate reservation action
        self._pending_confirmations.pop(user_id, None)

        # Build immediate voice reservation response
        loc_str = f" in {city.capitalize()}" if city else ""
        if intent_res.language_mix in ("hi", "hi+en"):
            text = f"Station {st_name}{loc_str} ({st_dist:.1f} km door, {st_kw:.0f} kW) ke liye 30-minute slot lock kar diya hai."
        elif intent_res.language_mix == "mr":
            text = f"Station {st_name}{loc_str} ({st_dist:.1f} km, {st_kw:.0f} kW) sathi 30 min exclusive slot book kela aahe."
        else:
            text = f"Locked exclusive 30-minute slot at {st_name}{loc_str} ({st_dist:.1f} km away, {st_kw:.0f} kW)."

        return VoiceAssistantResponse(
            text=text,
            display_data={
                "action": "reserve",
                "station_id": st_id,
                "station_name": st_name,
                "rank": rank,
                "distance_km": st_dist,
                "power_kw": st_kw,
                "city": city,
            },
            action_type="action_complete",
            language=intent_res.language_mix,
            needs_confirmation=False,
            pending_action=None,
            tool_calls=["charging.reserve_slot", "priority_engine.recommend"],
        )

    # ── CANCEL handler ────────────────────────────────────────────────────────
    def _handle_cancel_reservation(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        self._pending_confirmations.pop(user_id, None)
        if intent_res.language_mix in ("hi", "hi+en"):
            return VoiceAssistantResponse(
                text="Theek hai, koi action cancel kar diya.", action_type="info", language=intent_res.language_mix
            )
        elif intent_res.language_mix == "mr":
            return VoiceAssistantResponse(
                text="Theek aahe, action cancel kela.", action_type="info", language=intent_res.language_mix
            )
        return VoiceAssistantResponse(text="Understood, action cancelled.", action_type="info", language=intent_res.language_mix)

    # ── SET_AC handler ────────────────────────────────────────────────────────
    def _handle_set_ac(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        if intent_res.language_mix in ("hi", "hi+en"):
            return VoiceAssistantResponse(
                text="AC adjust kar diya.", action_type="action_complete", language=intent_res.language_mix
            )
        return VoiceAssistantResponse(text="AC adjusted.", action_type="action_complete", language=intent_res.language_mix)

    # ── GREETING handler ──────────────────────────────────────────────────────
    def _handle_greeting(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        if intent_res.language_mix in ("hi", "hi+en"):
            msg = "Namaste! Main AVA hoon, aapki Hyundai in-vehicle AI assistant. Gaadi ka status, charging station ya reservation ke baare mein poochiye."
        elif intent_res.language_mix == "mr":
            msg = "Namaskar! Mee AVA aahe, tumchi Hyundai AI assistant. Battery status, charging stations kiva reservations baddal vichara."
        else:
            msg = "Hello! I am AVA, your Hyundai in-cabin EV assistant. Ask me about vehicle status, charging stations, or reservations."
        return VoiceAssistantResponse(text=msg, action_type="info", language=intent_res.language_mix)

    # ── UNKNOWN with Gemini fallback ──────────────────────────────────────────
    def _handle_unknown_with_llm(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        try:
            from llm_service import ask_gemini, is_available as llm_ok
            if llm_ok():
                v_ctx = self._get_vehicle_context()
                resp = ask_gemini(
                    intent_res.raw_text,
                    vehicle_context=v_ctx,
                    language_mix=intent_res.language_mix,
                )
                if resp:
                    return VoiceAssistantResponse(
                        text=resp,
                        action_type="info",
                        language=intent_res.language_mix,
                        tool_calls=["llm_service.ask_gemini"],
                    )
        except Exception as e:
            logger.warning(f"[AVA] Gemini unknown handler failed: {e}")

        # Check brain fallback if not already captured
        brain_ans = brain.answer_query(intent_res.raw_text, vehicle_status=self._get_vehicle_context(), language=intent_res.language_mix)
        if brain_ans:
            spoken_txt, intent_name, display_d = brain_ans
            return VoiceAssistantResponse(
                text=spoken_txt,
                display_data=display_d,
                action_type="info",
                language=intent_res.language_mix,
                tool_calls=[f"brain.{intent_name.lower()}"]
            )

        if intent_res.language_mix in ("hi", "hi+en"):
            return VoiceAssistantResponse(
                text="Aap pooch sakte hain: nearest charging station dhundna, slot reserve karna, battery status, tyre pressure ya SmartReserve project ke baare mein.",
                action_type="info",
                language=intent_res.language_mix
            )
        elif intent_res.language_mix == "mr":
            return VoiceAssistantResponse(
                text="Tumhi vicharu shakta: charging station shodha, slot reserve kara, battery status, kiva tyre chi hawa.",
                action_type="info",
                language=intent_res.language_mix
            )
        return VoiceAssistantResponse(
            text="I can assist with: finding charging stations, reserving a 30-minute slot, checking battery range, tyre pressure, or explaining the SmartReserve system.",
            action_type="info",
            language=intent_res.language_mix
        )

    def _handle_destination_range(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        v_status = self._get_vehicle_context()
        brain_ans = brain.answer_query(intent_res.raw_text, vehicle_status=v_status, language=intent_res.language_mix)
        if brain_ans:
            spoken_txt, intent_name, display_d = brain_ans
            return VoiceAssistantResponse(
                text=spoken_txt,
                display_data=display_d,
                action_type="info",
                language=intent_res.language_mix,
                tool_calls=[f"brain.{intent_name.lower()}"]
            )
        return self._handle_unknown_with_llm(intent_res)

    def _handle_cabin_control(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        v_status = self._get_vehicle_context()
        brain_ans = brain.answer_query(intent_res.raw_text, vehicle_status=v_status, language=intent_res.language_mix)
        if brain_ans:
            spoken_txt, intent_name, display_d = brain_ans
            return VoiceAssistantResponse(
                text=spoken_txt,
                display_data=display_d,
                action_type="action_complete",
                language=intent_res.language_mix,
                tool_calls=[f"brain.{intent_name.lower()}"]
            )
        return self._handle_set_ac(intent_res)

    # ── Helper: vehicle context dict for LLM ─────────────────────────────────
    def _get_vehicle_context(self) -> Optional[dict]:
        try:
            from vehicle_intelligence import vehicle
            return vehicle.get_vehicle_status()
        except Exception:
            return None


assistant = VoiceAssistant()
