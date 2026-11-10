"""A local daily token ledger for free-tier quotas.

GroqCloud reports requests-per-day and tokens-per-minute in response headers, but not
tokens-per-day. This ledger records what *this project* spent per UTC day, so a run can stop
before the daily token quota is reached. It is approximate: Groq's daily window is not
documented, and other clients on the same organisation are invisible to it. A daily-quota 429
from the API stays the authoritative backstop.

The file holds only counts, never prompts, outputs or keys. It lives under ``runs/``
(gitignored).
"""

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class DailyLedger:
    def __init__(self, path: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.path = path
        self._now = now

    @classmethod
    def for_model(cls, root: Path, provider: str, model: str, **kwargs) -> "DailyLedger":
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{provider}-{model}")
        return cls(root / f"{safe}.json", **kwargs)

    def today(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def used_today(self) -> dict[str, int]:
        day = self._load().get(self.today(), {})
        return {k: int(day.get(k, 0)) for k in ("requests", "prompt_tokens", "completion_tokens")}

    def tokens_today(self) -> int:
        used = self.used_today()
        return used["prompt_tokens"] + used["completion_tokens"]

    def add(self, requests: int, prompt_tokens: int, completion_tokens: int) -> None:
        data = self._load()
        day = data.setdefault(self.today(), {})
        day["requests"] = int(day.get("requests", 0)) + requests
        day["prompt_tokens"] = int(day.get("prompt_tokens", 0)) + prompt_tokens
        day["completion_tokens"] = int(day.get("completion_tokens", 0)) + completion_tokens
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
