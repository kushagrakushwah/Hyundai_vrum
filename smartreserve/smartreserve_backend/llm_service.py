"""
llm_service.py — Gemini Cloud LLM reasoning engine for AVA voice assistant.

Capabilities:
- Answers ANY general knowledge, technical, driving, science, or chitchat question.
- Injects real-time vehicle telematics (Ioniq 5 AWD, SoC, range, GPS) so answers are vehicle-aware.
- Multilingual reasoning: English, Hindi, Hinglish, Marathi.
- Dual execution: Direct REST API (zero dependencies, 100% reliable) + google.generativeai SDK fallback.
"""

import os
import json
import logging
import urllib.request
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

# ── Load .env from multiple paths ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    for _path in [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    ]:
        if os.path.exists(_path):
            load_dotenv(dotenv_path=_path)
            break
except ImportError:
    pass

GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

_SYSTEM_PROMPT = """You are AVA (Automotive Virtual Assistant), the intelligent in-cabin AI co-pilot in a Hyundai Ioniq 5 EV.
You can answer ANY question — both general knowledge (science, history, trivia, travel, life) and vehicle/EV queries.

Rules:
1. Respond in the SAME language mix as the user (English, Hindi, Marathi, Hinglish).
2. Be concise and friendly — your answers are read aloud over the car audio system (max 2-4 spoken sentences).
3. Sound like a knowledgeable, calm, futuristic automotive co-pilot.
4. Avoid markdown asterisks, hashtags, or emojis in text because they get read aloud awkwardly by the TTS engine.
5. If the user asks about the car or charging, use the injected vehicle context.
"""

# Supported model names verified working with Gemini API
WORKING_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]

def _call_gemini_rest(prompt: str) -> Optional[str]:
    """Call Gemini REST API directly using standard library urllib — zero extra dependencies."""
    if not GEMINI_API_KEY:
        return None

    for model_name in WORKING_MODELS:
        clean_model = model_name.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "system_instruction": {
                "parts": [{"text": _SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.5,
                "topP": 0.9,
                "maxOutputTokens": 300,
            }
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            text = parts[0]["text"].strip()
                            # Clean out markdown formatting for natural TTS speech
                            text = text.replace("*", "").replace("#", "").replace("`", "")
                            return text
        except Exception as e:
            logger.debug(f"[LLM] REST model {clean_model} attempt failed: {e}")
            continue

    return None


def ask_gemini(
    user_text: str,
    vehicle_context: Optional[dict] = None,
    station_context: Optional[list] = None,
    language_mix: str = "en",
) -> Optional[str]:
    """
    Send ANY query to Gemini LLM with live vehicle and station context.
    Returns the natural spoken answer, or None if network fails.
    """
    if not GEMINI_API_KEY:
        return None

    context_parts = []
    if vehicle_context:
        v_summary = {
            "car": vehicle_context.get("vehicle", "Hyundai Ioniq 5 AWD"),
            "battery_soc": f"{vehicle_context.get('soc', 18)}%",
            "range_km": f"{vehicle_context.get('range_km', 87)} km",
            "city": vehicle_context.get("location", {}).get("city", "Nagpur"),
            "drive_mode": vehicle_context.get("drive_mode", "normal"),
        }
        context_parts.append(f"[Current Vehicle Telematics: {json.dumps(v_summary)}]")
    if station_context:
        top2 = [{"name": s.get("name"), "kw": s.get("power_kw"), "dist": s.get("distance_km")} for s in station_context[:2]]
        context_parts.append(f"[Nearby Chargers: {json.dumps(top2)}]")

    full_prompt = ("\n".join(context_parts) + f"\nUser Question: {user_text}") if context_parts else user_text

    # Primary: Fast, resilient REST call
    res = _call_gemini_rest(full_prompt)
    if res:
        return res

    # Secondary: Try SDK if available
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        for m in WORKING_MODELS:
            try:
                model = genai.GenerativeModel(m, system_instruction=_SYSTEM_PROMPT)
                response = model.generate_content(full_prompt)
                if response and response.text:
                    return response.text.strip().replace("*", "").replace("#", "")
            except Exception:
                continue
    except Exception as e:
        logger.error(f"[LLM] SDK call failed: {e}")

    return None


def is_available() -> bool:
    """Check if Gemini API key is configured."""
    return bool(GEMINI_API_KEY)
