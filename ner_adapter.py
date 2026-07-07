#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class EntitySpan:
    start: int
    end: int
    label: str
    text: str
    source: str


NAME_CUE_PATTERNS = [
    re.compile(r"(?:(?:我叫|姓名是|姓名|名字是|名字|联系人是|联系人|收件人是|收件人)\s*[:：]?\s*)([\u4e00-\u9fff]{2,4})"),
    re.compile(r"(?:(?:my name is|name is|contact is|recipient is)\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"),
]

ADDRESS_CUE_PATTERNS = [
    re.compile(
        r"(?:(?:地址是|地址|收货地址是|收货地址|家庭住址是|家庭住址|住址是|住址|住在|送到)\s*[:：]?\s*)"
        r"([^\n\r,，。；;]{6,80}(?:省|市|区|县|镇|乡|街|路|道|巷|号|栋|单元|室)[^\n\r,，。；;]{0,40})"
    ),
    re.compile(
        r"(?:(?:address is|shipping address is|shipping address|home address is|home address)\s+)"
        r"([A-Za-z0-9][^,\n\r]{8,100})"
    ),
]

ID_CUE_PATTERNS = [
    re.compile(r"(?:(?:身份证号是|身份证号|身份证是|身份证|证件号是|证件号)\s*[:：]?\s*)(\d{17}[\dXx])"),
    re.compile(r"(?:(?:passport number is|passport no\.?\s*|id number is)\s*)([A-Za-z0-9-]{6,20})", re.IGNORECASE),
]


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


@lru_cache(maxsize=1)
def _load_backend():
    backend = os.environ.get("APG_NER_BACKEND", "").strip().lower()

    if backend in {"hanlp", ""} and _has_module("hanlp"):
        import hanlp  # type: ignore

        model_name = os.environ.get("APG_HANLP_NER_MODEL", "MSRA_NER_ELECTRA_SMALL_ZH")
        try:
            pretrained_model = getattr(getattr(hanlp.pretrained, "ner", object()), model_name, model_name)
            model = hanlp.load(pretrained_model)
            return ("hanlp", model)
        except Exception:
            pass

    if backend in {"spacy", ""} and _has_module("spacy"):
        import spacy  # type: ignore

        model_name = os.environ.get("APG_SPACY_MODEL", "zh_core_web_sm")
        try:
            model = spacy.load(model_name)
            return ("spacy", model)
        except Exception:
            pass

    return ("heuristic", None)


def _detect_with_hanlp(text: str, model) -> list[EntitySpan]:
    spans: list[EntitySpan] = []
    try:
        result = model(text)
    except Exception:
        return spans

    for item in result:
        if len(item) < 3:
            continue
        entity_text, label, start = item[0], item[1], item[2]
        if not isinstance(entity_text, str) or not isinstance(label, str) or not isinstance(start, int):
            continue
        mapped = _map_backend_label(label)
        if not mapped:
            continue
        spans.append(EntitySpan(start=start, end=start + len(entity_text), label=mapped, text=entity_text, source="hanlp"))
    return spans


def _detect_with_spacy(text: str, model) -> list[EntitySpan]:
    spans: list[EntitySpan] = []
    try:
        doc = model(text)
    except Exception:
        return spans

    for ent in doc.ents:
        mapped = _map_backend_label(ent.label_)
        if not mapped:
            continue
        spans.append(
            EntitySpan(
                start=ent.start_char,
                end=ent.end_char,
                label=mapped,
                text=ent.text,
                source="spacy",
            )
        )
    return spans


def _map_backend_label(label: str) -> str | None:
    normalized = label.upper()
    if normalized in {"PER", "PERSON", "NR"}:
        return "PERSON_NAME"
    if normalized in {"LOC", "GPE", "ADDRESS", "NS"}:
        return "STREET_ADDRESS"
    if normalized in {"ID", "CARD", "IDCARD"}:
        return "NATIONAL_ID"
    return None


def _detect_with_heuristics(text: str) -> list[EntitySpan]:
    spans: list[EntitySpan] = []

    for pattern in NAME_CUE_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.group(1).strip()
            if len(captured) < 2:
                continue
            start = match.start(1)
            end = match.end(1)
            spans.append(EntitySpan(start=start, end=end, label="PERSON_NAME", text=captured, source="heuristic"))

    for pattern in ADDRESS_CUE_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.group(1).strip()
            if len(captured) < 6:
                continue
            start = match.start(1)
            end = match.end(1)
            spans.append(EntitySpan(start=start, end=end, label="STREET_ADDRESS", text=captured, source="heuristic"))

    for pattern in ID_CUE_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.group(1).strip()
            start = match.start(1)
            end = match.end(1)
            spans.append(EntitySpan(start=start, end=end, label="NATIONAL_ID", text=captured, source="heuristic"))

    return _dedupe_spans(spans)


def _dedupe_spans(spans: list[EntitySpan]) -> list[EntitySpan]:
    kept: list[EntitySpan] = []
    for span in sorted(spans, key=lambda item: (item.start, -(item.end - item.start))):
        overlaps = False
        for existing in kept:
            if not (span.end <= existing.start or span.start >= existing.end):
                overlaps = True
                break
        if not overlaps:
            kept.append(span)
    return kept


def detect_entities(text: str) -> list[EntitySpan]:
    backend_name, backend_model = _load_backend()
    if backend_name == "hanlp":
        spans = _detect_with_hanlp(text, backend_model)
        if spans:
            return _dedupe_spans(spans)
    elif backend_name == "spacy":
        spans = _detect_with_spacy(text, backend_model)
        if spans:
            return _dedupe_spans(spans)
    return _detect_with_heuristics(text)
