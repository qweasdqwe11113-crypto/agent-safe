#!/usr/bin/env python3
import hashlib
import re


SENSITIVE_KEY_MARKERS = (
    "apikey",
    "accesskey",
    "secretkey",
    "privatekey",
    "clientsecret",
    "password",
    "passcode",
    "pin",
    "passwd",
    "credential",
    "cookie",
    "sessionid",
)

SENSITIVE_KEY_EXACT = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "passwd",
    "password",
    "private_key",
    "secret",
    "session_id",
    "sessionid",
    "token",
    "otp",
    "凭据",
    "口令",
    "密码",
    "密钥",
    "授权",
    "私钥",
    "秘钥",
    "令牌",
    "验证码",
}

SENSITIVE_KEY_EXACT_COMPACT = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", key) for key in SENSITIVE_KEY_EXACT
}

SENSITIVE_KEY_ZH_SUFFIXES = ("密码", "口令", "令牌", "密钥", "秘钥", "凭据", "私钥")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]


def make_token(label: str, value: str) -> str:
    return f"[{label}_{token_hash(value)}]"


def is_sensitive_key(key_name: str) -> bool:
    lowered = key_name.strip().lower()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", lowered)
    if lowered in SENSITIVE_KEY_EXACT or compact in SENSITIVE_KEY_EXACT_COMPACT:
        return True
    if any(marker in compact for marker in SENSITIVE_KEY_MARKERS):
        return True
    return compact.endswith(("secret", "token", "auth", "pwd", *SENSITIVE_KEY_ZH_SUFFIXES))
