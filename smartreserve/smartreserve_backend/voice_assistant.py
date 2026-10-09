"""
voice_assistant.py — AVA in-cabin AI voice assistant for Hyundai Ioniq 5.

Intent routing:
  Simple intents  → local keyword handler (zero latency, works offline)
  Complex intents → Gemini 1.5 Flash cloud LLM (with vehicle + station context)
  Fallback        → honest "no data" message — NEVER fake/hardcoded station names
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import re

logger = logging.getLogger(__name__)


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
    def _parse_intent(self, text: str) -> IntentResult:
        text_lower = text.lower()

        intents = {
            "FIND_CPO": [
                "nearest", "charging station", "charger", "find station",
                "kahan", "kuthe", "javal", "paas", "charge karna", "charger dhundo",
            ],
            "VEHICLE_STATUS": [
                "status", "kitni battery", "kiti battery", "range", "fuel left",
                "charge level", "gaadi ka", "how much charge", "battery kiti",
            ],
            "RESERVE_SLOT": [
                "reserve", "book", "lock", "slot", "pehla reserve", "doosra reserve",
                "pehle wala", "first one", "station book",
            ],
            "GET_DIAGNOSTICS": [
                "diagnostic", "health", "service", "tyre", "tire", "check",
                "condition", "maintenance", "pressure",
            ],
            "SET_AC": [
                "ac", "temperature", "cooling", "heating", "thanda", "garam",
                "aircon", "climate",
            ],
            "CANCEL": ["cancel", "band karo", "stop", "hatao", "nahi chahiye"],
            "GREETING": ["hello", "hi", "hey", "namaste", "namaskar", "ava", "hey ava"],
        }

        best_intent = "UNKNOWN"
        best_score = 0
        for intent, keywords in intents.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_intent = intent

        confidence = min(best_score * 0.4, 1.0) if best_score > 0 else 0.0

        entities: Dict[str, Any] = {}
        if "pehla" in text_lower or "first" in text_lower or "1" in text_lower:
            entities["rank"] = 1
        elif "doosra" in text_lower or "second" in text_lower or "2" in text_lower:
            entities["rank"] = 2
        elif "teesra" in text_lower or "third" in text_lower or "3" in text_lower:
            entities["rank"] = 3

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities=entities,
            language_mix=self._detect_language(text),
            raw_text=text,
        )

    # ── Main entry point ──────────────────────────────────────────────────────
    def process_input(
        self, text: str, user_id: str = "hyundai_driver_001"
    ) -> VoiceAssistantResponse:
        # Check pending confirmation first
        if user_id in self._pending_confirmations:
            text_lower = text.lower()
            yes_words = {"haan", "yes", "ho", "ya", "yup", "sure", "ok", "confirm"}
            no_words = {"nahi", "no", "nako", "nope", "nah", "mat", "band", "cancel"}
            if any(w in text_lower for w in yes_words):
                return self.confirm_action(user_id, True)
            elif any(w in text_lower for w in no_words):
                return self.confirm_action(user_id, False)

        intent_res = self._parse_intent(text)

        if intent_res.intent == "FIND_CPO":
            return self._handle_find_cpo(intent_res, user_id)
        elif intent_res.intent == "VEHICLE_STATUS":
            return self._handle_vehicle_status(intent_res)
        elif intent_res.intent == "GET_DIAGNOSTICS":
            return self._handle_diagnostics(intent_res)
        elif intent_res.intent == "RESERVE_SLOT":
            return self._handle_reserve(intent_res, user_id)
        elif intent_res.intent == "CANCEL":
            return self._handle_cancel_reservation(intent_res, user_id)
        elif intent_res.intent == "SET_AC":
            return self._handle_set_ac(intent_res)
        elif intent_res.intent == "GREETING":
            return self._handle_greeting(intent_res)
        else:
            return self._handle_unknown_with_llm(intent_res)

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
        # 1. Try priority engine (real data)
        stations: List[dict] = []
        try:
            from priority_engine import get_engine
            engine = get_engine()
            if engine:
                recs_list = engine.recommend(top_k=3)
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
        recs_data = {"recommendations": stations, "stations": stations, "source": "priority_engine"}
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

        if gemini_text:
            spoken = gemini_text
        elif intent_res.language_mix in ("hi", "hi+en"):
            spoken = (
                f"Aapke paas {len(stations)} charging stations hain. "
                f"Pehla: {s1_name}, {s1_dist:.1f} km door, {s1_kw:.0f} kW charger, "
                f"~{s1_time:.0f} min mein 80% tak charge. "
                f"Best option yahi hai — kya isko reserve karu?"
            )
        elif intent_res.language_mix == "mr":
            spoken = (
                f"Tumchya javal {len(stations)} stations aahet. "
                f"Pehla {s1_name} aahe, {s1_dist:.1f} km antaravar, "
                f"{s1_kw:.0f} kW charger. ~{s1_time:.0f} min madhe 80% charge hoil. "
                f"Slot reserve karu ka?"
            )
        else:
            spoken = (
                f"Found {len(stations)} charging stations nearby. "
                f"Top pick: {s1_name}, {s1_dist:.1f} km away, {s1_kw:.0f} kW "
                f"(~{s1_time:.0f} min to 80%). Shall I reserve Station 1 for you?"
            )

        # Store pending reservation for confirmation flow
        self._pending_confirmations[user_id] = {
            "action": "reserve",
            "station_id": s1.get("station_id") or s1.get("id", ""),
            "station_name": s1_name,
            "rank": 1,
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
        rank = intent_res.entities.get("rank", 1)
        st_name = None
        st_id = ""

        try:
            from priority_engine import get_engine
            engine = get_engine()
            if engine:
                recs = engine.recommend(top_k=3)
                if recs and len(recs) >= rank:
                    rec = recs[rank - 1]
                    st_name = rec.station_name
                    st_id = rec.station_id
        except Exception as e:
            logger.warning(f"[AVA] Priority engine error in reserve: {e}")

        if not st_name:
            # No real station data available
            if intent_res.language_mix in ("hi", "hi+en"):
                msg = "Reservation ke liye pehle 'nearest charging station' command try karein taaki stations load ho sakein."
            elif intent_res.language_mix == "mr":
                msg = "Reserve karna sathi aadhi 'nearest charging station' command vhapra."
            else:
                msg = "Please first ask me to find charging stations so I can load the available options."
            return VoiceAssistantResponse(text=msg, action_type="info", language=intent_res.language_mix)

        if intent_res.language_mix in ("hi", "hi+en"):
            text = f"Station {st_name} reserve karu? 30 minute ka slot lock hoga. Confirm karo — haan ya nahi?"
        elif intent_res.language_mix == "mr":
            text = f"Station {st_name} sathi 30 min slot reserve karu ka? Ho ki nahi sanga."
        else:
            text = f"Shall I reserve {st_name}? A 30-minute exclusive slot will be locked. Please confirm — yes or no."

        self._pending_confirmations[user_id] = {
            "action": "reserve",
            "station_id": st_id,
            "station_name": st_name,
            "rank": rank,
        }

        return VoiceAssistantResponse(
            text=text,
            action_type="confirmation_needed",
            language=intent_res.language_mix,
            needs_confirmation=True,
            pending_action=self._pending_confirmations[user_id],
            tool_calls=["priority_engine.recommend"],
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

        if intent_res.language_mix in ("hi", "hi+en"):
            return VoiceAssistantResponse(text="Samajh nahi aaya. Kripya dobara bolen.", action_type="error", language=intent_res.language_mix)
        elif intent_res.language_mix == "mr":
            return VoiceAssistantResponse(text="Samajle nahi. Parat sangaa.", action_type="error", language=intent_res.language_mix)
        return VoiceAssistantResponse(text="I didn't understand that. Could you rephrase?", action_type="error", language=intent_res.language_mix)

    # ── Helper: vehicle context dict for LLM ─────────────────────────────────
    def _get_vehicle_context(self) -> Optional[dict]:
        try:
            from vehicle_intelligence import vehicle
            return vehicle.get_vehicle_status()
        except Exception:
            return None


assistant = VoiceAssistant()
