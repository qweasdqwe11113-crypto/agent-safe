#!/usr/bin/env python3
import json
from pathlib import Path


def save_token_map(token_map: dict[str, str], output_path: str) -> None:
    map_path = Path(output_path)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        json.dumps(token_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_token_map(input_path: str) -> dict[str, str]:
    return json.loads(Path(input_path).read_text(encoding="utf-8-sig"))


def restore_string(text: str, token_map: dict[str, str]) -> str:
    result = text
    for token in sorted(token_map, key=len, reverse=True):
        result = result.replace(token, token_map[token])
    return result
