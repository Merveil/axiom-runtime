from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .config import RuntimeConfig
from .schemas import RuntimeEvent


class EventStorage:
    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self.config.storage_dir.mkdir(parents=True, exist_ok=True)
        self.config.events_path.touch(exist_ok=True)

    def append_event(self, event: RuntimeEvent) -> None:
        with self.config.events_path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json())
            f.write("\n")

    def iter_events(self) -> Iterable[RuntimeEvent]:
        with self.config.events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield RuntimeEvent.model_validate(json.loads(line))

    def get_event(self, event_id: str) -> RuntimeEvent | None:
        for event in self.iter_events():
            if event.event_id == event_id:
                return event
        return None

    def list_events(self) -> list[RuntimeEvent]:
        return list(self.iter_events())

    def clear(self) -> None:
        Path(self.config.events_path).write_text("", encoding="utf-8")