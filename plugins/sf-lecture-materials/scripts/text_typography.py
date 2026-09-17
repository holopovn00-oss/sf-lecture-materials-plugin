"""Explicit pre-freeze typography helpers. Never run implicitly on accepted JSON.

Pass only reviewed authorial prose, not code, formulas, citations or UI literals.
Protected ranges use source character offsets; audit records retain both forms.
"""
from __future__ import annotations

import re


def normalize_prose(text, protected=()):
    """Normalize reviewed prose while preserving exact protected source spans."""
    ranges = sorted(protected)
    cursor = 0
    for start, end in ranges:
        if not (type(start) is int and type(end) is int and cursor <= start < end <= len(text)):
            raise ValueError("Invalid or overlapping protected source ranges")
        cursor = end
    def held(index):
        return any(start <= index < end for start, end in ranges)
    replacements = {"ё": "е", "Ё": "Е", "—": "–"}
    changes = [{"start": i, "end": i+1, "before": c, "after": replacements[c]}
               for i, c in enumerate(text) if c in replacements and not held(i)]
    result = list(text)
    for change in changes:
        result[change["start"]] = change["after"]
    return "".join(result), changes


def cover_title(source_title, *, confirmed_ordinal=None):
    """Remove only an explicitly confirmed lesson prefix, never semantic numbers."""
    if confirmed_ordinal is None:
        return source_title
    ordinal = str(confirmed_ordinal)
    if not re.fullmatch(r"[0-9]+", ordinal):
        raise ValueError("Lesson ordinal must be a confirmed decimal number")
    pattern = rf"^\s*(?:(?:Урок|Лекция)\s+)?{re.escape(ordinal)}[.)]\s+(.+)$"
    match = re.fullmatch(pattern, source_title, re.IGNORECASE)
    if not match:
        raise ValueError("Confirmed ordinal does not match an unambiguous lesson prefix")
    return match.group(1)
