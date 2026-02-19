from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PetCommand:
    reply: str
    expression: str = "neutral"
    motion: str = "idle"
    emotion_timeline: List[Dict[str, str]] | None = None

    _ALLOWED_EMOTIONS = {"neutral", "happy", "sad", "angry", "surprised", "shy"}

    @classmethod
    def from_llm_text(cls, text: str) -> "PetCommand":
        payload = _extract_first_json(text) or {}
        reply = str(payload.get("reply", "")).strip()
        expression = cls._normalize_emotion(payload.get("expression", "neutral"))
        motion = str(payload.get("motion", "idle")).strip().lower() or "idle"
        emotion_timeline = cls._normalize_timeline(payload.get("emotion_timeline"))

        if not reply:
            reply = _strip_json_like(text).strip() or "我在。"

        return cls(
            reply=reply,
            expression=expression,
            motion=motion,
            emotion_timeline=emotion_timeline,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "expression": self.expression,
            "motion": self.motion,
            "emotion_timeline": self.emotion_timeline or [],
        }

    def to_voice_payload(self) -> Dict[str, Any]:
        return {
            "text": self.reply,
            "base_emotion": self.expression,
            "emotion_timeline": self.emotion_timeline or [],
        }

    @classmethod
    def _normalize_emotion(cls, value: Any) -> str:
        s = str(value or "").strip().lower()
        if s in cls._ALLOWED_EMOTIONS:
            return s
        return "neutral"

    @classmethod
    def _normalize_timeline(cls, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            seg_text = str(item.get("text", "")).strip()
            emo = cls._normalize_emotion(item.get("emotion", "neutral"))
            if not seg_text:
                continue
            out.append({"text": seg_text, "emotion": emo})
        return out


def _extract_first_json(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for item in candidates:
        try:
            data = json.loads(item)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _strip_json_like(text: str) -> str:
    return re.sub(r"```(?:json)?|```", "", text or "", flags=re.IGNORECASE)
