#!/usr/bin/env python3
import json
import re

from .utils import is_sensitive_key, make_token


NAME_FIELD_KEYS = (
    "name",
    "full_name",
    "real_name",
    "legal_name",
    "contact_name",
    "username",
    "user_name",
    "姓名",
    "联系人",
    "收件人",
)

ADDRESS_FIELD_KEYS = (
    "address",
    "home_address",
    "street_address",
    "shipping_address",
    "billing_address",
    "mailing_address",
    "详细地址",
    "收货地址",
    "家庭住址",
    "住址",
    "地址",
)

ID_FIELD_KEYS = (
    "id_number",
    "national_id",
    "citizen_id",
    "identity_number",
    "identity_no",
    "id_card",
    "idcard",
    "passport_number",
    "passport_no",
    "身份证",
    "身份证号",
    "证件号",
    "证件号码",
    "护照号",
)

DATABASE_FIELD_KEYS = (
    "database_url",
    "db_url",
    "db_uri",
    "database_uri",
    "sqlalchemy_database_uri",
    "mongo_uri",
    "mongodb_uri",
    "redis_url",
    "jdbc_url",
)

CLOUD_CREDENTIAL_FIELD_KEYS = (
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "gcp_credentials",
    "gcp_service_account",
    "azure_storage_connection_string",
    "azure_connection_string",
    "accountkey",
    "client_secret",
)

TYPE_PATTERNS = [
    ("OPENAI_KEY", re.compile(r"(?i)\bsk-[a-z0-9]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("NPM_TOKEN", re.compile(r"\bnpm_[a-z0-9]{36}\b", re.IGNORECASE)),
    ("STRIPE_SECRET", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,99}\b")),
    ("USER_EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE_NUMBER", re.compile(r"\b1[3-9]\d{9}\b")),
    ("NATIONAL_ID", re.compile(r"\b\d{17}[\dXx]\b")),
    ("DATABASE_URL", re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|jdbc):\/\/[^\s\"']+\b", re.IGNORECASE)),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("AWS_SECRET_KEY", re.compile(r"(?<![A-Za-z0-9\/+=])[A-Za-z0-9\/+=]{40}(?![A-Za-z0-9\/+=])")),
    ("AZURE_CONN_STRING", re.compile(r"\bDefaultEndpointsProtocol=https;AccountName=[^;\s]+;AccountKey=[^;\s]+(?:;EndpointSuffix=[^;\s]+)?", re.IGNORECASE)),
    ("COOKIE_HEADER", re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*[^\n\r]+")),
    ("INTERNAL_ENDPOINT", re.compile(r"(?i)\bhttps?:\/\/(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|[A-Za-z0-9._-]+(?:\.local|\.internal|\.corp))(?:[:\/][^\s\"']*)?")),
    ("STACK_TRACE_PATH", re.compile(r"(?i)\b(?:[A-Z]:\\[^:\n\r]+|\/(?:home|Users|var|opt|srv|etc)\/[^\s:\n\r]+)")),
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
PII_KEY_VALUE = re.compile(
    r"(?i)\b([a-z_][a-z0-9_-]*|姓名|联系人|收件人|详细地址|收货地址|家庭住址|住址|地址|身份证|身份证号|证件号|证件号码|护照号)"
    r"\b(\s*[:=：]\s*[\"']?)([^\n\r\"']+)"
)
SECRET_KEY_VALUE = re.compile(
    r"(?i)\b([a-z_][a-z0-9_-]*|数据库地址|数据库连接串|云凭据|云密钥|访问密钥|连接字符串)"
    r"\b(\s*[:=：]\s*[\"']?)([^\n\r\"']+)"
)


def normalize_key_name(key_name: str) -> str:
    return key_name.lower().replace("-", "").replace("_", "").replace(" ", "")


def pii_label_for_key(key_name: str) -> str | None:
    normalized = normalize_key_name(key_name)

    if any(normalize_key_name(candidate) in normalized for candidate in NAME_FIELD_KEYS):
        return "PERSON_NAME"
    if any(normalize_key_name(candidate) in normalized for candidate in ADDRESS_FIELD_KEYS):
        return "STREET_ADDRESS"
    if any(normalize_key_name(candidate) in normalized for candidate in ID_FIELD_KEYS):
        return "NATIONAL_ID"
    return None


def secret_label_for_key(key_name: str, value: str) -> str | None:
    normalized = normalize_key_name(key_name)

    if normalized in {"privatekey", "private_key"}:
        return "PRIVATE_KEY"
    if normalized in {"awsaccesskeyid"}:
        return "AWS_ACCESS_KEY"
    if normalized in {"awssecretaccesskey"}:
        return "AWS_SECRET_KEY"
    if normalized in {"azurestorageconnectionstring", "azureconnectionstring", "accountkey"}:
        return "AZURE_CONN_STRING"
    if any(normalize_key_name(candidate) in normalized for candidate in DATABASE_FIELD_KEYS):
        return "DATABASE_URL"
    if any(normalize_key_name(candidate) in normalized for candidate in CLOUD_CREDENTIAL_FIELD_KEYS):
        return "CLOUD_CREDENTIAL"
    if normalized in {"clientemail"} and value.endswith(".gserviceaccount.com"):
        return "CLOUD_CREDENTIAL"
    return None


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

    def replace_pii_key_value(match):
        label = pii_label_for_key(match.group(1))
        if not label:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{replace_with_token(label, match.group(3).strip())}"

    result = PII_KEY_VALUE.sub(replace_pii_key_value, result)

    def replace_secret_key_value(match):
        label = secret_label_for_key(match.group(1), match.group(3).strip())
        if not label:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{replace_with_token(label, match.group(3).strip())}"

    result = SECRET_KEY_VALUE.sub(replace_secret_key_value, result)

    for label, pattern in TYPE_PATTERNS:
        result = pattern.sub(lambda match: replace_with_token(label, match.group(0)), result)

    return result


def redact_content(node, token_map: dict[str, str], key_name: str | None = None):
    if isinstance(node, str):
        pii_label = pii_label_for_key(key_name) if key_name else None
        if pii_label:
            token = make_token(pii_label, node)
            token_map[token] = node
            return token
        secret_label = secret_label_for_key(key_name, node) if key_name else None
        if secret_label:
            token = make_token(secret_label, node)
            token_map[token] = node
            return token
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
