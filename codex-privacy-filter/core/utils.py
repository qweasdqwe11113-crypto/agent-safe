#!/usr/bin/env python3
import hashlib


SENSITIVE_KEYS = ("apikey", "api_key", "secret", "password", "token", "auth", "credential")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]


def make_token(label: str, value: str) -> str:
    return f"[{label}_{token_hash(value)}]"


def is_sensitive_key(key_name: str) -> bool:
    normalized = key_name.lower().replace("-", "").replace("_", "")
    return any(part.replace("_", "") in normalized for part in SENSITIVE_KEYS)

