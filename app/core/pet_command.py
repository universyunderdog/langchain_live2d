from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class PetCommand:
    reply: str
    expression: str = "neutral"
    motion: str = "idle"

    @classmethod
    def from_llm_text(cls, text: str) -> "PetCommand":
        payload = _extract_first_json(text) or {}
        reply = str(payload.get("reply", "")).strip()
        expression = str(payload.get("expression", "neutral")).strip().lower()
        motion = str(payload.get("motion", "idle")).strip().lower()

        if not reply:
            reply = _strip_json_like(text).strip() or "我在。"
        if not expression:
            expression = "neutral"
        if not motion:
            motion = "idle"
        return cls(reply=reply, expression=expression, motion=motion)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "expression": self.expression,
            "motion": self.motion,
        }


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
        candidates.append(raw[start:end + 1])
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
