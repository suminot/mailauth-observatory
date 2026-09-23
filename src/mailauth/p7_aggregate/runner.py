"""P7 集計の本体。

silver（facts + inferences）から gold を作る。**gold は Git にコミットする**
唯一の層なので、ここが公開物の内容を決める。したがって次を守る。

  - 採用率の分母は観測できたドメインだけ（原則5）
  - n<5 の業種セルは秘匿し、秘匿セルが1つになるなら2次秘匿を行う
  - 「消えた」と「取れなかった」を混同しない
  - `market_segment` は内部専用なので **gold には出さない**（DESIGN.md P1）
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..contracts import (
    STATS_BY_SECTOR_ARROW_SCHEMA,
    STATS_BY_SECTOR_SORT_KEYS,
    STATS_OVERALL_ARROW_SCHEMA,
    STATS_OVERALL_SORT_KEYS,
    SUPPRESSED_SECTOR_CODE,
    SUPPRESSED_SECTOR_LABEL,
    EntityStatus,
    SpecVersion,
    StatsBySector,
)
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest
from ..paths import gold_dir, month_date, phase_dir, phase_output, previous_run_id
from . import delta as delta_mod
from . import suppress
from .metrics import DomainRow, aggregate, park_classes

PHASE = "p7_aggregate"
OVERALL_FILENAME = "stats_overall.parquet"
BY_SECTOR_FILENAME = "stats_by_sector.parquet"
AGGREGATOR_VERSION = "1.0.0"

#: 現況の分母に入れる企業の状態。delisted（上場廃止）は入れない
ACTIVE_STATUSES = (EntityStatus.ACTIVE, EntityStatus.RENAMED)


class MissingInputError(RuntimeError):
    pass


def _merge_other_populations(
    new_rows: list[dict],
    existing_path: Path,
    manifest: RunManifest,
    *,
    what: str,
) -> list[dict]:
    """同じ月の gold にある**他の母集団の行を残す。**

    gold のパスは `gold/month=YYYY-MM/` で母集団を含まない。行そのものは
    `population_id` を持ち、P8 は全母集団を縦に積んで出すので、**月に複数の
    母集団を置けることが前提の構造になっている。** それなのに書き込みが
    ファイルを丸ごと置き換えていたため、国内を回すと同じ月の米国が消えていた。

    自分の母集団の行だけを差し替える。同じ母集団を回し直せば自分の行が
    置き換わるだけなので、冪等性（原則6）も保たれる。

    **読めなかった既存ファイルを「無かった」ことにしない**（原則5）。
    黙って上書きすると、他の母集団の実績が理由も残さず消える。
    """
    if not existing_path.is_file():
        return new_rows

    mine = {r.get("population_id") for r in new_rows}
    try:
        frame = read_parquet(existing_path)
    except Exception as exc:  # pragma: no cover - 壊れた Parquet は再現が難しい
        frame = None
        manifest.add_warning(
            "GOLD_UNREADABLE",
            message=(
                f"既存の {what}（{existing_path}）を読めなかった: {exc}。"
                "**他の母集団の行が失われた可能性がある。** 必要なら該当母集団を回し直すこと"
            ),
        )
    if frame is None:
        return new_rows

    kept = [r for r in frame_records(frame) if r.get("population_id") not in mine]
    if kept:
        manifest.add_warning(
            "GOLD_OTHER_POPULATIONS_KEPT",
            count=len(kept),
            message=(
                "同じ月の gold にあった他の母集団の行を残した: "
                + ", ".join(sorted({str(r.get("population_id")) for r in kept}))
            ),
        )
    return new_rows + kept


def frame_records(frame) -> list[dict]:
    return frame.to_dict(orient="records")


def _rows_for_write(rows) -> list[dict]:
    """Pydantic モデルでも dict でも、population_id を読める形にそろえる。"""
    from pydantic import BaseModel

    return [r.model_dump() if isinstance(r, BaseModel) else dict(r) for r in rows]


def _rows(frame) -> list[dict]:
    if frame is None:
        return []
    return frame.to_dict(orient="records")


def _facts_for(run_id: str) -> list[dict] | None:
    frame = read_parquet(phase_output(run_id, "p5_parse", "facts.parquet"))
    return None if frame is None else _rows(frame)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return str(value)


def entity_index(entities: list[dict]) -> dict[str, dict]:
    """entity_id -> 集計に必要な属性だけ。

    `market_segment` は**意図的に持ち込まない**。内部の集計軸専用であり、
    gold に出してはいけない（DESIGN.md P1）。ここで落としておけば
    下流で誤って書き出すことがない。
    """
    out: dict[str, dict] = {}
    for row in entities:
        out[str(row.get("entity_id") or "")] = {
            "population_ids": list(row.get("population_ids") or []),
            "common12_code": _text(row.get("common12_code")),
            "common12_label": _text(row.get("common12_label")),
            "status": _text(row.get("status")) or EntityStatus.ACTIVE,
        }
    return out


def run(
    run_id: str,
    *,
    dry_run: bool = False,
    threshold: int | None = None,
) -> dict[str, Any]:
    """P7 を実行し manifest の内容を返す。"""
    out_dir = phase_dir(run_id, PHASE)
    month = month_date(run_id)
    cell_threshold = threshold if threshold is not None else suppress.MIN_CELL_SIZE

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        tool_versions={"aggregator": AGGREGATOR_VERSION},
        params={"dry_run": dry_run, "min_cell_size": cell_threshold},
    ) as manifest:
        facts_frame = read_parquet(phase_output(run_id, "p5_parse", "facts.parquet"))
        if facts_frame is None:
            raise MissingInputError(
                f"{run_id} の facts.parquet がありません。先に p5-parse を実行してください"
            )
        entities_frame = read_parquet(phase_output(run_id, "p1_population", "entities.parquet"))
        if entities_frame is None:
            raise MissingInputError(
                f"{run_id} の entities.parquet がありません。先に p1-population を実行してください"
            )

        facts = _rows(facts_frame)
        entities = entity_index(_rows(entities_frame))
        inferences = _rows(read_parquet(phase_output(run_id, "p6_infer", "inferences.parquet")))
        manifest.counts.input = len(facts)

        if not inferences:
            manifest.add_warning(
                "NO_INFERENCES",
                message=(
                    "inferences.parquet が無いためパークドメイン指標を集計していない。"
                    "先に p6-infer を実行すると parked_* が埋まる"
                ),
            )

        parks = park_classes(inferences)
        rows: list[DomainRow] = []
        orphan_entity_ids: set[str] = set()
        for fact in facts:
            row = DomainRow.from_fact(fact)
            row.park_class = parks.get(row.domain_id)
            if row.entity_id not in entities:
                orphan_entity_ids.add(row.entity_id)
                continue
            rows.append(row)

        if orphan_entity_ids:
            manifest.add_warning(
                "DOMAIN_WITHOUT_ENTITY",
                count=len(orphan_entity_ids),
                sample=sorted(orphan_entity_ids)[:5],
                message=(
                    "entities.parquet に存在しない entity_id のドメインを集計から外した。"
                    "母集団と計測対象がずれている"
                ),
            )

        # -- 母集団ごとに集計 ------------------------------------------------
        by_population: dict[str, list[DomainRow]] = defaultdict(list)
        population_entities: dict[str, set[str]] = defaultdict(set)
        for entity_id, attrs in entities.items():
            if attrs["status"] not in ACTIVE_STATUSES:
                continue
            for population_id in attrs["population_ids"]:
                population_entities[population_id].add(entity_id)
        for row in rows:
            attrs = entities[row.entity_id]
            if attrs["status"] not in ACTIVE_STATUSES:
                continue
            for population_id in attrs["population_ids"]:
                by_population[population_id].append(row)

        if not population_entities:
            manifest.add_warning(
                "NO_POPULATION",
                message="population_ids を持つ企業がいないため集計できない",
            )

        # -- 前月・前々月 ----------------------------------------------------
        prev_run = previous_run_id(run_id)
        prev_facts = _facts_for(prev_run)
        older_facts = _facts_for(previous_run_id(prev_run))
        prev_entities_frame = read_parquet(
            phase_output(prev_run, "p1_population", "entities.parquet")
        )
        prev_entity_ids = (
            {str(r.get("entity_id")) for r in _rows(prev_entities_frame)}
            if prev_entities_frame is not None
            else None
        )

        overall_rows = []
        sector_rows: list[StatsBySector] = []
        suppressed_total = 0
        deltas: dict[str, dict] = {}

        for population_id in sorted(population_entities):
            members = population_entities[population_id]
            pop_rows = by_population.get(population_id, [])

            stats = aggregate(
                pop_rows,
                measured_month=month,
                population_id=population_id,
                total_entities=len(members),
                spec_version=SpecVersion.RFC7489,
            )

            d = delta_mod.compute(
                [f for f in facts if str(f.get("entity_id")) in members],
                (
                    [f for f in prev_facts if str(f.get("entity_id")) in members]
                    if prev_facts is not None
                    else None
                ),
                before_previous=(
                    [f for f in older_facts if str(f.get("entity_id")) in members]
                    if older_facts is not None
                    else None
                ),
                current_entities=members,
                previous_entities=prev_entity_ids,
            )
            stats.delta_prev_month = d.to_json()
            deltas[population_id] = json.loads(d.to_json())
            overall_rows.append(stats)

            sector_rows.extend(
                _sectors(
                    population_id=population_id,
                    members=members,
                    rows=pop_rows,
                    entities=entities,
                    month=month,
                    threshold=cell_threshold,
                    manifest=manifest,
                )
            )

        suppressed_total = sum(1 for s in sector_rows if s.suppressed)
        manifest.counts.success = len(overall_rows) + len(sector_rows)
        manifest.set_breakdown(
            populations=sorted(population_entities),
            overall_rows=len(overall_rows),
            sector_rows=len(sector_rows),
            suppressed_sectors=suppressed_total,
            min_cell_size=cell_threshold,
            delta_prev_month=deltas,
            domains_aggregated=len(rows),
        )

        if prev_facts is None:
            manifest.add_warning(
                "NO_PREVIOUS_FACTS",
                message=f"前月 {prev_run} の facts が無いため差分を計算していない",
            )
        unobserved = sum(v.get("domains_unobserved_this_month", 0) for v in deltas.values())
        if unobserved:
            manifest.add_warning(
                "DOMAINS_UNOBSERVED",
                count=unobserved,
                message=(
                    "前月あったが今月観測できなかったドメイン。"
                    "**消滅ではない。** 差分では判定を保留している"
                ),
            )

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            target = gold_dir(run_id.split("-")[0] + "-" + run_id.split("-")[1])
            meta = {
                "mailauth.phase": PHASE,
                "mailauth.run_id": run_id,
                "mailauth.min_cell_size": str(cell_threshold),
            }
            n1 = write_parquet(
                _merge_other_populations(
                    _rows_for_write(overall_rows),
                    target / OVERALL_FILENAME,
                    manifest,
                    what="stats_overall",
                ),
                target / OVERALL_FILENAME,
                STATS_OVERALL_ARROW_SCHEMA,
                sort_keys=STATS_OVERALL_SORT_KEYS,
                metadata=meta,
            )
            n2 = write_parquet(
                _merge_other_populations(
                    _rows_for_write(sector_rows),
                    target / BY_SECTOR_FILENAME,
                    manifest,
                    what="stats_by_sector",
                ),
                target / BY_SECTOR_FILENAME,
                STATS_BY_SECTOR_ARROW_SCHEMA,
                sort_keys=STATS_BY_SECTOR_SORT_KEYS,
                metadata=meta,
            )
            # gold は out_dir の外（リポジトリ直下の gold/）に置くので絶対パスで記録する
            manifest.add_output(str(target / OVERALL_FILENAME), records=n1)
            manifest.add_output(str(target / BY_SECTOR_FILENAME), records=n2)

    return manifest.to_dict()


def _sectors(
    *,
    population_id: str,
    members: set[str],
    rows: list[DomainRow],
    entities: dict[str, dict],
    month,
    threshold: int,
    manifest: RunManifest,
) -> list[StatsBySector]:
    """業種別集計。n<threshold のセルは「その他」に束ねる。"""
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    unmapped = 0
    for entity_id in members:
        code = entities[entity_id]["common12_code"]
        if not code:
            unmapped += 1
            continue
        counts[code] += 1
        labels.setdefault(code, entities[entity_id]["common12_label"] or code)

    if unmapped:
        manifest.add_warning(
            "COMMON12_UNMAPPED",
            count=unmapped,
            message=(
                "共通12分類が付いていない企業は業種別集計から外した。"
                "業種不明を1つの業種として扱うと分母が歪む"
            ),
        )

    if not counts:
        return []

    plan = suppress.plan(dict(counts), threshold=threshold)
    for note in plan.notes:
        manifest.add_warning("CELL_SUPPRESSED", message=note)

    if suppress.is_recoverable(dict(counts), plan.suppressed, threshold=threshold):
        # 受け入れ基準に反する。黙って出すと個社が逆算されうる
        manifest.add_warning(
            "SUPPRESSION_RECOVERABLE",
            message=(
                "秘匿後の値が合計から逆算できる状態になっている。"
                "業種軸をさらに粗くするか、公開を見送ること"
            ),
        )

    by_code: dict[str, list[DomainRow]] = defaultdict(list)
    for row in rows:
        code = entities[row.entity_id]["common12_code"]
        if code:
            by_code[code].append(row)

    out: list[StatsBySector] = []
    for code in plan.published:
        out.append(
            _sector_row(
                code=code,
                label=labels[code],
                population_id=population_id,
                rows=by_code.get(code, []),
                n_entities=counts[code],
                month=month,
                suppressed=False,
            )
        )

    if plan.suppressed:
        merged_rows = [r for code in plan.suppressed for r in by_code.get(code, [])]
        out.append(
            _sector_row(
                code=SUPPRESSED_SECTOR_CODE,
                label=SUPPRESSED_SECTOR_LABEL,
                population_id=population_id,
                rows=merged_rows,
                n_entities=sum(counts[code] for code in plan.suppressed),
                month=month,
                suppressed=True,
            )
        )
    return out


def _sector_row(
    *,
    code: str,
    label: str,
    population_id: str,
    rows: list[DomainRow],
    n_entities: int,
    month,
    suppressed: bool,
) -> StatsBySector:
    stats = aggregate(
        rows,
        measured_month=month,
        population_id=population_id,
        total_entities=n_entities,
        spec_version=SpecVersion.RFC7489,
    )
    return StatsBySector(
        **stats.model_dump(),
        common12_code=code,
        common12_label=label,
        n_entities=n_entities,
        suppressed=suppressed,
    )
