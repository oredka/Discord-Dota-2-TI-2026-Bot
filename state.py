"""Load and save per-day announcement state."""

from __future__ import annotations

import json

from config import STATE_FILE


def state_file_for(day: int | None = None):
    if day is None:
        return STATE_FILE
    return STATE_FILE.with_name(f"match_states_day{day}.json")


def load_states(day: int | None = None) -> dict[str, object]:
    file = state_file_for(day)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def save_states(states: dict[str, object], day: int | None = None) -> None:
    file = state_file_for(day)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
