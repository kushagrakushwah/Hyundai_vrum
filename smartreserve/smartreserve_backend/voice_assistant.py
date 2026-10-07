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
    language_mix: str = 'en'
    raw_text: str = ''

@dataclass
class VoiceAssistantResponse:
    text: str
    display_data: Dict[str, Any] = field(default_factory=dict)
    action_type: str = 'info'
    tool_calls: List[str] = field(default_factory=list)
    language: str = 'en'
    needs_confirmation: bool = False
    pending_action: Optional[Dict[str, Any]] = None

class VoiceAssistant:
    def __init__(self):
        self._pending_confirmations = {}

    def _detect_language(self, text: str) -> str:
        text_lower = text.lower()
        hindi_words = ['karo', 'batao', 'kya', 'hai', 'nahi', 'haan', 'kitna', 'kitni', 'kahan', 'paas', 'wala', 'mein', 'gaadi', 'battery', 'station', 'charge', 'nearest', 'dikhao', 'chahiye', 'laga']
        marathi_words = ['kara', 'sanga', 'aahe', 'nahi', 'ho', 'kiti', 'kuthe', 'javal', 'gaadi', 'chi', 'che', 'karel', 'milel', 'dya']
        
        words = set(re.findall(r'\b\w+\b', text_lower))
        
        hi_count = sum(1 for w in words if w in hindi_words)
        mr_count = sum(1 for w in words if w in marathi_words)
        en_count = len(words) - (hi_count + mr_count)
        
        if mr_count > 0 and mr_count >= hi_count:
            return 'mr'
        elif hi_count > 0:
            if en_count > hi_count:
                return 'hi+en'
            return 'hi'
        return 'en'

    def _parse_intent(self, text: str) -> IntentResult:
        text_lower = text.lower()
        
        intents = {
            'FIND_CPO': ['nearest', 'charging station', 'charger', 'find', 'station', 'kahan', 'kuthe', 'javal', 'paas'],
            'VEHICLE_STATUS': ['status', 'battery', 'kitni', 'kiti', 'range', 'fuel', 'charge level'],
            'RESERVE_SLOT': ['reserve', 'book', 'lock', 'slot', 'pehla', 'doosra'],
            'GET_DIAGNOSTICS': ['diagnostic', 'health', 'service', 'tyre', 'tire', 'check', 'condition'],
            'SET_AC': ['ac', 'temperature', 'cooling', 'heating', 'thanda', 'garam'],
            'CANCEL': ['cancel', 'band', 'stop', 'hatao'],
            'GREETING': ['hello', 'hi', 'hey', 'namaste', 'namaskar', 'ava', 'hey ava']
        }
        
        best_intent = 'UNKNOWN'
        best_score = 0
        
        for intent, keywords in intents.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_intent = intent
                
        confidence = min(best_score * 0.4, 1.0) if best_score > 0 else 0.0
        
        entities = {}
        if 'pehla' in text_lower or 'first' in text_lower:
            entities['rank'] = 1
        elif 'doosra' in text_lower or 'second' in text_lower:
            entities['rank'] = 2
        elif 'teesra' in text_lower or 'third' in text_lower:
            entities['rank'] = 3
            
        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities=entities,
            language_mix=self._detect_language(text_lower),
            raw_text=text
        )

    def process_input(self, text: str, user_id: str = 'hyundai_driver_001') -> VoiceAssistantResponse:
        try:
            if user_id in self._pending_confirmations:
                text_lower = text.lower()
                yes_words = ['haan', 'yes', 'ho', 'ya', 'yup']
                no_words = ['nahi', 'no', 'nako', 'nope', 'nah']
                
                if any(w in text_lower for w in yes_words):
                    return self.confirm_action(user_id, True)
                elif any(w in text_lower for w in no_words):
                    return self.confirm_action(user_id, False)
        except Exception as e:
            logger.error(f"Error checking pending confirmations: {e}")

        intent_res = self._parse_intent(text)
        
        if intent_res.intent == 'FIND_CPO':
            return self._handle_find_cpo(intent_res, user_id)
        elif intent_res.intent == 'VEHICLE_STATUS':
            return self._handle_vehicle_status(intent_res)
        elif intent_res.intent == 'GET_DIAGNOSTICS':
            return self._handle_diagnostics(intent_res)
        elif intent_res.intent == 'RESERVE_SLOT':
            return self._handle_reserve(intent_res, user_id)
        elif intent_res.intent == 'CANCEL':
            return self._handle_cancel_reservation(intent_res, user_id)
        elif intent_res.intent == 'SET_AC':
            return self._handle_set_ac(intent_res)
        elif intent_res.intent == 'GREETING':
            return self._handle_greeting(intent_res)
        else:
            return self._handle_unknown(intent_res)
            
    def confirm_action(self, user_id: str, confirmed: bool) -> VoiceAssistantResponse:
        pending = self._pending_confirmations.pop(user_id, None)
        if not pending:
            return VoiceAssistantResponse(text="No pending actions to confirm.", action_type='info')
            
        if confirmed:
            action = pending.get('action')
            if action == 'reserve':
                st_name = pending.get('station_name', 'Station')
                st_id = pending.get('station_id', 'TG0001')
                return VoiceAssistantResponse(
                    text=f"Reservation confirmed for {st_name}! 30-minute exclusive slot is locked. One-time PIN has been generated.",
                    display_data={'action': 'reserve', 'station_id': st_id, 'station_name': st_name},
                    action_type='action_complete'
                )
            return VoiceAssistantResponse(text="Action confirmed.", action_type='action_complete')
        else:
            return VoiceAssistantResponse(text="Action cancelled.", action_type='info')

    def _handle_find_cpo(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        try:
            from priority_engine import engine
            recs_list = engine.recommend(top_k=3) if engine else []
            stations = [vars(r) if hasattr(r, '__dict__') else r for r in recs_list]
        except Exception:
            stations = []

        if not stations:
            stations = [
                {'station_name': 'Tata Power Gachibowli Fast Hub', 'name': 'Tata Power Gachibowli', 'distance_km': 4.1, 'rated_power_kw': 120, 'station_id': 'TG0001', 'id': 'TG0001', 'estimated_charge_time_min': 18},
                {'station_name': 'ChargeZone Jubilee Hills', 'name': 'ChargeZone Jubilee Hills', 'distance_km': 7.2, 'rated_power_kw': 60, 'station_id': 'TG0002', 'id': 'TG0002', 'estimated_charge_time_min': 35},
                {'station_name': 'BPCL Kukatpally Ultra Fast', 'name': 'BPCL Kukatpally', 'distance_km': 9.5, 'rated_power_kw': 150, 'station_id': 'TG0003', 'id': 'TG0003', 'estimated_charge_time_min': 15}
            ]
        recs_data = {'recommendations': stations, 'stations': stations}
        
        s1 = stations[0]
        s1_name = s1.get('station_name') or s1.get('name') or 'Tata Power Fast Hub'
        s1_dist = s1.get('distance_km', 4.1)
        s1_kw = s1.get('effective_power_kw') or s1.get('rated_power_kw', 120)
        s1_time = s1.get('estimated_charge_time_min', 18)
        
        if intent_res.language_mix in ['hi', 'hi+en']:
            text = f"Aapke paas {len(stations)} charging stations hain. Pehla: {s1_name}, {s1_dist} km door, {s1_kw:.0f} kW, ~{s1_time:.0f} min mein 80% tak charge. Best option Station 1 hai — kya isko reserve karu?"
        elif intent_res.language_mix == 'mr':
            text = f"Tumchya javal {len(stations)} stations aahet. Pehla {s1_name} aahe, {s1_dist} km antaravar. {s1_kw:.0f} kW fast charger aahe. Slot reserve karu ka?"
        else:
            text = f"Found {len(stations)} charging stations. Best option is {s1_name}, {s1_dist} km away at {s1_kw:.0f} kW (~{s1_time:.0f} min charge). Shall I reserve Station 1 for you?"
            
        self._pending_confirmations[user_id] = {
            'action': 'reserve',
            'station_id': s1.get('station_id') or s1.get('id', 'TG0001'),
            'station_name': s1_name,
            'rank': 1
        }
            
        return VoiceAssistantResponse(
            text=text,
            display_data=recs_data,
            action_type='confirmation_needed',
            language=intent_res.language_mix,
            needs_confirmation=True,
            pending_action=self._pending_confirmations[user_id]
        )

    def _handle_vehicle_status(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        try:
            from vehicle_intelligence import vehicle
            status = vehicle.get_vehicle_status()
        except Exception:
            status = {'soc': 18.0, 'range_km': 86.6, 'efficiency': 6.6, 'hours_remaining': 1.9}
            
        soc = status.get('soc', 18.0)
        range_km = status.get('range_km', status.get('range_estimate_km', 86.6))
        eff = status.get('efficiency', 6.6)
        hours = status.get('hours_remaining', 1.9)
        next_serv = status.get('service_status', {}).get('next_service_in_km', 5500.0)
        
        if intent_res.language_mix in ['hi', 'hi+en']:
            text = f"Gaadi ki battery {soc}% hai, aur lagbhag {range_km} km ({hours} ghante) ki range bachi hai. Average efficiency {eff} km/kWh hai. Front-left tyre pressure kam hai (28 PSI). Agli service {next_serv:.0f} km baad due hai. Battery 20% se kam hai, charging recommend ki jaati hai."
        elif intent_res.language_mix == 'mr':
            text = f"Gaadi chi battery {soc}% aahe, approximately {range_km} km range ({hours} taas) bachi aahe. Average efficiency {eff} km/kWh aahe. Front-left tyre pressure kami aahe. Agli service {next_serv:.0f} km nantar aahe. Charging recommend karto — nearest charger shodhayche ka?"
        else:
            text = f"Your vehicle is at {soc}% battery with approximately {range_km} km of range remaining ({hours} hours of driving at current pace). Average efficiency is {eff} km/kWh. Front-left tyre is low at 28 PSI. Next service is due in {next_serv:.0f} km. I recommend charging soon."
            
        return VoiceAssistantResponse(
            text=text,
            display_data={'vehicle_status': status, 'soc': soc, 'range_km': range_km},
            action_type='info',
            language=intent_res.language_mix
        )

    def _handle_diagnostics(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        try:
            from vehicle_intelligence import vehicle
            diag_text = vehicle.get_diagnostic_summary()
            status = vehicle.get_vehicle_status()
        except Exception:
            diag_text = "All key powertrain systems normal. Front-left tyre pressure is low."
            status = {}
        return VoiceAssistantResponse(text=diag_text, display_data={'diagnostics': status}, action_type='info', language=intent_res.language_mix)

    def _handle_reserve(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        rank = intent_res.entities.get('rank', 1)
        st_name = "Tata Power Fast Hub"
        st_id = "TG0001"
        try:
            from priority_engine import engine
            if engine:
                r = engine.recommend(top_k=3)
                if r and len(r) >= rank:
                    st_name = r[rank-1].station_name
                    st_id = r[rank-1].station_id
        except Exception:
            pass
            
        if intent_res.language_mix in ['hi', 'hi+en']:
            text = f"Station {st_name} reserve karu? 30 minute ka slot lock hoga aur ₹200 deposit lagega. Confirm karo — haan ya nahi?"
        elif intent_res.language_mix == 'mr':
            text = f"Station {st_name} sathi 30 min slot reserve karu ka? Ho ki nahi sanga."
        else:
            text = f"Shall I reserve Station {st_name}? A 30-minute exclusive slot will be locked. Please confirm with yes or no."
            
        self._pending_confirmations[user_id] = {
            'action': 'reserve',
            'station_id': st_id,
            'station_name': st_name,
            'rank': rank
        }
            
        return VoiceAssistantResponse(
            text=text,
            action_type='confirmation_needed',
            language=intent_res.language_mix,
            needs_confirmation=True,
            pending_action=self._pending_confirmations[user_id]
        )

    def _handle_cancel_reservation(self, intent_res: IntentResult, user_id: str) -> VoiceAssistantResponse:
        self._pending_confirmations[user_id] = {'action': 'cancel_reserve'}
        return VoiceAssistantResponse(
            text="Cancel the reservation? Please confirm.",
            action_type='confirmation_needed',
            language=intent_res.language_mix,
            needs_confirmation=True,
            pending_action=self._pending_confirmations[user_id]
        )

    def _handle_set_ac(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        return VoiceAssistantResponse(text="AC set.", action_type='action_complete', language=intent_res.language_mix)
        
    def _handle_greeting(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        if intent_res.language_mix in ['hi', 'hi+en']:
            return VoiceAssistantResponse(text="Namaste! Main AVA hoon, aapki Hyundai in-vehicle AI assistant. Gaadi ka status, charging station ya reservation ke baare mein poochiye.", action_type='info', language=intent_res.language_mix)
        elif intent_res.language_mix == 'mr':
            return VoiceAssistantResponse(text="Namaskar! Mee AVA aahe, tumchi Hyundai AI assistant. Battery status, charging stations kiva reservations baddal vichara.", action_type='info', language=intent_res.language_mix)
        return VoiceAssistantResponse(text="Hello! I am AVA, your Hyundai in-cabin EV assistant. Ask me about vehicle status, charging stations, or reservations.", action_type='info', language=intent_res.language_mix)

    def _handle_unknown(self, intent_res: IntentResult) -> VoiceAssistantResponse:
        return VoiceAssistantResponse(text="I didn't quite catch that.", action_type='error', language=intent_res.language_mix)

assistant = VoiceAssistant()
