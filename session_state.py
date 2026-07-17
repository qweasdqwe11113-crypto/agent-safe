#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class TurnRecord:
    turn_id: int
    user_original: str
    user_redacted: str
    suggested_action: str
    user_sent_text: str
    final_action: str = ""
    risk_level: str = ""
    review_mode: str = "review-first"
    is_override: bool = False
    override_reason: str = ""
    codex_raw_reply: str = ""
    codex_restored_reply: str = ""
    token_map_path: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SessionState:
    session_id: str
    profile: str
    session_dir: str
    created_at: str
    turns: list[TurnRecord] = field(default_factory=list)

    @classmethod
    def create(cls, profile: str, base_dir: Path, session_id: str | None = None) -> "SessionState":
        actual_session_id = session_id or datetime.now().strftime("session-%Y%m%d-%H%M%S")
        session_dir = base_dir / actual_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            session_id=actual_session_id,
            profile=profile,
            session_dir=str(session_dir),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    @property
    def session_path(self) -> Path:
        return Path(self.session_dir)

    def next_turn_id(self) -> int:
        return len(self.turns) + 1

    def history_for_prompt(self) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for turn in self.turns:
            history.append(
                {
                    "user": turn.user_sent_text,
                    "assistant": turn.codex_restored_reply or turn.codex_raw_reply,
                }
            )
        return history

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "profile": self.profile,
            "created_at": self.created_at,
            "session_dir": self.session_dir,
            "turns": [asdict(turn) for turn in self.turns],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "SessionState":
        turns = []
        for turn_payload in payload.get("turns", []):
            current_fields = TurnRecord.__dataclass_fields__
            turns.append(TurnRecord(**{key: value for key, value in turn_payload.items() if key in current_fields}))
        return cls(
            session_id=payload["session_id"],
            profile=payload["profile"],
            session_dir=payload["session_dir"],
            created_at=payload["created_at"],
            turns=turns,
        )


def save_session_log(session_state: SessionState) -> Path:
    output_path = session_state.session_path / "session.json"
    payload = session_state.to_dict()
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def append_turn(session_state: SessionState, turn_record: TurnRecord) -> Path:
    session_state.turns.append(turn_record)
    jsonl_path = session_state.session_path / "session-log.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(turn_record), ensure_ascii=False) + "\n")
    save_session_log(session_state)
    return jsonl_path


def load_session(session_path: Path) -> SessionState:
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    return SessionState.from_dict(payload)
