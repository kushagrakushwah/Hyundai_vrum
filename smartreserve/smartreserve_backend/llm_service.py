"""
llm_service.py — Gemini Cloud LLM + local keyword router for AVA voice assistant.

Routing logic:
  Simple commands (AC, "battery kitni?") → local keyword handler (zero latency)
  Complex reasoning (trip planning, multi-step CPO analysis, Hinglish/Marathi)
  → Gemini 1.5 Flash (fast, cheap, multilingual)
  Offline / no signal → graceful degradation with local handler
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Load .env — file is at repo root, two levels above this module ────────────
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    load_dotenv(dotenv_path=_env_path)
except ImportError:
    pass  # dotenv optional

GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

# ── Lazy Gemini model singleton ───────────────────────────────────────────────
_gemini_model = None

_SYSTEM_PROMPT = """You are AVA, the AI voice assistant embedded in a Hyundai Ioniq 5.
You help the driver with:
- EV charging station recommendations and reservations
- Vehicle status, diagnostics, and range estimation
- Trip planning with charging stops
- Vehicle comfort controls (AC, ambient lighting, seat heating)

Rules:
1. Respond in the SAME language mix as the user (English, Hindi, Marathi, Hinglish).
2. Be concise — responses are read aloud. Max 3 sentences for simple queries.
3. For charging recommendations always mention: station name, distance, power kW, estimated charge time.
4. NEVER fabricate station names or data. If you lack tool data say: "Let me check live stations."
5. Safety: NEVER suggest changes to steering, brakes, or powertrain.
6. When the user wants to reserve — always ask for confirmation first.
7. Respond as a calm, helpful co-pilot. Avoid emojis in spoken text.

Vehicle context and station recommendations will be injected as JSON before each query.
"""


def _init_gemini():
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model
    if not GEMINI_API_KEY:
        logger.warning("[LLM] GEMINI_API_KEY not set — cloud reasoning disabled.")
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-3.8-flash",
            generation_config={
                "temperature": 0.3,
                "top_p": 0.9,
                "max_output_tokens": 1024,
            },
            system_instruction=_SYSTEM_PROMPT,
        )
        logger.info("[LLM] Gemini 3.8 Flash initialized.")
        return _gemini_model
    except Exception as e:
        logger.error(f"[LLM] Gemini init failed: {e}")
        return None


# ── Intent routing ────────────────────────────────────────────────────────────
_SIMPLE_INTENTS = {"VEHICLE_STATUS", "BATTERY_QUERY", "GREETING", "SET_AC", "CANCEL", "GET_DIAGNOSTICS"}
_COMPLEX_INTENTS = {"FIND_CPO", "RESERVE_SLOT", "TRIP_PLANNING", "UNKNOWN"}


def needs_cloud_llm(intent: str, text: str) -> bool:
    """Return True if this query should be routed to Gemini."""
    if intent in _COMPLEX_INTENTS:
        return True
    if len(text.split()) > 12:
        return True
    return False


def ask_gemini(
    user_text: str,
    vehicle_context: Optional[dict] = None,
    station_context: Optional[list] = None,
    language_mix: str = "en",
) -> Optional[str]:
    """
    Send a query to Gemini with vehicle + station context injected.
    Returns the model's text response, or None if unavailable (offline / no key).
    """
    model = _init_gemini()
    if model is None:
        return None

    import json
    context_parts = []
    if vehicle_context:
        context_parts.append(f"<vehicle_status>\n{json.dumps(vehicle_context, indent=2)}\n</vehicle_status>")
    if station_context:
        top3 = station_context[:3]
        context_parts.append(
            f"<charging_recommendations>\n{json.dumps(top3, indent=2)}\n</charging_recommendations>"
        )

    context_block = "\n".join(context_parts)
    full_prompt = f"{context_block}\n\nUser: {user_text}" if context_block else f"User: {user_text}"

    try:
        response = model.generate_content(full_prompt)
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                text_parts = [p.text for p in candidate.content.parts if hasattr(p, "text") and p.text]
                if text_parts:
                    return " ".join(text_parts).strip()
        return response.text.strip()
    except Exception as e:
        logger.error(f"[LLM] Gemini call failed: {e}")
        return None


def is_available() -> bool:
    """Check if Gemini API key is configured."""
    return bool(GEMINI_API_KEY)
