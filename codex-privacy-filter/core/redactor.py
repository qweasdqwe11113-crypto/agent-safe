#!/usr/bin/env python3
import json
import re

from .utils import is_sensitive_key, make_token

TYPE_PATTERNS = [
    ("OPENAI_KEY", re.compile(r"(?i)\bsk-[a-z0-9]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("NPM_TOKEN", re.compile(r"\bnpm_[a-z0-9]{36}\b", re.IGNORECASE)),
    ("STRIPE_SECRET", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,99}\b")),
    ("USER_EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE_NUMBER", re.compile(r"\b1[3-9]\d{9}\b")),
    ("IPV4_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("IPV6_ADDRESS", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")),
    ("PAYMENT_CARD", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----[\s\S]{64,}?-----END[ A-Z0-9_-]{0,100}PRIVATE KEY-----"
        ),
    ),
    ("GENERIC_TOKEN", re.compile(r"(?i)\b[A-Z0-9]{20,}[_-]?[A-Z0-9]{10,}\b")),
]

KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b(\s*[:=]\s*[\"']?)([^\s\"']+)"
)
AUTH_BEARER = re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)([^\s]+)")


def redact_string(text: str, token_map: dict[str, str]) -> str:
    result = text

    def replace_with_token(label: str, value: str) -> str:
        token = make_token(label, value)
        token_map[token] = value
        return token

    result = KEY_VALUE_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{replace_with_token('SENSITIVE_SECRET', match.group(3))}",
        result,
    )

    result = AUTH_BEARER.sub(
        lambda match: f"{match.group(1)}{replace_with_token('AUTH_TOKEN', match.group(2))}",
        result,
    )

    for label, pattern in TYPE_PATTERNS:
        result = pattern.sub(lambda match: replace_with_token(label, match.group(0)), result)

    return result


def redact_content(node, token_map: dict[str, str], key_name: str | None = None):
    if isinstance(node, str):
        if key_name and is_sensitive_key(key_name):
            token = make_token("SENSITIVE_SECRET", node)
            token_map[token] = node
            return token
        return redact_string(node, token_map)

    if isinstance(node, list):
        return [redact_content(item, token_map) for item in node]

    if isinstance(node, dict):
        return {key: redact_content(value, token_map, key) for key, value in node.items()}

    return node


def redact_text(text: str) -> tuple[str, dict[str, str]]:
    token_map: dict[str, str] = {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return redact_string(text, token_map), token_map

    redacted = redact_content(parsed, token_map)
    return json.dumps(redacted, ensure_ascii=False, indent=2), token_map


def restore_text(text: str, token_map: dict[str, str], restore_string_fn) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return restore_string_fn(text, token_map)

    def walk(node):
        if isinstance(node, str):
            return restore_string_fn(node, token_map)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        return node

    restored = walk(parsed)
    return json.dumps(restored, ensure_ascii=False, indent=2)
