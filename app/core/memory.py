from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@dataclass
class MemoryItem:
    text: str
    source: str = "user"
    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "ts": self.ts or datetime.now().isoformat(timespec="seconds"),
        }


class MemoryStore:
    """Simple long-term memory store for important user facts."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.memory_dir = self.workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.long_term_file = self.memory_dir / "MEMORY.jsonl"
        self.daily_file = self.memory_dir / f"{_today()}.md"

    def append_today(self, content: str) -> None:
        if not content.strip():
            return
        header = ""
        if not self.daily_file.exists():
            header = f"# {_today()}\n\n"
        with self.daily_file.open("a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write(content.rstrip() + "\n")

    def load_long_term(self, max_items: int = 200) -> list[MemoryItem]:
        if not self.long_term_file.exists():
            return []
        out: list[MemoryItem] = []
        lines = self.long_term_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines[-max_items:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            text = str(obj.get("text", "")).strip()
            if not text:
                continue
            out.append(
                MemoryItem(
                    text=text,
                    source=str(obj.get("source", "user")),
                    ts=str(obj.get("ts", "")),
                )
            )
        return out

    def add_long_term(self, text: str, source: str = "user") -> bool:
        cleaned = _normalize_text(text)
        if not cleaned:
            return False
        if not _is_important_memory(cleaned):
            return False

        existing = self.load_long_term(max_items=300)
        if _is_duplicate(cleaned, (m.text for m in existing)):
            return False

        item = MemoryItem(text=cleaned, source=source)
        with self.long_term_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        return True

    def add_from_text(self, text: str, source: str = "user") -> int:
        count = 0
        for sent in _extract_candidate_sentences(text):
            if self.add_long_term(sent, source=source):
                count += 1
        return count

    def memory_context(self, max_items: int = 20) -> str:
        items = self.load_long_term(max_items=max_items)
        if not items:
            return ""
        lines = [f"- {m.text}" for m in items[-max_items:]]
        return "## 用户长期记忆\n" + "\n".join(lines)


def _normalize_text(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("，。！？!?；;、, ")
    return s


def _extract_candidate_sentences(text: str) -> list[str]:
    s = str(text or "").strip()
    if not s:
        return []
    parts = re.split(r"[。！？!?；;\n]+", s)
    out: list[str] = []
    for p in parts:
        p = _normalize_text(p)
        if not p:
            continue
        if len(p) < 4:
            continue
        out.append(p)
    return out


def _is_important_memory(text: str) -> bool:
    t = text.lower()

    # Filter greetings/chitchat
    small_talk = [
        "你好", "您好", "哈喽", "在吗", "早上好", "中午好", "晚上好", "晚安",
        "谢谢", "再见", "拜拜", "hi", "hello", "hey", "good morning", "good night",
    ]
    if any(k in t for k in small_talk):
        return False

    # Explicit remember intent.
    remember_keys = ["记住", "请记住", "别忘了", "remember that", "remember"]
    if any(k in t for k in remember_keys):
        return True

    # Personal profile / stable preference.
    important_patterns = [
        r"^我叫.{1,20}$",
        r"^我是.{1,30}$",
        r"^我的名字是.{1,20}$",
        r"^我住在.{1,30}$",
        r"^我来自.{1,30}$",
        r"^我喜欢.{1,40}$",
        r"^我不喜欢.{1,40}$",
        r"^我的生日是?.{1,30}$",
        r"^我过敏.{1,30}$",
        r"^我的目标是?.{1,40}$",
        r"^my name is .{1,40}$",
        r"^i am .{1,40}$",
        r"^i like .{1,60}$",
        r"^i don't like .{1,60}$",
        r"^i live in .{1,60}$",
    ]
    for pat in important_patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True

    return False


def _is_duplicate(text: str, existing: Iterable[str]) -> bool:
    norm = _normalize_text(text)
    for e in existing:
        ne = _normalize_text(e)
        if not ne:
            continue
        if ne == norm:
            return True
        if norm in ne or ne in norm:
            if min(len(norm), len(ne)) >= 8:
                return True
    return False
