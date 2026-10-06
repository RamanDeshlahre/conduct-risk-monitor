"""Firm name normalisation so FCA and FOS records join on the same key.

FCA writes 'NATIONAL WESTMINSTER BANK PUBLIC LIMITED COMPANY' and FOS writes
'National Westminster Bank Public Limited Company'. Both should land on one key.
Anything the rules miss goes in dbt/seeds/firm_name_overrides.csv, which is
version-controlled and reviewable: a small governance win.
"""
from __future__ import annotations

import re
import unicodedata

_SUFFIXES = [
    "public limited company", "plc", "p l c", "limited", "ltd", "llp", "llc",
    "designated activity company", "dac", "d a c", "s a", "sa", "se", "ag", "ab", "nv", "n v",
    "the", "publ", "company", "co",
]
_SUFFIX_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in _SUFFIXES) + r")\b")


def firm_key(name: str | None) -> str:
    if not name or not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\(.*?\)", lambda m: " " + m.group(0)[1:-1] + " ", text)  # keep bracket content, drop brackets
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\btrading as\b.*$", " ", text)
    text = _SUFFIX_RE.sub(" ", text)
    return re.sub(r"\s+", "_", text.strip())
