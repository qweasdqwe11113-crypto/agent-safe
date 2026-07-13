#!/usr/bin/env python3
"""MCP adapter for Agent Privacy Guard's existing policy engine.

Run locally over stdio with ``python mcp_server.py``.  This module only
translates MCP inputs/outputs; the detection, redaction, policy decision, and
restoration behavior all remain in :mod:`guard_core`.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from fastmcp import FastMCP

from guard_core import (
    PROFILE_POLICIES,
    RISK_LEVELS,
    apply_final_action,
    build_preview,
    get_policy_templates_summary,
    restore_response,
    scan_file_bytes,
    scan_text,
    summarize_findings,
)


mcp = FastMCP(
    "Agent Privacy Guard",
    instructions=(
        "Use these tools to inspect content before it is shared with a remote "
        "model. Do not expose the returned token_map or original_text to an "
        "untrusted destination."
    ),
)


def _require_profile(profile: str) -> None:
    if profile not in PROFILE_POLICIES:
        choices = ", ".join(sorted(PROFILE_POLICIES))
        raise ValueError(f"Unknown profile '{profile}'. Available profiles: {choices}.")


def _scan_result_payload(scan_result: Any) -> dict[str, Any]:
    """Convert an existing guard_core ScanResult into a serializable MCP result."""
    action = scan_result.suggested_action
    return {
        "profile": scan_result.profile,
        "original_text": scan_result.original_text,
        "redacted_text": scan_result.redacted_text,
        "token_map": scan_result.token_map,
        "labels": sorted(scan_result.labels),
        "findings": [
            {"label": label, "count": count}
            for label, count in summarize_findings(scan_result.token_map)
        ],
        "suggested_action": action,
        "risk_level": RISK_LEVELS[action],
        "suggested_sent_text": apply_final_action(scan_result, action),
        "preview_text": build_preview(scan_result),
        "blocked": action == "block",
    }


@mcp.tool()
def list_privacy_profiles() -> list[dict[str, Any]]:
    """List the available privacy-policy profiles and their rule summaries."""
    return get_policy_templates_summary()


@mcp.tool()
def scan_text_content(text: str, profile: str = "coding") -> dict[str, Any]:
    """Scan text and return redaction tokens plus the policy-recommended action.

    This is read-only: it does not write artifacts or send the text to a model.
    """
    _require_profile(profile)
    if not text.strip():
        raise ValueError("text must be a non-empty string")
    return _scan_result_payload(scan_text(text, profile))


@mcp.tool()
def scan_file_content(
    file_name: str,
    content_base64: str,
    profile: str = "coding",
) -> dict[str, Any]:
    """Scan supplied file bytes without reading a path from the local machine.

    ``content_base64`` must be standard base64-encoded file contents. Passing
    content rather than a filesystem path prevents this MCP server from being
    used to read arbitrary local files.
    """
    _require_profile(profile)
    if not file_name.strip():
        raise ValueError("file_name must be a non-empty string")
    try:
        payload = base64.b64decode(content_base64, validate=True)
    except ValueError as exc:
        raise ValueError("content_base64 must be valid base64") from exc

    result = _scan_result_payload(scan_file_bytes(file_name, payload, profile))
    result.update({"file_name": file_name, "file_size": len(payload)})
    return result


@mcp.tool()
def restore_redacted_response(response_text: str, token_map: Mapping[str, str]) -> dict[str, str]:
    """Restore known redaction tokens in a model response using a private token map.

    Only provide a token map created for the same trusted conversation.
    """
    if not response_text:
        raise ValueError("response_text must be a non-empty string")
    return {"restored_text": restore_response(response_text, dict(token_map))}


if __name__ == "__main__":
    mcp.run(transport="stdio")
