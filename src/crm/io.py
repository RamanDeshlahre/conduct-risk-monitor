"""Table read/write helpers. Parquet when pyarrow is installed, gzipped CSV otherwise."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    import pyarrow  # noqa: F401
    _HAS_PARQUET = True
except ImportError:  # pragma: no cover
    _HAS_PARQUET = False


def save_table(df: pd.DataFrame, path_without_ext: Path) -> Path:
    path_without_ext.parent.mkdir(parents=True, exist_ok=True)
    if _HAS_PARQUET:
        out = path_without_ext.with_suffix(".parquet")
        df.to_parquet(out, index=False)
    else:
        out = path_without_ext.with_suffix(".csv.gz")
        df.to_csv(out, index=False)
    return out


def load_table(path_without_ext: Path, parse_dates: list[str] | None = None) -> pd.DataFrame:
    pq = path_without_ext.with_suffix(".parquet")
    csv = path_without_ext.with_suffix(".csv.gz")
    if pq.exists():
        df = pd.read_parquet(pq)
    elif csv.exists():
        df = pd.read_csv(csv)
    else:
        raise FileNotFoundError(f"No table at {pq} or {csv}. Run the earlier pipeline step first.")
    for col in parse_dates or []:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def table_exists(path_without_ext: Path) -> bool:
    return path_without_ext.with_suffix(".parquet").exists() or path_without_ext.with_suffix(".csv.gz").exists()
