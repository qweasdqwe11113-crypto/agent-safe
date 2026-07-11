#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from codex_client import build_file_analysis_prompt, extract_assistant_reply, run_codex_turn
from guard_core import (
    PROFILE_POLICIES,
    RISK_LEVELS,
    apply_final_action,
    build_preview,
    build_report,
    restore_response_file,
    scan_file,
    scan_text,
    write_turn_artifacts,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview allow/mask/block decisions before sending content to an agent."
    )
    parser.add_argument("input", nargs="?", help="Input file path.")
    parser.add_argument("--stdin", action="store_true", help="Read input from stdin instead of a file.")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_POLICIES),
        required=True,
        help="Policy profile used to decide allow, mask, or block.",
    )
    parser.add_argument("--out", help="Optional output file path used to save the policy-approved content.")
    parser.add_argument(
        "--codex",
        action="store_true",
        help="Automatically call `codex exec` with the policy-approved content file.",
    )
    parser.add_argument("--codex-profile", help="Optional Codex profile name passed to `codex exec --profile`.")
    parser.add_argument("--codex-output", help="Optional file path used to save the last message returned by `codex exec`.")
    args = parser.parse_args()

    if args.stdin == bool(args.input):
        parser.error("Use exactly one input source: either <file> or --stdin.")
    if args.codex_output and not args.codex:
        parser.error("--codex-output requires --codex.")
    if args.codex_profile and not args.codex:
        parser.error("--codex-profile requires --codex.")
    if args.codex and not args.out:
        parser.error("--codex requires --out so Codex has a prepared file to consume.")

    return args


def read_input(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read()
    return Path(args.input).read_text(encoding="utf-8")


def build_codex_prompt(output_path: Path, final_action: str) -> str:
    return build_file_analysis_prompt(output_path, final_action)


def write_guard_artifacts(output_path: Path, original_text: str, token_map: dict[str, str]) -> tuple[Path, Path | None]:
    artifacts = write_turn_artifacts(output_path, original_text, None, token_map)
    return artifacts["original"], artifacts.get("token_map")


def run_codex_exec(
    output_path: Path,
    final_action: str,
    codex_output: str | None,
    codex_profile: str | None,
):
    prompt = build_codex_prompt(output_path, final_action)
    process = run_codex_turn(prompt, Path(codex_output) if codex_output else None, codex_profile, PROJECT_ROOT)
    if codex_output:
        codex_output_path = Path(codex_output)
        if (not codex_output_path.exists()) or (not codex_output_path.read_text(encoding="utf-8").strip()):
            codex_output_path.parent.mkdir(parents=True, exist_ok=True)
            codex_output_path.write_text(extract_assistant_reply(process.stdout), encoding="utf-8")
    return process


def restore_codex_output(codex_output_path: Path, token_map: dict[str, str]) -> Path:
    return restore_response_file(codex_output_path, token_map)


def main() -> int:
    args = parse_args()
    if args.stdin:
        scan_result = scan_text(read_input(args), args.profile)
    else:
        scan_result = scan_file(Path(args.input), args.profile)

    final_action = scan_result.suggested_action
    sys.stdout.write(build_report(scan_result, final_action))

    output_path = Path(args.out) if args.out else None
    wrote_output = False
    if output_path:
        safe_text = apply_final_action(scan_result, final_action)
        artifacts = write_turn_artifacts(
            output_path=output_path,
            original_text=scan_result.original_text,
            safe_text=safe_text,
            token_map=scan_result.token_map,
        )

        if safe_text is None:
            sys.stdout.write("\nOutput File: not written because final action is BLOCK\n")
        else:
            sys.stdout.write(f"\nOutput File: {output_path}\n")
            wrote_output = True

        if "original" in artifacts:
            sys.stdout.write(f"Original File: {artifacts['original']}\n")
        if "token_map" in artifacts:
            sys.stdout.write(f"Token Map File: {artifacts['token_map']}\n")

    if args.codex:
        if final_action == "block":
            sys.stdout.write("Codex Exec: skipped because final action is BLOCK\n")
        elif output_path and wrote_output:
            run_codex_exec(output_path, final_action, args.codex_output, args.codex_profile)
            sys.stdout.write("Codex Exec: launched successfully\n")
            if args.codex_profile:
                sys.stdout.write(f"Codex Profile: {args.codex_profile}\n")
            if args.codex_output:
                sys.stdout.write(f"Codex Output File: {args.codex_output}\n")
                codex_output_path = Path(args.codex_output)
                if codex_output_path.exists():
                    restored_path = restore_codex_output(codex_output_path, scan_result.token_map)
                    sys.stdout.write(f"Codex Restored Output File: {restored_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
