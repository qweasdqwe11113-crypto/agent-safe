#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def build_file_analysis_prompt(output_path: Path, final_action: str) -> str:
    mode_text = "approved original" if final_action == "allow" else "sanitized"
    absolute_output_path = output_path.resolve()
    file_name = absolute_output_path.name
    return (
        f"Please analyze the {mode_text} content in this file: "
        f"[{file_name}](<{absolute_output_path}>). "
        f"Absolute path: {absolute_output_path}. "
        "Treat this file as the approved context prepared by Agent Privacy Guard."
    )


def build_session_prompt(history: list[dict[str, str]], current_message: str, profile: str) -> str:
    sections = [
        "You are continuing an ongoing session protected by Agent Privacy Guard.",
        f"Active privacy profile: {profile}.",
        "",
        "Conversation so far:",
    ]

    if history:
        for turn in history:
            sections.append(f"User: {turn['user']}")
            sections.append(f"Assistant: {turn['assistant']}")
            sections.append("")
    else:
        sections.append("(No previous conversation)")
        sections.append("")

    sections.extend(
        [
            "Current user message:",
            current_message,
        ]
    )
    return "\n".join(sections)


def run_codex_turn(
    prompt: str,
    output_path: Path | None = None,
    profile: str | None = None,
    workdir: Path | None = None,
) -> subprocess.CompletedProcess:
    codex_executable = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if not codex_executable:
        raise FileNotFoundError(
            "Could not find Codex CLI executable. Make sure `codex` is installed and available in PATH."
        )

    command = [codex_executable, "exec"]
    if profile:
        command.extend(["--profile", profile])
    command.append(prompt)
    command.extend(["-C", str((workdir or PROJECT_ROOT).resolve())])
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["-o", str(output_path)])

    return subprocess.run(command, text=True, check=True, env=os.environ.copy(), capture_output=True)


def extract_assistant_reply(stdout: str) -> str:
    lines = stdout.splitlines()
    assistant_chunks: list[list[str]] = []
    index = 0

    while index < len(lines):
        if lines[index].strip() == "codex":
            index += 1
            chunk: list[str] = []
            while index < len(lines):
                current = lines[index].strip()
                if current == "codex":
                    break
                if current.startswith("tokens used") or current == "exec" or current == "user":
                    break
                chunk.append(lines[index])
                index += 1
            assistant_chunks.append(chunk)
            continue
        index += 1

    if not assistant_chunks:
        return ""
    return "\n".join(assistant_chunks[-1]).strip()
