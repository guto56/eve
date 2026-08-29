"""Trilha de auditoria das chamadas de ferramenta (spec §37, §38).

Uma linha JSON por chamada, para que o usuário sempre possa responder "o que a
EVE fez, com quais argumentos, autorizada por quem". Argumentos marcados como
secretos nunca chegam aqui.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 10 * 1024 * 1024


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except FileNotFoundError:
            return
        self.path.replace(self.path.with_suffix(".1.jsonl"))

    def record(self, **entry: Any) -> dict[str, Any]:
        entry.setdefault("ts", time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0 or not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # pragma: no cover - linha truncada
                continue
        return out
