#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = PROJECT_ROOT / "codex-privacy-filter"

import sys

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.redactor import redact_text  # noqa: E402
from core.vault import restore_string, save_token_map  # noqa: E402
from ner_adapter import detect_entities  # noqa: E402


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
    "DATABASE_URL": "secret",
    "AWS_ACCESS_KEY": "secret",
    "AWS_SECRET_KEY": "secret",
    "AZURE_CONN_STRING": "secret",
    "CLOUD_CREDENTIAL": "secret",
    "USER_EMAIL": "pii",
    "PHONE_NUMBER": "pii",
    "PERSON_NAME": "pii",
    "STREET_ADDRESS": "pii",
    "NATIONAL_ID": "pii",
    "PAYMENT_CARD": "finance",
    "IPV4_ADDRESS": "network",
    "IPV6_ADDRESS": "network",
}

RISK_LEVELS = {
    "allow": "LOW",
    "mask": "MEDIUM",
    "block": "HIGH",
}


@dataclass(slots=True)
class ScanResult:
    profile: str
    original_text: str
    redacted_text: str
    token_map: dict[str, str]
    labels: set[str]
    suggested_action: str


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


def suggest_action(labels: set[str], profile: str) -> str:
    if not labels:
        return "allow"

    policy = PROFILE_POLICIES[profile]
    categories = {LABEL_CATEGORIES.get(label, "unknown") for label in labels}

    if categories & policy["block_categories"]:
        return "block"
    if categories & policy["mask_categories"]:
        return "mask"
    return "allow"


def scan_text(text: str, profile: str) -> ScanResult:
    redacted_text, token_map = redact_text(text)
    entity_spans = detect_entities(redacted_text)
    if entity_spans:
        redacted_text = apply_entity_redaction(redacted_text, token_map, entity_spans)
    labels = {extract_label(token) for token in token_map}
    return ScanResult(
        profile=profile,
        original_text=text,
        redacted_text=redacted_text,
        token_map=token_map,
        labels=labels,
        suggested_action=suggest_action(labels, profile),
    )


def apply_entity_redaction(text: str, token_map: dict[str, str], entity_spans) -> str:
    result = text
    for span in sorted(entity_spans, key=lambda item: item.start, reverse=True):
        token = f"[{span.label}_{hash_token_source(span.text)}]"
        if token in token_map and token_map[token] != span.text:
            continue
        token_map[token] = span.text
        result = result[: span.start] + token + result[span.end :]
    return result


def hash_token_source(value: str) -> str:
    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()[:6]


def build_preview(scan_result: ScanResult) -> str:
    sections = [
        f"Profile: {scan_result.profile}",
        "",
        "Detection Results:",
    ]

    findings = summarize_findings(scan_result.token_map)
    if findings:
        for label, count in findings:
            sections.append(f"- {label_display_name(label)}: {count}")
    else:
        sections.append("- No sensitive content detected")

    sections.extend(
        [
            "",
            f"Risk Level: {RISK_LEVELS[scan_result.suggested_action]}",
            f"Suggested Action: {scan_result.suggested_action.upper()}",
            "",
            "Original Content:",
            scan_result.original_text,
            "",
            "Redacted Content:",
            scan_result.redacted_text,
        ]
    )
    return "\n".join(sections)


def build_report(scan_result: ScanResult, final_action: str, override_reason: str | None) -> str:
    sections = [build_preview(scan_result)]
    sections.extend(["", f"Final Action: {final_action.upper()}"])

    if final_action != scan_result.suggested_action:
        sections.append(f"Override: YES ({(override_reason or 'No reason provided').strip()})")
    else:
        sections.append("Override: NO")
    return "\n".join(sections)


def apply_final_action(scan_result: ScanResult, final_action: str) -> str | None:
    if final_action == "allow":
        return scan_result.original_text
    if final_action == "mask":
        return scan_result.redacted_text
    return None


def build_artifact_path(base_path: Path, file_name: str) -> Path:
    return base_path.parent / file_name


def write_turn_artifacts(
    output_path: Path,
    original_text: str,
    safe_text: str | None,
    token_map: dict[str, str],
) -> dict[str, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}

    original_path = build_artifact_path(output_path, "original.txt")
    original_path.write_text(original_text, encoding="utf-8")
    artifacts["original"] = original_path

    if safe_text is not None:
        output_path.write_text(safe_text, encoding="utf-8")
        artifacts["safe"] = output_path
    elif output_path.exists():
        output_path.unlink()

    if token_map:
        token_map_path = build_artifact_path(output_path, "token-map.json")
        save_token_map(token_map, str(token_map_path))
        artifacts["token_map"] = token_map_path

    return artifacts


def restore_response(response_text: str, token_map: dict[str, str]) -> str:
    return restore_string(response_text, token_map) if token_map else response_text


def restore_response_file(output_path: Path, token_map: dict[str, str]) -> Path:
    restored_path = build_artifact_path(output_path, "codex-result-restored.txt")
    restored_text = restore_response(output_path.read_text(encoding="utf-8"), token_map)
    restored_path.write_text(restored_text, encoding="utf-8")
    return restored_path
