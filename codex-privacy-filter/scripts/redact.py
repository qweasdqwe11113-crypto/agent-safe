#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.redactor import redact_text, restore_text  # noqa: E402
from core.vault import load_token_map, restore_string, save_token_map  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Redact sensitive text for Codex workflows.")
    parser.add_argument("input", nargs="?", help="Optional file path. If omitted, read from stdin.")
    parser.add_argument(
        "--map-out",
        help="Optional JSON file path used to save the token-to-original-value mapping.",
    )
    parser.add_argument(
        "--restore-map",
        help="Optional JSON file path used to restore previously redacted tokens back to their original values.",
    )
    args = parser.parse_args()

    if args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    if args.restore_map:
        restore_map = load_token_map(args.restore_map)
        sys.stdout.write(restore_text(text, restore_map, restore_string))
        return 0

    redacted_text, token_map = redact_text(text)
    sys.stdout.write(redacted_text)

    if args.map_out:
        save_token_map(token_map, args.map_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
