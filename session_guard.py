#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_client import build_session_prompt, extract_assistant_reply, run_codex_turn
from guard_core import PROFILE_POLICIES, RISK_LEVELS, apply_final_action, build_preview, restore_response, scan_text
from session_state import SessionState, TurnRecord, append_turn, save_session_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an ongoing privacy-guarded Codex session.")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_POLICIES),
        required=True,
        help="Policy profile used to decide allow, mask, or block.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/sessions",
        help="Directory used to store session artifacts.",
    )
    parser.add_argument(
        "--session-id",
        help="Optional session id. If omitted, a timestamp-based id is created.",
    )
    parser.add_argument(
        "--codex-profile",
        help="Optional Codex profile name passed to `codex exec --profile`.",
    )
    return parser.parse_args()


def prompt_review_decision(suggested_action: str) -> tuple[str, str | None]:
    while True:
        print(
            "\nReview Decision:\n"
            f"- Press Enter to accept the suggested action ({suggested_action.upper()})\n"
            "- Or type allow / mask / block to override it"
        )
        choice = input("> ").strip().lower()
        if not choice:
            return suggested_action, None
        if choice in RISK_LEVELS:
            if choice == suggested_action:
                return suggested_action, None
            reason = input("Override reason: ").strip()
            return choice, reason or "No reason provided"
        print("Invalid choice. Please enter allow, mask, block, or press Enter.")


def run_session_turn(session_state: SessionState, user_input: str, codex_profile: str | None) -> TurnRecord | None:
    scan_result = scan_text(user_input, session_state.profile)
    print(build_preview(scan_result))
    final_action, override_reason = prompt_review_decision(scan_result.suggested_action)

    safe_text = apply_final_action(scan_result, final_action)
    if safe_text is None:
        print("\nFinal Action: BLOCK")
        print("Codex turn skipped because final action is BLOCK.")
        turn_record = TurnRecord(
            turn_id=session_state.next_turn_id(),
            user_original=scan_result.original_text,
            user_redacted=scan_result.redacted_text,
            suggested_action=scan_result.suggested_action,
            final_action=final_action,
            override_reason=override_reason,
            user_sent_text="",
        )
        append_turn(session_state, turn_record)
        return turn_record

    turn_id = session_state.next_turn_id()
    turn_prefix = f"turn-{turn_id:03d}"
    token_map_path = session_state.session_path / f"{turn_prefix}-token-map.json"
    if scan_result.token_map:
        token_map_path.write_text(
            json.dumps(scan_result.token_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        token_map_str = str(token_map_path)
    else:
        token_map_str = None

    original_path = session_state.session_path / f"{turn_prefix}-user-original.txt"
    safe_path = session_state.session_path / f"{turn_prefix}-user-safe.txt"
    raw_path = session_state.session_path / f"{turn_prefix}-codex-raw.txt"
    restored_path = session_state.session_path / f"{turn_prefix}-codex-restored.txt"

    original_path.write_text(scan_result.original_text, encoding="utf-8")
    safe_path.write_text(safe_text, encoding="utf-8")

    prompt = build_session_prompt(session_state.history_for_prompt(), safe_text, session_state.profile)
    process = run_codex_turn(prompt, raw_path, codex_profile)
    if raw_path.exists():
        raw_reply = raw_path.read_text(encoding="utf-8")
    else:
        raw_reply = ""

    if not raw_reply.strip():
        raw_reply = extract_assistant_reply(process.stdout)
        raw_path.write_text(raw_reply, encoding="utf-8")
    restored_reply = restore_response(raw_reply, scan_result.token_map)
    restored_path.write_text(restored_reply, encoding="utf-8")

    turn_record = TurnRecord(
        turn_id=turn_id,
        user_original=scan_result.original_text,
        user_redacted=scan_result.redacted_text,
        suggested_action=scan_result.suggested_action,
        final_action=final_action,
        override_reason=override_reason,
        user_sent_text=safe_text,
        codex_raw_reply=raw_reply,
        codex_restored_reply=restored_reply,
        token_map_path=token_map_str,
        artifacts={
            "user_original": str(original_path),
            "user_safe": str(safe_path),
            "codex_raw": str(raw_path),
            "codex_restored": str(restored_path),
            **({"token_map": token_map_str} if token_map_str else {}),
        },
    )
    append_turn(session_state, turn_record)
    print("\nAssistant:")
    print(restored_reply or raw_reply)
    return turn_record


def main() -> int:
    args = parse_args()
    session_state = SessionState.create(args.profile, Path(args.output_dir), args.session_id)
    save_session_log(session_state)

    print(f"Session started: {session_state.session_id}")
    print(f"Artifacts: {session_state.session_path}")
    print("Commands: /exit to finish, /history to inspect saved turns")

    while True:
        try:
            user_input = input("\nYou> ")
        except EOFError:
            print("\nSession ended.")
            break

        command = user_input.strip()
        if not command:
            continue
        if command == "/exit":
            print("Session ended.")
            break
        if command == "/history":
            if not session_state.turns:
                print("No turns yet.")
            else:
                for turn in session_state.turns:
                    print(f"Turn {turn.turn_id}: {turn.final_action.upper()}")
            continue

        run_session_turn(session_state, user_input, args.codex_profile)

    save_session_log(session_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
