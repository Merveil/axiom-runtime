from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RuntimeConfig:
    storage_dir: Path = Path(".rrt")
    events_file: str = "events.jsonl"

    @property
    def events_path(self) -> Path:
        return self.storage_dir / self.events_file