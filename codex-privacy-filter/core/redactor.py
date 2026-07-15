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

PHONE_FIELD_KEYS = (
    "phone",
    "phone_number",
    "mobile",
    "mobile_phone",
    "telephone",
    "tel",
    "contact_phone",
    "手机",
    "手机号",
    "电话",
    "电话号码",
    "联系电话",
)

PAYMENT_CARD_FIELD_KEYS = (
    "card",
    "card_number",
    "card_no",
    "bank_card",
    "bank_card_number",
    "credit_card",
    "credit_card_number",
    "debit_card",
    "debit_card_number",
    "银行卡",
    "银行卡号",
    "卡号",
    "信用卡号",
    "借记卡号",
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
    ("ANTHROPIC_KEY", re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    (
        "OPENAI_KEY",
        re.compile(r"(?i)(?<![a-z0-9_-])sk-(?:(?:proj|svcacct)-)?[a-z0-9][a-z0-9_-]{19,}(?![a-z0-9_-])"),
    ),
    ("GITHUB_TOKEN", re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])")),
    ("GITLAB_TOKEN", re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("NPM_TOKEN", re.compile(r"(?<![A-Za-z0-9_])npm_[a-z0-9]{36}(?![A-Za-z0-9_])", re.IGNORECASE)),
    ("PYPI_TOKEN", re.compile(r"(?<![A-Za-z0-9_-])pypi-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("HUGGINGFACE_TOKEN", re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])")),
    ("GOOGLE_API_KEY", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")),
    ("SLACK_TOKEN", re.compile(r"(?<![A-Za-z0-9_-])(?:xox[abprs]|xapp)-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])")),
    (
        "SLACK_WEBHOOK",
        re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"),
    ),
    (
        "JWT_TOKEN",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?![A-Za-z0-9_-])"
        ),
    ),
    ("TELEGRAM_BOT_TOKEN", re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")),
    (
        "SENDGRID_KEY",
        re.compile(r"(?<![A-Za-z0-9_-])SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    ),
    (
        "STRIPE_SECRET",
        re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,99}(?![A-Za-z0-9_])"),
    ),
    (
        "DATABASE_URL",
        re.compile(
            r"(?<![A-Za-z0-9+.-])(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|jdbc):\/\/[^\s\"'<>，。！？；、,;）)\]】}]+",
            re.IGNORECASE,
        ),
    ),
    ("USER_EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE_NUMBER", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![A-Za-z0-9])\+[1-9]\d{0,2}(?:[ .-]?\(?\d{1,4}\)?){2,5}(?![A-Za-z0-9])"),
    ),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![A-Za-z0-9])(?:\(\d{2,4}\)|\d{2,4})[ .-]\d{3,4}[ .-]\d{4}(?![A-Za-z0-9])"),
    ),
    ("NATIONAL_ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("AWS_ACCESS_KEY", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("AWS_SECRET_KEY", re.compile(r"(?<![A-Za-z0-9\/+=])[A-Za-z0-9\/+=]{40}(?![A-Za-z0-9\/+=])")),
    (
        "AZURE_CONN_STRING",
        re.compile(
            r"(?<![A-Za-z0-9])DefaultEndpointsProtocol=https;AccountName=[^;\s]+;AccountKey=[^;\s]+(?:;EndpointSuffix=[^;\s]+)?",
            re.IGNORECASE,
        ),
    ),
    ("COOKIE_HEADER", re.compile(r"(?i)(?<![A-Za-z0-9_-])(?:cookie|set-cookie)\s*:\s*[^\n\r]+")),
    (
        "INTERNAL_ENDPOINT",
        re.compile(
            r"(?i)(?<![A-Za-z0-9+.-])https?:\/\/(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|[A-Za-z0-9._-]+(?:\.local|\.internal|\.corp))(?:[:\/][^\s\"'<>，。！？；、,;）)\]】}]*)?"
        ),
    ),
    (
        "STACK_TRACE_PATH",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:\\[^:\n\r，。；;]+|\/(?:home|Users|var|opt|srv|etc)\/[^\s:\n\r，。；;]+)"
        ),
    ),
    ("IPV4_ADDRESS", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("IPV6_ADDRESS", re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")),
    ("PAYMENT_CARD", re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]?){12,15}\d(?![A-Za-z0-9])")),
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----[\s\S]{64,}?-----END[ A-Z0-9_-]{0,100}PRIVATE KEY-----"
        ),
    ),
    (
        "GENERIC_TOKEN",
        re.compile(r"(?i)(?<![A-Z0-9_-])[A-Z0-9]{20,}[_-]?[A-Z0-9]{10,}(?![A-Z0-9_-])"),
    ),
]

KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b(\s*[:=]\s*[\"']?)([^\s\"']+)"
)
AUTH_BEARER = re.compile(r"(?i)(?<![A-Za-z0-9_-])(authorization\s*:\s*bearer\s+)([^\s]+)")
PII_KEY_VALUE = re.compile(
    r"(?i)\b([a-z_][a-z0-9_-]*|姓名|联系人|收件人|详细地址|收货地址|家庭住址|住址|地址|身份证|身份证号|证件号|证件号码|护照号)"
    r"\b(\s*[:=：]\s*[\"']?)([^\n\r\"']+)"
)
SECRET_KEY_VALUE = re.compile(
    r"(?i)\b([a-z_\u4e00-\u9fff][a-z0-9_.\-\u4e00-\u9fff]*)"
    r"\b(\s*[:=：]\s*[\"']?)([^\n\r\"']+)"
)


def normalize_key_name(key_name: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", key_name.lower())


def pii_label_for_key(key_name: str) -> str | None:
    normalized = normalize_key_name(key_name)

    if any(normalize_key_name(candidate) in normalized for candidate in PHONE_FIELD_KEYS):
        return "PHONE_NUMBER"
    if any(normalize_key_name(candidate) in normalized for candidate in NAME_FIELD_KEYS):
        return "PERSON_NAME"
    if any(normalize_key_name(candidate) in normalized for candidate in ADDRESS_FIELD_KEYS):
        return "STREET_ADDRESS"
    if any(normalize_key_name(candidate) in normalized for candidate in ID_FIELD_KEYS):
        return "NATIONAL_ID"
    if any(normalize_key_name(candidate) in normalized for candidate in PAYMENT_CARD_FIELD_KEYS):
        return "PAYMENT_CARD"
    return None


def secret_label_for_key(key_name: str, value: str) -> str | None:
    normalized = normalize_key_name(key_name)

    if normalized in {"authorization", "authheader"}:
        return "AUTH_TOKEN"
    if normalized in {"cookie", "setcookie", "cookieheader", "sessioncookie"}:
        return "COOKIE_HEADER"
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
        if not label and is_sensitive_key(match.group(1)):
            label = "SENSITIVE_SECRET"
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

    if isinstance(node, (int, float)) and not isinstance(node, bool):
        value = str(node)
        pii_label = pii_label_for_key(key_name) if key_name else None
        if pii_label:
            token = make_token(pii_label, value)
            token_map[token] = value
            return token
        secret_label = secret_label_for_key(key_name, value) if key_name else None
        if secret_label:
            token = make_token(secret_label, value)
            token_map[token] = value
            return token
        if key_name and is_sensitive_key(key_name):
            token = make_token("SENSITIVE_SECRET", value)
            token_map[token] = value
            return token
        redacted_value = redact_string(value, token_map)
        return redacted_value if redacted_value != value else node

    if isinstance(node, list):
        return [redact_content(item, token_map, key_name) for item in node]

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
