"""画面3 レコード検査 ── 1ドメインを bronze から gold まで縦に並べる。

**「なぜこの判定になったか」を追跡する主要な手段**である（DESIGN.md 7.2）。
コンソールの受け入れ基準にも「任意のドメインについて bronze から gold までの
経路を追跡できること」が入っている。

原則2（事実と推察を混ぜない）をそのまま画面の構造にしてある。
  - bronze: 生の DNS 応答。加工していない
  - fact:   仕様に照らした解釈。ここまでは事実
  - inference: 辞書との照合による推定。確度と根拠が付く
  - gold:   このドメインがどの集計セルに寄与したか

**gold への寄与は「どのセルに入ったか」までしか出さない。** 個社の数字を
gold から逆算して見せる画面ではない。秘匿の意味が無くなる。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mailauth.contracts import SUPPRESSED_SECTOR_CODE
from mailauth.io import read_parquet
from mailauth.p4_measure.bronze import iter_bronze_files, read_bronze
from mailauth.paths import bronze_dir, gold_dir, phase_output, run_dir

from .inspect import _jsonable

router = APIRouter(prefix="/api/trace", tags=["trace"])

#: 1ドメインの bronze 行数。DKIM を50セレクタ引くと簡単に増える
MAX_BRONZE_ROWS = 400


def _rows(path) -> list[dict]:
    frame = read_parquet(path)
    if frame is None:
        return []
    return [
        {k: _jsonable(v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _first(rows: list[dict], **match: Any) -> dict | None:
    for row in rows:
        if all(row.get(k) == v for k, v in match.items()):
            return row
    return None


@router.get("/{run_id}")
def trace(
    run_id: str,
    domain: str = Query(..., description="追跡するドメイン名"),
) -> dict[str, Any]:
    """1ドメインの全工程の記録を返す。"""
    if not run_dir(run_id).is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} がありません")

    target = domain.strip().rstrip(".").lower()
    domains = _rows(phase_output(run_id, "p3_domains", "domains.parquet"))
    domain_row = _first(domains, domain=target)

    candidates = [
        c
        for c in _rows(phase_output(run_id, "p2_candidates", "domain_candidates.parquet"))
        if c.get("domain") == target
    ]

    if domain_row is None and not candidates:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{target} は {run_id} の候補にも確定ドメインにも現れていない。"
                "P2 が候補として拾えていない可能性がある"
            ),
        )

    entity_id = (domain_row or candidates[0]).get("entity_id")
    entity = _first(
        _rows(phase_output(run_id, "p1_population", "entities.parquet")),
        entity_id=entity_id,
    )
    domain_id = (domain_row or {}).get("domain_id")

    fact = (
        _first(_rows(phase_output(run_id, "p5_parse", "facts.parquet")), domain_id=domain_id)
        if domain_id
        else None
    )
    inferences = [
        i
        for i in _rows(phase_output(run_id, "p6_infer", "inferences.parquet"))
        if i.get("domain_id") == domain_id
    ]
    for inference in inferences:
        # evidence は JSON 文字列で入っている。画面で組み立て直させない
        raw = inference.get("evidence")
        if isinstance(raw, str):
            try:
                inference["evidence"] = json.loads(raw)
            except json.JSONDecodeError:
                pass

    return {
        "run_id": run_id,
        "domain": target,
        "entity": entity,
        "candidates": candidates,
        "domain_row": domain_row,
        "bronze": _bronze_for(run_id, target),
        "fact": fact,
        "inferences": inferences,
        "gold": _gold_contribution(run_id, entity),
    }


def _bronze_for(run_id: str, domain: str) -> dict[str, Any]:
    """そのドメイン宛の生応答を purpose 別に並べる。

    bronze は加工しない（原則1）。**ここでも整形しない。** 見えているものが
    保存されているものと同じであることが、この画面の価値の前提である。
    """
    files = iter_bronze_files(bronze_dir(run_id))
    matched: list[dict] = []
    truncated = False
    for path in files:
        for record in read_bronze(path):
            if record.get("domain") != domain:
                continue
            if len(matched) >= MAX_BRONZE_ROWS:
                truncated = True
                break
            matched.append(record)
        if truncated:
            break

    by_purpose: dict[str, list[dict]] = {}
    for record in matched:
        by_purpose.setdefault(str(record.get("purpose")), []).append(record)

    return {
        "files": [p.name for p in files],
        "total": len(matched),
        "truncated": truncated,
        "by_purpose": {k: by_purpose[k] for k in sorted(by_purpose)},
        # 観測できなかったクエリを別に数える。「無かった」と混ぜない（原則5）
        "not_observed": sum(1 for r in matched if not r.get("observed")),
    }


def _gold_contribution(run_id: str, entity: dict | None) -> dict[str, Any]:
    """このドメインがどの集計セルに寄与したか。

    **個社の数字は出さない。** どのセルに入ったかだけを示す。gold から
    個社を逆算できる画面にすると、セル秘匿の意味が無くなる。
    """
    month = run_id[:7]
    if entity is None:
        return {
            "month": month,
            "reason": "母集団に企業が見つからないため、集計には寄与していない",
            "cells": [],
        }

    populations = list(entity.get("population_ids") or [])
    common12 = entity.get("common12_code")
    sectors = _rows(gold_dir(month) / "stats_by_sector.parquet")

    cells = []
    for population_id in populations:
        published = _first(
            sectors, population_id=population_id, common12_code=common12
        )
        merged = _first(
            sectors, population_id=population_id, common12_code=SUPPRESSED_SECTOR_CODE
        )
        if published is not None:
            cells.append(
                {
                    "population_id": population_id,
                    "common12_code": common12,
                    "common12_label": published.get("common12_label"),
                    "suppressed": False,
                    "n_entities": published.get("n_entities"),
                }
            )
        elif merged is not None and common12:
            cells.append(
                {
                    "population_id": population_id,
                    "common12_code": SUPPRESSED_SECTOR_CODE,
                    "common12_label": merged.get("common12_label"),
                    "suppressed": True,
                    "n_entities": merged.get("n_entities"),
                    "note": "n<5 のため「その他」に束ねられている",
                }
            )
        else:
            cells.append(
                {
                    "population_id": population_id,
                    "common12_code": common12,
                    "suppressed": None,
                    "note": (
                        "業種別集計に現れていない。共通12分類が付いていないか、"
                        "P7 をまだ実行していない"
                    ),
                }
            )

    return {
        "month": month,
        "populations": populations,
        "common12_code": common12,
        "cells": cells,
        "note": (
            "個社の数字は出していない。どの集計セルに寄与したかだけを示す。"
            "gold から個社を逆算できるとセル秘匿の意味が無くなる"
        ),
    }
