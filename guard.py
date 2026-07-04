#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = PROJECT_ROOT / "codex-privacy-filter"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.redactor import redact_text  # noqa: E402


PROFILE_POLICIES = {
    "coding": {
        "block_categories": {"secret"},
        "mask_categories": {"pii", "network"},
    },
    "office": {
        "block_categories": {"secret"},
        "mask_categories": {"pii", "network"},
    },
    "finance": {
        "block_categories": {"secret", "finance"},
        "mask_categories": {"pii", "network"},
    },
}

LABEL_CATEGORIES = {
    "SENSITIVE_SECRET": "secret",
    "AUTH_TOKEN": "secret",
    "OPENAI_KEY": "secret",
    "ANTHROPIC_KEY": "secret",
    "GITHUB_TOKEN": "secret",
    "NPM_TOKEN": "secret",
    "STRIPE_SECRET": "secret",
    "PRIVATE_KEY": "secret",
    "GENERIC_TOKEN": "secret",
    "USER_EMAIL": "pii",
    "PHONE_NUMBER": "pii",
    "PAYMENT_CARD": "finance",
    "IPV4_ADDRESS": "network",
    "IPV6_ADDRESS": "network",
}

RISK_LEVELS = {
    "allow": "LOW",
    "mask": "MEDIUM",
    "block": "HIGH",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview allow/mask/block decisions before sending content to an agent."
    )
    parser.add_argument("input", nargs="?", help="Input file path.")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read input from stdin instead of a file.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_POLICIES),
        required=True,
        help="Policy profile used to decide allow, mask, or block.",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Show a review step and let the user choose the final action interactively.",
    )
    parser.add_argument(
        "--out",
        help="Optional output file path used to save the final content selected by the wrapper.",
    )
    parser.add_argument(
        "--codex",
        action="store_true",
        help="After wrapper review, automatically call `codex exec` with the prepared content file.",
    )
    parser.add_argument(
        "--codex-profile",
        help="Optional Codex profile name passed to `codex exec --profile`.",
    )
    parser.add_argument(
        "--codex-output",
        help="Optional file path used to save the last message returned by `codex exec`.",
    )
    parser.add_argument(
        "--override",
        choices=("allow", "mask", "block"),
        help="Optional final action chosen by the user to override the suggested action.",
    )
    parser.add_argument(
        "--override-reason",
        help="Optional reason recorded when overriding the suggested action.",
    )
    args = parser.parse_args()

    if args.stdin == bool(args.input):
        parser.error("Use exactly one input source: either <file> or --stdin.")
    if args.override_reason and not args.override:
        parser.error("--override-reason requires --override.")
    if args.review and args.override:
        parser.error("Use either --review or --override, not both.")
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


def extract_label(token: str) -> str:
    if not (token.startswith("[") and token.endswith("]")):
        return "UNKNOWN"
    body = token[1:-1]
    if "_" not in body:
        return body
    return body.rsplit("_", 1)[0]


def label_display_name(label: str) -> str:
    return label.replace("_", " ").title()


def summarize_findings(token_map: dict[str, str]) -> list[tuple[str, int]]:
    label_counts = Counter(extract_label(token) for token in token_map)
    return sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))


def decide_action(labels: set[str], profile: str) -> str:
    if not labels:
        return "allow"

    policy = PROFILE_POLICIES[profile]
    categories = {LABEL_CATEGORIES.get(label, "unknown") for label in labels}

    if categories & policy["block_categories"]:
        return "block"
    if categories & policy["mask_categories"]:
        return "mask"
    return "allow"


def open_review_input():
    candidates = ["CONIN$"] if sys.platform.startswith("win") else ["/dev/tty"]
    for path in candidates:
        try:
            return open(path, "r", encoding="utf-8")
        except OSError:
            continue
    return None


def prompt_review_decision(suggested_action: str) -> tuple[str, str | None]:
    review_input = open_review_input()
    if review_input is None:
        raise RuntimeError("Interactive review is unavailable in this terminal. Use --override instead.")

    try:
        while True:
            sys.stdout.write(
                "\nReview Decision:\n"
                f"- Press Enter to accept the suggested action ({suggested_action.upper()})\n"
                "- Or type allow / mask / block to override it\n"
                "> "
            )
            sys.stdout.flush()
            choice = review_input.readline()
            if not choice:
                raise RuntimeError("Interactive review was cancelled before a decision was provided.")

            choice = choice.strip().lower()
            if not choice:
                return suggested_action, None
            if choice in RISK_LEVELS:
                if choice == suggested_action:
                    return suggested_action, None

                sys.stdout.write("Override reason: ")
                sys.stdout.flush()
                reason = review_input.readline()
                if not reason:
                    raise RuntimeError("Override reason is required when changing the suggested action.")
                return choice, reason.strip() or "No reason provided"

            sys.stdout.write("Invalid choice. Please enter allow, mask, block, or press Enter.\n")
    finally:
        review_input.close()


def format_preview(
    profile: str,
    suggested_action: str,
    original_text: str,
    token_map: dict[str, str],
    redacted_text: str,
) -> str:
    sections = [
        f"Profile: {profile}",
        "",
        "Detection Results:",
    ]

    findings = summarize_findings(token_map)
    if findings:
        for label, count in findings:
            sections.append(f"- {label_display_name(label)}: {count}")
    else:
        sections.append("- No sensitive content detected")

    sections.extend(
        [
            "",
            f"Risk Level: {RISK_LEVELS[suggested_action]}",
            f"Suggested Action: {suggested_action.upper()}",
            "",
            "Original Content:",
            original_text,
            "",
            "Redacted Content:",
            redacted_text,
        ]
    )
    return "\n".join(sections)


def format_report(
    profile: str,
    suggested_action: str,
    final_action: str,
    override_reason: str | None,
    original_text: str,
    token_map: dict[str, str],
    redacted_text: str,
) -> str:
    sections = [format_preview(profile, suggested_action, original_text, token_map, redacted_text)]
    sections.extend(["", f"Final Action: {final_action.upper()}"])

    if final_action != suggested_action:
        sections.append(f"Override: YES ({(override_reason or 'No reason provided').strip()})")
    else:
        sections.append("Override: NO")
    return "\n".join(sections)


def build_codex_prompt(output_path: Path, final_action: str) -> str:
    mode_text = "approved original" if final_action == "allow" else "sanitized"
    absolute_output_path = output_path.resolve()
    file_name = absolute_output_path.name
    return (
        f"Please analyze the {mode_text} content in this file: "
        f"[{file_name}](<{absolute_output_path}>). "
        f"Absolute path: {absolute_output_path}. "
        "Treat this file as the approved context prepared by Agent Privacy Guard."
    )


def run_codex_exec(
    output_path: Path,
    final_action: str,
    codex_output: str | None,
    codex_profile: str | None,
) -> subprocess.CompletedProcess:
    codex_executable = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if not codex_executable:
        raise FileNotFoundError(
            "Could not find Codex CLI executable. Make sure `codex` is installed and available in PATH."
        )

    command = [
        codex_executable,
        "exec",
    ]
    if codex_profile:
        command.extend(["--profile", codex_profile])
    command.extend(
        [
            build_codex_prompt(output_path, final_action),
            "-C",
            str(PROJECT_ROOT),
        ]
    )
    if codex_output:
        command.extend(["-o", codex_output])
    return subprocess.run(command, text=True, check=True, env=os.environ.copy())


def main() -> int:
    args = parse_args()
    text = read_input(args)
    redacted_text, token_map = redact_text(text)
    labels = {extract_label(token) for token in token_map}
    suggested_action = decide_action(labels, args.profile)
    if args.review:
        sys.stdout.write(format_preview(args.profile, suggested_action, text, token_map, redacted_text))
        sys.stdout.write("\n")
        final_action, override_reason = prompt_review_decision(suggested_action)
        sys.stdout.write(f"\nFinal Action: {final_action.upper()}\n")
        if final_action != suggested_action:
            sys.stdout.write(f"Override: YES ({(override_reason or 'No reason provided').strip()})\n")
        else:
            sys.stdout.write("Override: NO\n")
    else:
        final_action = args.override or suggested_action
        override_reason = args.override_reason
        sys.stdout.write(
            format_report(
                args.profile,
                suggested_action,
                final_action,
                override_reason,
                text,
                token_map,
                redacted_text,
            )
        )

    output_path = Path(args.out) if args.out else None
    wrote_output = False
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if final_action == "allow":
            output_path.write_text(text, encoding="utf-8")
            sys.stdout.write(f"\nOutput File: {output_path}\n")
            wrote_output = True
        elif final_action == "mask":
            output_path.write_text(redacted_text, encoding="utf-8")
            sys.stdout.write(f"\nOutput File: {output_path}\n")
            wrote_output = True
        else:
            if output_path.exists():
                output_path.unlink()
            sys.stdout.write(f"\nOutput File: not written because final action is BLOCK\n")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
