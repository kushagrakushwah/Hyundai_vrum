import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    import requests
    HAS_HTTPX = False

logger = logging.getLogger(__name__)

@dataclass
class SpeechConfig:
    sarvam_api_key: Optional[str] = field(default_factory=lambda: os.environ.get('SARVAM_API_KEY'))
    default_language: str = 'hi-IN'
    supported_languages: List[str] = field(default_factory=lambda: ['en-IN', 'hi-IN', 'mr-IN'])
    tts_voice: str = 'meera'  # Sarvam Bulbul voice
    stt_model: str = 'saarika:v2'  # Sarvam Saarika
    enable_wake_word: bool = True
    wake_words: List[str] = field(default_factory=lambda: ['hey ava', 'ava', 'ok ava', 'hey hyundai', 'hyundai'])

class SpeechService:
    def __init__(self):
        self.config = SpeechConfig()
        
    def _get_headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {}
        if self.config.sarvam_api_key:
            headers['api-subscription-key'] = self.config.sarvam_api_key
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    async def transcribe(self, audio_bytes: bytes, language: str = 'hi-IN') -> dict:
        if not self.config.sarvam_api_key:
            logger.warning("Sarvam API key not set, transcription unavailable")
            return {"text": "", "language": language, "confidence": 0.0}
            
        url = "https://api.sarvam.ai/speech-to-text"
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {"language_code": language, "model": self.config.stt_model}
        
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url, 
                        headers=self._get_headers(), 
                        files=files,
                        data=data
                    )
                    response.raise_for_status()
                    result = response.json()
            else:
                response = requests.post(
                    url, 
                    headers=self._get_headers(), 
                    files=files,
                    data=data
                )
                response.raise_for_status()
                result = response.json()
                
            return {
                "text": result.get("transcript", ""),
                "language": result.get("language_code", language),
                "confidence": 1.0
            }
        except Exception as e:
            logger.error(f"Error in transcription: {e}")
            return {"text": "", "language": language, "confidence": 0.0}

    async def synthesize(self, text: str, language: str = 'hi-IN') -> Optional[bytes]:
        if not self.config.sarvam_api_key:
            logger.warning("Sarvam API key not set, synthesis unavailable")
            return None
            
        url = "https://api.sarvam.ai/text-to-speech"
        payload = {
            "inputs": [text],
            "target_language_code": language,
            "speaker": self.config.tts_voice,
            "model": "bulbul:v1"
        }
        
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=self._get_headers('application/json'),
                        json=payload
                    )
                    response.raise_for_status()
                    result = response.json()
            else:
                response = requests.post(
                    url,
                    headers=self._get_headers('application/json'),
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
            import base64
            if "audios" in result and result["audios"]:
                return base64.b64decode(result["audios"][0])
            return None
        except Exception as e:
            logger.error(f"Error in synthesis: {e}")
            return None

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not self.config.sarvam_api_key:
            logger.warning("Sarvam API key not set, translation unavailable")
            return text
            
        url = "https://api.sarvam.ai/translate"
        payload = {
            "input": text,
            "source_language_code": source_lang,
            "target_language_code": target_lang
        }
        
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=self._get_headers('application/json'),
                        json=payload
                    )
                    response.raise_for_status()
                    result = response.json()
            else:
                response = requests.post(
                    url,
                    headers=self._get_headers('application/json'),
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
            return result.get("translated_text", text)
        except Exception as e:
            logger.error(f"Error in translation: {e}")
            return text

    def detect_wake_word(self, text: str) -> bool:
        if not self.config.enable_wake_word:
            return False
            
        lower_text = text.lower()
        for word in self.config.wake_words:
            if word.lower() in lower_text:
                return True
        return False

    def get_frontend_config(self) -> dict:
        return {
            'stt_available': True,
            'stt_languages': ['en-IN', 'hi-IN', 'mr-IN'],
            'wake_words': ['hey ava', 'ava', 'ok ava', 'hey hyundai'],
            'continuous_listening': False,
            'interim_results': True,
            'sarvam_available': bool(self.config.sarvam_api_key),
            'tts_available': True
        }

speech_service = SpeechService()

# API route helpers
async def process_voice_input(audio_bytes: bytes, language: str = 'hi-IN') -> dict:
    """Helper to process incoming voice and return transcription dict."""
    result = await speech_service.transcribe(audio_bytes, language)
    return result

async def generate_voice_response(text: str, language: str = 'hi-IN') -> Optional[bytes]:
    """Helper to generate voice response audio bytes."""
    return await speech_service.synthesize(text, language)
