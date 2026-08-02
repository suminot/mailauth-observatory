"""Parquet / JSONL の読み書き。

冪等性（原則6）のために、書き出しは必ず
  1. 列をスキーマ順に揃える
  2. 決まったキーでソートする
  3. PyArrow スキーマを明示して型を固定する
の順に行う。これをやらないと、ある月に全件 NULL になった列の型が
推論で揺れて月次結合が壊れる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel


def records_to_frame(records: list[BaseModel] | list[dict], schema: pa.Schema) -> pd.DataFrame:
    """Pydantic モデル列 or dict 列を、スキーマ順の DataFrame にする。"""
    rows: list[dict[str, Any]] = [
        r.model_dump() if isinstance(r, BaseModel) else dict(r) for r in records
    ]
    names = [f.name for f in schema]
    if not rows:
        return pd.DataFrame({n: pd.Series(dtype="object") for n in names})

    df = pd.DataFrame(rows)
    for n in names:
        if n not in df.columns:
            df[n] = None
    extra = [c for c in df.columns if c not in names]
    if extra:
        raise ValueError(f"スキーマに無い列があります: {extra}")
    return df[names]


def write_parquet(
    records: list[BaseModel] | list[dict] | pd.DataFrame,
    path: Path | str,
    schema: pa.Schema,
    sort_keys: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> int:
    """スキーマを固定して Parquet を書き、書き込んだ行数を返す。"""
    df = records if isinstance(records, pd.DataFrame) else records_to_frame(records, schema)
    if sort_keys and len(df) > 0:
        df = df.sort_values(sort_keys, kind="mergesort", na_position="last").reset_index(drop=True)

    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    if metadata:
        existing = table.schema.metadata or {}
        merged = {**existing, **{k.encode(): str(v).encode() for k, v in metadata.items()}}
        table = table.replace_schema_metadata(merged)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 圧縮とバージョンを固定して、同じ入力で同じファイルになるようにする
    pq.write_table(table, out, compression="zstd", version="2.6")
    return len(df)


def read_parquet(path: Path | str) -> pd.DataFrame | None:
    """無ければ None。前月データが存在しないのは正常な状態。"""
    p = Path(path)
    if not p.is_file():
        return None
    return pq.read_table(p).to_pandas()


def write_jsonl(records: list[BaseModel] | list[dict], path: Path | str) -> int:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            obj = r.model_dump(mode="json") if isinstance(r, BaseModel) else r
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n
