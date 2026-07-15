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
    re.compile(r"(?:(?:我是|本人是)\s*[:：]?\s*)([\u4e00-\u9fff]{2,4})(?=$|[，,。.!！?？；;、\s])"),
    re.compile(r"(?:(?:my name is|name is|contact is|recipient is)\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"),
    re.compile(
        r"(?:(?:请)?(?:联系|通知|找|抄送给|发送给|交给)|(?:负责人|客户|用户|收件人)是)\s*[:：]?\s*"
        r"((?:欧阳|司马|上官|诸葛|东方|尉迟|公孙|慕容|司徒|夏侯|皇甫|令狐|"
        r"赵|钱|孙|李|周|吴|郑|王|冯|陈|褚|卫|蒋|沈|韩|杨|朱|秦|尤|许|何|吕|施|张|孔|曹|严|华|金|魏|陶|姜|戚|谢|邹|喻|柏|水|窦|章|云|苏|潘|葛|奚|范|彭|郎|鲁|韦|昌|马|苗|凤|花|方|俞|任|袁|柳|鲍|史|唐|费|廉|岑|薛|雷|贺|倪|汤|滕|殷|罗|毕|郝|邬|安|常|乐|于|时|傅|皮|卞|齐|康|伍|余|元|卜|顾|孟|平|黄|和|穆|萧|尹|姚|邵|汪|祁|毛|禹|狄|米|贝|明|臧|计|伏|成|戴|宋|茅|庞|熊|纪|舒|屈|项|祝|董|梁|杜|阮|蓝|闵|席|季|麻|强|贾|路|娄|危|江|童|颜|郭|梅|盛|林|刁|钟|徐|邱|骆|高|夏|蔡|田|樊|胡|凌|霍|虞|万|支|柯|管|卢|莫|房|裘|缪|解|应|宗|丁|宣|邓|郁|单|杭|洪|包|左|石|崔|吉|龚|程|嵇|邢|裴|陆|荣|翁|荀|羊|甄|曲|家|封|芮|储|靳|汲|邴|糜|松|井|段|富|巫|乌|焦|巴|弓|牧|隗|山|谷|车|侯|宓|蓬|全|班|仰|秋|仲|伊|宫|宁|仇|栾|暴|甘|钭|厉|戎|祖|武|符|刘|景|詹|束|龙|叶|幸|司|韶|黎|蓟|薄|印|宿|白|怀|蒲|台|从|鄂|索|咸|籍|赖|卓|蔺|屠|蒙|池|乔|阴|胥|能|苍|双|闻|莘|党|翟|谭|贡|劳|逄|姬|申|扶|堵|冉|宰|郦|雍|璩|桑|桂|濮|牛|寿|通|边|扈|燕|冀|浦|尚|农|温|别|庄|晏|柴|瞿|阎|连|习|艾|鱼|容|向|古|易|慎|戈|廖|庾|终|暨|居|衡|步|都|耿|满|弘|匡|国|文|寇|广|禄|阙|东|欧|利|师|巩|聂|晁|勾|敖|融|冷|辛|阚|那|简|饶|空|曾|毋|沙|养|鞠|须|丰|巢|关|蒯|相|查|后|荆|红|游|竺|权|逯|盖|益|桓|公)[\u4e00-\u9fff]{1,2})"
        r"(?=$|[，,。.!！?？；;、\s]|处理|确认|负责|回复|参加|查收|收件)",
    ),
    re.compile(
        r"(?:(?i:contact|notify|email|call|recipient|assignee|owner|customer|client)"
        r"\s*(?:(?i:is)\s*)?[:：]?\s*)"
        r"([A-Z][a-z]+(?:[-'][A-Za-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Za-z]+)?){1,2})"
    ),
]

ADDRESS_CUE_PATTERNS = [
    re.compile(
        r"(?:(?:地址是|地址|收货地址是|收货地址|家庭住址是|家庭住址|住址是|住址|住在|送到|寄往|寄到|配送到|邮寄到|办公地址是|公司地址是)\s*[:：]?\s*)"
        r"([^\n\r,，。；;]{6,80}(?:省|市|区|县|镇|乡|街|路|道|巷|号|栋|单元|室)[^\n\r,，。；;]{0,40})"
    ),
    re.compile(
        r"(?:(?i:address is|shipping address is|shipping address|home address is|home address)\s+)"
        r"([A-Za-z0-9][^,\n\r]{8,100})"
    ),
    re.compile(
        r"(?:(?i:ship to|deliver to|send to|mail to|located at|office at)\s+)"
        r"(\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,6}\s+"
        r"(?i:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|Highway|Hwy)\.?)"
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
    heuristic_spans = _detect_with_heuristics(text)
    backend_name, backend_model = _load_backend()
    if backend_name == "hanlp":
        spans = _detect_with_hanlp(text, backend_model)
        if spans:
            return _dedupe_spans(spans + heuristic_spans)
    elif backend_name == "spacy":
        spans = _detect_with_spacy(text, backend_model)
        if spans:
            return _dedupe_spans(spans + heuristic_spans)
    return heuristic_spans
