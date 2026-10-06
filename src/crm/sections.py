"""Split Financial Ombudsman decisions into their standard sections.

Every final decision follows the same skeleton:
    The complaint / What happened / What I've decided - and why /
    Putting things right (only if upheld) / My final decision

Leakage is the trap here. "What happened" usually says what the investigator
concluded ("Our investigator ... upheld it"), and the ombudsman agrees with the
investigator most of the time. A model trained on that section learns to read
the answer, not to predict it. So the model only ever sees "The complaint",
which describes the problem as it stood when the case arrived.
"""
from __future__ import annotations

import re

APOS = "['\u2019]"
DASH = "[-\u2013\u2014]"

HEADINGS: list[tuple[str, re.Pattern]] = [
    ("complaint", re.compile(r"^\s*the complaint\s*$", re.I)),
    ("what_happened", re.compile(r"^\s*what happened\s*$", re.I)),
    ("provisional", re.compile(rf"^\s*what i{APOS}?ve provisionally decided.*$", re.I)),
    ("reasons", re.compile(rf"^\s*what i{APOS}?ve decided\s*{DASH}?\s*and why\s*$", re.I)),
    ("putting_right", re.compile(r"^\s*putting things right\s*$", re.I)),
    ("final_decision", re.compile(r"^\s*my final decision\s*$", re.I)),
]

NOISE_LINE = re.compile(r"^\s*(DRN-\d+|-?\s*\d+\s*-?|page \d+ of \d+)\s*$", re.I)

# Phrases that reveal the outcome or the process stage. If they appear in model text, it leaks.
LEAKY_PATTERNS = [
    r"\binvestigator\b", r"\bombudsman\b", r"\buph[eo]ld\w*\b",
    r"\bfinal decision\b", r"\bprovisional decision\b", r"\bfair and reasonable\b",
    r"\bput things right\b", r"\bredress\b", r"\bcompensation award\b",
]
LEAKY_RE = re.compile("|".join(LEAKY_PATTERNS), re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
MONEY_RE = re.compile(r"\u00a3\s?([\d,]+(?:\.\d{2})?)")


def split_sections(text: str) -> dict[str, str]:
    """Return a dict of section name -> text. Missing sections come back as ''."""
    out = {name: [] for name, _ in HEADINGS}
    current = None
    for line in text.splitlines():
        if NOISE_LINE.match(line):
            continue
        matched = next((name for name, pat in HEADINGS if pat.match(line)), None)
        if matched:
            current = matched
            continue
        if current:
            out[current].append(line.strip())
    return {k: re.sub(r"\s+", " ", " ".join(v)).strip() for k, v in out.items()}


def complaint_from_snippet(snippet: str) -> str:
    """Listing pages show 'The complaint ... What happened ...' truncated. Keep the complaint part."""
    if not isinstance(snippet, str):
        return ""
    text = re.sub(r"^\s*DRN-\d+\s*", "", snippet)
    # Most ombudsmen use "The complaint"; some write just "Complaint". Either way the heading goes,
    # because a house style that identifies one ombudsman would let the model learn who decided the case.
    text = re.sub(r"^\s*(?:The\s+)?complaint\b\s*", "", text, flags=re.I)
    text = re.split(r"(?i:\bWhat happened\b)|\bBackground\b(?=\s+[A-Z])", text, maxsplit=1)[0]
    return text.rstrip(". \u2026").strip()


def strip_leaky_sentences(text: str) -> str:
    if not text:
        return ""
    kept = [s for s in SENTENCE_SPLIT.split(text) if not LEAKY_RE.search(s)]
    return " ".join(kept).strip()


def leakage_rate(texts) -> float:
    texts = [t for t in texts if isinstance(t, str) and t]
    if not texts:
        return 0.0
    return sum(bool(LEAKY_RE.search(t)) for t in texts) / len(texts)


def outcome_from_final_decision(final_text: str) -> str | None:
    """Independent read of the outcome, used as a data quality cross-check against the listing."""
    if not final_text:
        return None
    t = final_text.lower()
    if re.search(r"\b(do not|don['\u2019]t|not)\s+uphold\b", t):
        return "Not upheld"
    if re.search(r"\buphold\b", t):
        return "Upheld"
    return None


def redress_amounts(putting_right: str) -> list[float]:
    return [float(m.replace(",", "")) for m in MONEY_RE.findall(putting_right or "")]
