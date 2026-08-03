"""ビュー ── 同じ計測結果を別の軸で切り替えて見る。

全上場を業種軸で見たいときと、プライムだけを見たいときがある。
これを「母集団を変えて計測し直す」で実現すると、計測を何度も回すことになり、
月次の比較もできなくなる。そこで計測は一度きり（全上場）にして、
見るときに絞る。ビューはその絞り方の定義である。

ビューは集計軸であって計測対象ではない。
`configs/populations/` が「何を測るか」、`configs/views/` が「どう見るか」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .contracts import EntityStatus
from .paths import config_path

#: 集計に使える軸。entities.parquet の列名に対応する。
GROUP_BY_COLUMNS = {
    "common12": ("common12_code", "common12_label"),
    "industry": ("industry_label", "industry_label"),
    "segment": ("market_segment", "market_segment"),
    "country": ("country", "country"),
    "status": ("status", "status"),
}


class ViewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    population_ids: list[str] | None = None
    market_segment: list[str] | None = None
    country: list[str] | None = None
    common12_code: list[str] | None = None
    #: 既定では delisted を除く。「消えた会社」を現況の分母に入れない
    status: list[str] = Field(default_factory=lambda: [EntityStatus.ACTIVE, EntityStatus.RENAMED])


class ViewConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    description: str | None = None
    filter: ViewFilter = Field(default_factory=ViewFilter)
    default_group_by: str = "common12"
    #: このビューが市場区分に依存するか。依存するのに区分データが無ければ警告する
    requires_segment: bool = False
    source_path: Path | None = None


def load_view(path: str | Path) -> ViewConfig:
    p = config_path(str(path))
    if not p.is_file():
        raise FileNotFoundError(f"ビュー定義が見つかりません: {p}")
    cfg = ViewConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    cfg.source_path = p
    return cfg


def list_views(directory: str | Path = "configs/views") -> list[ViewConfig]:
    d = config_path(str(directory))
    if not d.is_dir():
        return []
    return [load_view(f) for f in sorted(d.glob("*.yaml"))]


def get_view(view_id: str, directory: str | Path = "configs/views") -> ViewConfig:
    for cfg in list_views(directory):
        if cfg.id == view_id:
            return cfg
    raise KeyError(f"ビュー {view_id} がありません")


@dataclass
class ViewResult:
    view_id: str
    label: str
    group_by: str
    total: int
    groups: list[dict[str, Any]] = field(default_factory=list)
    #: 区分データの有無。ビューが空でも「該当なし」と「データが無い」を区別する（原則5）
    segment_data_available: bool = True
    warnings: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view_id": self.view_id,
            "label": self.label,
            "group_by": self.group_by,
            "total": self.total,
            "groups": self.groups,
            "segment_data_available": self.segment_data_available,
            "warnings": self.warnings,
            "coverage": self.coverage,
        }


def _isin(series: pd.Series, values: list[str]) -> pd.Series:
    return series.astype("string").isin(values)


def apply_view(df: pd.DataFrame, view: ViewConfig) -> pd.DataFrame:
    """ビューのフィルタを DataFrame に適用する。"""
    out = df
    f = view.filter

    if f.status:
        out = out[_isin(out["status"], f.status)]
    if f.country:
        out = out[_isin(out["country"], f.country)]
    if f.market_segment:
        out = out[_isin(out["market_segment"], f.market_segment)]
    if f.common12_code:
        out = out[_isin(out["common12_code"], f.common12_code)]
    if f.population_ids:
        wanted = set(f.population_ids)
        out = out[
            out["population_ids"].apply(
                lambda v: bool(wanted & set(v)) if v is not None and len(v) else False
            )
        ]
    return out


def summarize(df: pd.DataFrame, view: ViewConfig, group_by: str | None = None) -> ViewResult:
    """ビューを適用して集計する。

    区分データが無いのに区分に依存するビューを見ようとした場合、
    総数0を「該当企業が無い」と誤読させないよう明示的に警告する。
    """
    axis = group_by or view.default_group_by
    if axis not in GROUP_BY_COLUMNS:
        raise ValueError(f"不明な集計軸: {axis}（使えるのは {', '.join(GROUP_BY_COLUMNS)}）")

    segment_available = bool(df["market_segment"].notna().any()) if len(df) else False
    warnings: list[str] = []
    if view.requires_segment and not segment_available:
        warnings.append(
            f"ビュー {view.id} は市場区分に依存しますが、区分データが付いていません。"
            "総数が0なのは該当企業が無いからではなく、区分を判定できないためです。"
            "configs/populations/_segments/ に対応表を置いて "
            "市場区分の対応表を market_filter.segment_map に指定してください"
        )

    filtered = apply_view(df, view)
    code_col, label_col = GROUP_BY_COLUMNS[axis]

    groups: list[dict[str, Any]] = []
    if len(filtered):
        grouped = (
            filtered.assign(
                _code=filtered[code_col].astype("string").fillna("(未分類)"),
                _label=filtered[label_col].astype("string").fillna("(未分類)"),
            )
            .groupby(["_code", "_label"], dropna=False)
            .size()
            .reset_index(name="n")
        )
        # common12 は数値順、それ以外は件数の多い順
        if axis == "common12":
            grouped["_sort"] = pd.to_numeric(grouped["_code"], errors="coerce").fillna(9999)
            grouped = grouped.sort_values(["_sort", "_code"])
        else:
            grouped = grouped.sort_values(["n", "_code"], ascending=[False, True])
        total = int(grouped["n"].sum())
        for _, row in grouped.iterrows():
            groups.append(
                {
                    "code": row["_code"],
                    "label": row["_label"],
                    "n": int(row["n"]),
                    "share": round(int(row["n"]) / total, 4) if total else 0.0,
                }
            )
    total = int(len(filtered))

    coverage = {
        "entities_in_run": int(len(df)),
        "entities_in_view": total,
        "with_official_domain": int(filtered["official_domain"].notna().sum()) if total else 0,
        "with_common12": int(filtered["common12_code"].notna().sum()) if total else 0,
        "with_segment": int(filtered["market_segment"].notna().sum()) if total else 0,
    }

    return ViewResult(
        view_id=view.id,
        label=view.label,
        group_by=axis,
        total=total,
        groups=groups,
        segment_data_available=segment_available,
        warnings=warnings,
        coverage=coverage,
    )
