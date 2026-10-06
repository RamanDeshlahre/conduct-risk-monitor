"""Rule-based theme tagging mapped to Consumer Duty outcomes, plus NMF theme discovery."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from .config import ROOT

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    outcome: str
    rationale: str
    regex: re.Pattern


@lru_cache(maxsize=1)
def load_taxonomy(path: str | None = None) -> tuple[list[Theme], dict[str, str]]:
    with open(Path(path) if path else ROOT / "taxonomy" / "themes.yaml", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    themes = [
        Theme(t["id"], t["label"], t["consumer_duty_outcome"], t["rationale"],
              re.compile("|".join(f"(?:{p})" for p in t["patterns"]), re.I))
        for t in raw["themes"]
    ]
    return themes, raw["consumer_duty_outcomes"]


def tag(text: str) -> list[str]:
    themes, _ = load_taxonomy()
    if not isinstance(text, str) or not text:
        return []
    return [t.id for t in themes if t.regex.search(text)]


def tag_frame(df: pd.DataFrame, text_col: str) -> pd.DataFrame:
    themes, outcomes = load_taxonomy()
    by_id = {t.id: t for t in themes}
    tags = df[text_col].map(tag)
    out = df.copy()
    out["themes"] = tags.map(lambda xs: "|".join(xs))
    out["primary_theme"] = tags.map(lambda xs: xs[0] if xs else "other")
    out["theme_label"] = out["primary_theme"].map(lambda i: by_id[i].label if i in by_id else "Other")
    out["consumer_duty_outcome"] = out["primary_theme"].map(lambda i: by_id[i].outcome if i in by_id else "unmapped")
    out["consumer_duty_label"] = out["consumer_duty_outcome"].map(outcomes)
    return out


# Words that say who is complaining or about whom, not what about. Without these the topics are just
# lists of bank names ("barclays, uk plc, barclays bank...") and titles.
DISCOVERY_STOP_WORDS = {
    "mr", "mrs", "miss", "ms", "dr", "complains", "complained", "complaint", "complaining", "says", "said",
    "unhappy", "trading", "plc", "limited", "ltd", "uk", "bank", "company", "public", "services", "financial",
    "didn", "doesn", "wasn", "isn", "won", "hasn", "haven", "couldn", "wouldn", "shouldn",
}


def firm_name_words(names: pd.Series) -> set[str]:
    words: set[str] = set()
    for name in names.dropna().unique():
        words.update(w for w in re.findall(r"[a-z][a-z]+", str(name).lower()) if len(w) > 1)
    return words


def discover(texts: pd.Series, n_topics: int = 12, top_terms: int = 12, random_state: int = 42,
             extra_stop_words: set[str] | None = None) -> list[list[str]]:
    """Find recurring language in complaints the taxonomy didn't catch."""
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

    texts = texts.dropna()
    texts = texts[texts.str.len() > 30]
    if len(texts) < n_topics * 20:
        log.warning("Only %d untagged texts; discovery skipped.", len(texts))
        return []
    stop = sorted(set(ENGLISH_STOP_WORDS) | DISCOVERY_STOP_WORDS | (extra_stop_words or set()))
    vec = TfidfVectorizer(stop_words=stop, ngram_range=(1, 2), min_df=5, max_df=0.5,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b")
    X = vec.fit_transform(texts)
    nmf = NMF(n_components=n_topics, random_state=random_state, init="nndsvda", max_iter=400)
    nmf.fit(X)
    vocab = vec.get_feature_names_out()
    return [[vocab[i] for i in comp.argsort()[::-1][:top_terms]] for comp in nmf.components_]
