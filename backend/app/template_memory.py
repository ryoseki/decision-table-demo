"""定型文のメモリ上の実装（DBなしのAPI契約テストと、メモリ保存の構成用）。

DBの実装（`template_store`）と同じ形（lookup・list_prompts・remove・save）を持つ。
設問・ルールのメモリの保存先が、承認と同じロックの中で `save` を呼ぶ。
"""

import threading
from typing import Any

from app.domain import INITIAL_TEMPLATES


class InMemoryProposalTemplates:
    def __init__(self, *, initial: bool = True) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, Any]] = {
            (target, prompt): operation
            for target, prompt, operation in (INITIAL_TEMPLATES if initial else ())
        }

    def lookup(self, target: str, prompt: str) -> dict[str, Any] | None:
        with self._lock:
            return self._rows.get((target, prompt))

    def list_prompts(self, target: str) -> list[str]:
        with self._lock:
            return sorted(p for t, p in self._rows if t == target)

    def save(self, target: str, prompt: str, operation: dict[str, Any]) -> None:
        with self._lock:
            self._rows[(target, prompt)] = operation

    def remove(self, target: str, prompt: str) -> None:
        with self._lock:
            self._rows.pop((target, prompt), None)
