"""ElevenLabs: konuşma (TTS) ve ses efekti (AI foley). Çıktı her zaman **ham PCM → ``.wav``** (yalnız wav kuralı)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ...core.contracts import ProviderKind
from .. import pricing
from .aicore import AIProvider, AudioResult, Usage, ai_err, pcm_to_wav, wav_seconds

PCM_RATE = 24000
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL = "eleven_multilingual_v2"


class ElevenLabsProvider(AIProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("elevenlabs", "ElevenLabs", ProviderKind.TTS, base_url="https://api.elevenlabs.io",
                         ping_path="/v1/user", auth="header:xi-api-key", settings=settings, transport=transport, **kw)

    def voices(self, *, vault: Any = None, timeout: float = 15.0) -> List[Dict[str, str]]:
        data = self.request_json("GET", "/v1/voices", secret=self.secret(vault), timeout=timeout)
        return [{"id": str(v.get("voice_id")), "name": str(v.get("name", ""))} for v in (data.get("voices") or [])
                if isinstance(v, Mapping)]

    def synthesize(self, text: str, *, vault: Any = None, voice_id: Optional[str] = None, model_id: Optional[str] = None,
                   stability: float = 0.5, similarity: float = 0.75, language: Optional[str] = None,
                   timeout: float = 120.0) -> AudioResult:
        text = (text or "").strip()
        if not text:
            raise ValueError("Seslendirilecek bir metin yaz.")
        secret = self.secret(vault)
        voice = voice_id or str(self.setting("voice", DEFAULT_VOICE))
        body: Dict[str, Any] = {"text": text, "model_id": model_id or self.model(DEFAULT_MODEL),
                                "voice_settings": {"stability": stability, "similarity_boost": similarity}}
        if language:
            body["language_code"] = language
        resp = self.request("POST", f"/v1/text-to-speech/{voice}", secret=secret, timeout=timeout, json_body=body,
                            params={"output_format": f"pcm_{PCM_RATE}"}, headers={"Accept": "application/octet-stream"})
        if not resp.content:
            raise ai_err("bad_response", source=self.id, name=self.display_name)
        wav = pcm_to_wav(resp.content, PCM_RATE)
        return AudioResult(wav, Usage(self.id, body["model_id"], 0, 0, float(len(text)), pricing.tts_cost(len(text))), self.id,
                           wav_seconds(wav))

    def sound_effect(self, description: str, *, vault: Any = None, seconds: float = 3.0, timeout: float = 120.0) -> AudioResult:
        """AI foley: yazıdan ses efekti (``/v1/sound-generation``), ``.wav`` olarak döner."""
        secret = self.secret(vault)
        body = {"text": description, "duration_seconds": max(0.5, min(22.0, float(seconds)))}
        resp = self.request("POST", "/v1/sound-generation", secret=secret, timeout=timeout, json_body=body,
                            params={"output_format": f"pcm_{PCM_RATE}"})
        if not resp.content:
            raise ai_err("bad_response", source=self.id, name=self.display_name)
        wav = pcm_to_wav(resp.content, PCM_RATE)
        return AudioResult(wav, Usage(self.id, "sound-generation", 0, 0, body["duration_seconds"],
                                      pricing.sfx_cost(body["duration_seconds"])), self.id, wav_seconds(wav))
