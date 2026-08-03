"""P1 母集団確定の本体。

処理（DESIGN.md P1）
  1. source.primary に応じてリストを取得
  2. enrich を順に適用して identity を補完
  3. 業種を一次分類と共通12分類の両方で付与
  4. 前月の entities.parquet と突合し、新規・消滅・変更を検出
  5. entities.parquet を書き出し
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import PopulationConfig
from ..contracts import ENTITY_ARROW_SCHEMA, ENTITY_SORT_KEYS, Entity, EntityStatus
from ..io import read_parquet, records_to_frame, write_parquet
from ..manifest import RunManifest, config_hash
from ..normalize import (
    domain_from_url,
    normalize_houjin_bangou,
    normalize_name,
    normalize_securities_code,
)
from ..paths import config_path, month_date, phase_dir, phase_output, previous_run_id
from ..segments import load_segment_map
from .edinet import EdinetError, fetch_code_list, parse_code_list
from .enrich import GbizInfoClient, HoujinBangouClient
from .industry import IndustryMapper

PHASE = "p1_population"
OUTPUT_FILENAME = "entities.parquet"


class PopulationNotImplementedError(NotImplementedError):
    """未実装の母集団。理由を添えて止める（黙って空の結果を出さない）。"""


def _load_segment_allowlist(path: str | Path) -> set[str]:
    """証券コードの許可リスト。1列目を4桁に正規化して読む。

    JPX の data_j.xls は使わない。運用者が別ソースから作った CSV を想定する。
    """
    p = config_path(str(path))
    if not p.is_file():
        raise FileNotFoundError(f"segment_allowlist が見つかりません: {p}")
    codes: set[str] = set()
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            code = normalize_securities_code(row[0])
            if code and code.isdigit():
                codes.add(code)
    return codes


def _entity_id(houjin_bangou: str | None, edinet_code: str | None) -> str:
    """将来 LEI ベースに移行できるよう、接頭辞付きの識別子にする。

    Sprint 1.5 で global500 と突合する際、LEI が付いた企業は
    lei:XXXX に付け替え、is_duplicate_of で旧IDから辿れるようにする。
    """
    if houjin_bangou:
        return f"jp:{houjin_bangou}"
    if edinet_code:
        return f"edinet:{edinet_code}"
    raise ValueError("entity_id を決められません（法人番号も EDINETコードも無い）")


def _build_entities(
    rows: list[Any],
    cfg: PopulationConfig,
    run_id: str,
    mapper: IndustryMapper,
    manifest: RunManifest,
) -> tuple[list[Entity], dict[str, int]]:
    """EDINET の行から Entity を組む。ここではまだ enrich していない。"""
    entities: list[Entity] = []
    seen: dict[str, Entity] = {}
    stats = {"no_identity": 0, "duplicate_entity_id": 0, "industry_missing": 0}

    for row in rows:
        houjin_bangou = normalize_houjin_bangou(row.houjin_bangou)
        edinet_code = row.edinet_code or None
        try:
            eid = _entity_id(houjin_bangou, edinet_code)
        except ValueError:
            stats["no_identity"] += 1
            manifest.add_failure("identity_missing")
            continue

        common12 = mapper.map(row.industry)
        if common12 is None:
            stats["industry_missing"] += 1

        entity = Entity(
            entity_id=eid,
            run_id=run_id,
            country=cfg.country,
            population_ids=[cfg.id],
            name=row.name,
            name_en=row.name_en or None,
            name_normalized=normalize_name(row.name),
            houjin_bangou=houjin_bangou,
            edinet_code=edinet_code,
            securities_code=normalize_securities_code(row.securities_code),
            industry_scheme=cfg.source.industry.primary_scheme,
            industry_code=None,  # EDINET の提出者業種はラベルのみでコードを持たない
            industry_label=row.industry or None,
            common12_code=common12.code if common12 else None,
            common12_label=common12.label if common12 else None,
            industry_map_version=mapper.map_version,
            status=EntityStatus.ACTIVE,
        )

        if eid in seen:
            # 同じ法人番号で複数の EDINET コードを持つ提出者がありうる。
            # 先勝ちで残し、証券コードだけは埋まっている方を採用する。
            stats["duplicate_entity_id"] += 1
            existing = seen[eid]
            if not existing.securities_code and entity.securities_code:
                existing.securities_code = entity.securities_code
            continue

        seen[eid] = entity
        entities.append(entity)

    return entities, stats


def _annotate_segments(
    entities: list[Entity], cfg: PopulationConfig, manifest: RunManifest
) -> None:
    """市場区分のラベルを付ける。計測対象は絞らない。

    絞らないのは、全上場を測っておけばビューで
    「全体を業種軸で」「プライムだけで」を切り替えられるからである。
    母集団を変えて計測し直すと月次の比較ができなくなる。
    """
    mf = cfg.source.market_filter
    if not mf.segment_map:
        manifest.set_breakdown(
            market_segment={
                "available": False,
                "reason": (
                    "market_filter.segment_map が未設定。"
                    "EDINETコードリストは市場区分を持たないため区分は付かない"
                ),
            }
        )
        return

    seg_map = load_segment_map(mf.segment_map)
    matched = 0
    for e in entities:
        segment = seg_map.get(e.securities_code)
        if segment:
            e.market_segment = segment
            e.market_segment_source = seg_map.source
            matched += 1

    unmatched = len(entities) - matched
    manifest.set_breakdown(
        market_segment={
            "available": True,
            "map_path": str(seg_map.path),
            "map_size": len(seg_map),
            "map_source": seg_map.source,
            "map_retrieved": seg_map.retrieved,
            "matched": matched,
            "unmatched": unmatched,
            "by_segment": seg_map.counts(),
            "invalid_rows": seg_map.invalid_rows,
        }
    )
    if seg_map.invalid_rows:
        manifest.add_warning(
            "SEGMENT_MAP_INVALID_ROWS",
            count=seg_map.invalid_rows,
            message="区分の対応表に証券コードか区分が読めない行がある",
        )
    if unmatched:
        manifest.add_warning(
            "SEGMENT_UNMATCHED",
            count=unmatched,
            message=(
                f"{unmatched}社に市場区分が付かなかった。"
                "対応表の網羅性を確認すること（区分ビューの分母から漏れる）"
            ),
        )


def _apply_enrichment(
    entities: list[Entity],
    cfg: PopulationConfig,
    manifest: RunManifest,
    *,
    limit: int | None = None,
) -> None:
    """enrich を順に適用する。認証情報が無いものはスキップして記録する。"""
    bangou_list = [e.houjin_bangou for e in entities if e.houjin_bangou]
    by_bangou = {e.houjin_bangou: e for e in entities if e.houjin_bangou}

    for name in cfg.source.enrich:
        if name == "gbizinfo":
            client = GbizInfoClient()
            result = client.fetch(bangou_list, limit=limit)
            if result.skipped_reason:
                manifest.add_warning("ENRICH_SKIPPED_GBIZINFO", message=result.skipped_reason)
            for bangou, values in result.values.items():
                entity = by_bangou.get(bangou)
                if entity and values.get("official_url"):
                    entity.official_url = values["official_url"]
                    entity.official_domain = domain_from_url(values["official_url"])
            manifest.set_breakdown(
                gbizinfo={
                    "attempted": result.attempted,
                    "hit": result.hit,
                    "miss": result.miss,
                    "error": result.error,
                    "cache_hit": result.cache_hit,
                }
            )

        elif name == "houjin_bangou":
            client = HoujinBangouClient()
            result = client.fetch(bangou_list, limit=limit)
            if result.skipped_reason:
                manifest.add_warning("ENRICH_SKIPPED_HOUJIN_BANGOU", message=result.skipped_reason)
            mismatched: list[str] = []
            for bangou, values in result.values.items():
                entity = by_bangou.get(bangou)
                if not entity:
                    continue
                nta_name = values.get("nta_name")
                # 商号の裏取り。EDINET と国税庁で表記が食い違う場合は
                # EDINET の値を残しつつ、食い違いの件数だけ記録する。
                if nta_name and normalize_name(nta_name) != entity.name_normalized:
                    mismatched.append(entity.entity_id)
            if mismatched:
                manifest.add_warning(
                    "NAME_MISMATCH_NTA",
                    count=len(mismatched),
                    sample=mismatched[:5],
                    message="EDINET と国税庁で商号の表記が一致しない（EDINET 側を採用）",
                )
            manifest.set_breakdown(
                houjin_bangou={
                    "attempted": result.attempted,
                    "hit": result.hit,
                    "miss": result.miss,
                    "error": result.error,
                    "cache_hit": result.cache_hit,
                }
            )

        else:
            manifest.add_warning(
                "ENRICH_UNKNOWN",
                sample=[name],
                message=f"未知の enrich 指定のためスキップした: {name}",
            )


def _from_parquet(value: Any) -> Any:
    """Parquet 由来の値を Pydantic に渡せる素の Python の値にする。

    pandas は欠損を float('nan') で返し、list 列を numpy 配列で返す。
    どちらもそのままでは str / list[str] の検証に落ちる。
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "tolist") and not isinstance(value, str):
        return value.tolist()
    if value is pd.NaT:
        return None
    return value


def _diff_with_previous(
    entities: list[Entity], run_id: str, manifest: RunManifest
) -> list[Entity]:
    """前月と突合して first_seen / last_seen / 消滅を埋める。

    消滅した企業は status=delisted の行として当月に1度だけ残す。
    時系列で「消えた」と「取れなかった」を混同しないため、
    差分は必ずファイルの実在をもって判定する（前月が無ければ差分は出さない）。
    """
    this_month = month_date(run_id)
    prev_id = previous_run_id(run_id)
    prev_df = read_parquet(phase_output(prev_id, PHASE, OUTPUT_FILENAME))

    if prev_df is None:
        for e in entities:
            e.first_seen_month = this_month
            e.last_seen_month = this_month
        manifest.set_breakdown(
            diff={
                "previous_run_id": prev_id,
                "previous_available": False,
                "new_entities": None,
                "delisted_entities": None,
                "renamed_entities": None,
            }
        )
        manifest.add_warning(
            "NO_PREVIOUS_RUN",
            message=f"前月 {prev_id} の entities.parquet が無いため差分を計算していない",
        )
        return entities

    prev = {
        str(r["entity_id"]): r
        for _, r in prev_df.iterrows()
        if str(r.get("status", "")) == EntityStatus.ACTIVE
    }
    current_ids = {e.entity_id for e in entities}

    new_count = 0
    renamed: list[str] = []
    for e in entities:
        before = prev.get(e.entity_id)
        if before is None:
            new_count += 1
            e.first_seen_month = this_month
        else:
            first = before.get("first_seen_month")
            e.first_seen_month = (
                first if isinstance(first, type(this_month)) and pd.notna(first) else this_month
            )
            if str(before.get("name") or "") != e.name:
                e.status = EntityStatus.RENAMED
                e.change_note = f"社名変更: {before.get('name')} -> {e.name}"
                renamed.append(e.entity_id)
        e.last_seen_month = this_month

    delisted: list[Entity] = []
    for eid, before in prev.items():
        if eid in current_ids:
            continue
        row = {k: _from_parquet(before.get(k)) for k in ENTITY_ARROW_SCHEMA.names}
        row["run_id"] = run_id
        row["status"] = EntityStatus.DELISTED
        row["change_note"] = "前月に存在したが当月の母集団に現れない"
        row["population_ids"] = row.get("population_ids") or []
        try:
            delisted.append(Entity.model_validate(row))
        except Exception as exc:  # noqa: BLE001 - 前月データの欠損で全体を落とさない
            manifest.add_warning(
                "DELISTED_ROW_INVALID",
                sample=[eid],
                message=f"前月レコードから delisted 行を作れなかった: {exc}",
            )

    manifest.counts.skipped += len(delisted)
    manifest.set_breakdown(
        diff={
            "previous_run_id": prev_id,
            "previous_available": True,
            "new_entities": new_count,
            "delisted_entities": len(delisted),
            "renamed_entities": len(renamed),
            "renamed_sample": renamed[:5],
        }
    )
    return entities + delisted


def _check_acceptance(entities: list[Entity], cfg: PopulationConfig, manifest: RunManifest) -> None:
    """DESIGN.md P1 の受け入れ基準を判定する。満たさなくても実行は止めない。

    止めないのは、基準未達それ自体が観測結果だからである。
    manifest に残してコンソールで見えるようにする方が有用。
    """
    active = [e for e in entities if e.status != EntityStatus.DELISTED]
    n = len(active)
    acc = cfg.acceptance
    checks: dict[str, Any] = {}

    if acc.expected_count_min is not None and acc.expected_count_max is not None:
        ok = acc.expected_count_min <= n <= acc.expected_count_max
        checks["count_in_range"] = ok
        if not ok:
            manifest.add_warning(
                "ACCEPTANCE_COUNT_OUT_OF_RANGE",
                message=(
                    f"取得件数 {n} が想定 {acc.expected_count_min}〜{acc.expected_count_max} の外"
                ),
            )

    def missing_rate(predicate) -> float:
        return sum(1 for e in active if predicate(e)) / n if n else 0.0

    rates = {
        "houjin_bangou": missing_rate(lambda e: not e.houjin_bangou),
        "official_url": missing_rate(lambda e: not e.official_url),
        "common12": missing_rate(lambda e: not e.common12_code),
    }
    checks["missing_rate"] = {k: round(v, 4) for k, v in rates.items()}

    for key, threshold in (acc.max_missing_rate or {}).items():
        actual = rates.get(key)
        if actual is None:
            continue
        if actual > threshold:
            manifest.add_warning(
                "ACCEPTANCE_MISSING_RATE",
                sample=[key],
                message=f"{key} の欠損率 {actual:.1%} が閾値 {threshold:.1%} を超えている",
            )

    manifest.set_breakdown(acceptance=checks)


def run(
    config: str | Path,
    run_id: str,
    *,
    limit: int | None = None,
    source_file: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """P1 を実行し、manifest の内容を返す。"""
    from ..config import load_population  # 循環 import を避けるため遅延

    cfg = load_population(config)
    out_dir = phase_dir(run_id, PHASE)

    mapping_path = cfg.source.industry.common_mapping
    hash_inputs = [p for p in (cfg.source_path, config_path(mapping_path) if mapping_path else None)
                   if p is not None]

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        config_hash=config_hash(*hash_inputs),
        params={
            "population_id": cfg.id,
            "config": str(cfg.source_path),
            "limit": limit,
            "dry_run": dry_run,
            "source_file": str(source_file) if source_file else None,
        },
    ) as manifest:
        manifest.attribution = list(cfg.attribution)

        if not cfg.enabled:
            raise PopulationNotImplementedError(
                f"母集団 {cfg.id} は enabled: false です。設定で有効化してください"
            )
        if not cfg.implemented:
            raise PopulationNotImplementedError(
                f"母集団 {cfg.id} は未実装です。理由: {cfg.blocked_by or '未記載'}"
            )
        if cfg.source.primary != "edinet_code_list":
            raise PopulationNotImplementedError(
                f"source.primary={cfg.source.primary} は Sprint 1 では未実装です"
                "（Sprint 1.5 で wikidata / SEC EDGAR / GLEIF を実装）"
            )
        if cfg.source.edinet_code_list is None:
            raise EdinetError("source.edinet_code_list の設定がありません")

        # 1. 取得
        fetched = fetch_code_list(
            cfg.source.edinet_code_list,
            cache_ttl_hours=cfg.refresh.cache_ttl_hours,
            source_file=source_file,
        )
        rows, parse_stats = parse_code_list(fetched.path, cfg.source.edinet_code_list)
        manifest.counts.input = len(rows)
        manifest.set_breakdown(
            source={
                "path": str(fetched.path),
                "from_cache": fetched.from_cache,
                "fetched_at": fetched.fetched_at.isoformat(),
                "bytes": fetched.bytes,
                **parse_stats,
            }
        )
        if parse_stats["skipped_malformed"]:
            manifest.add_warning(
                "MALFORMED_CSV_ROWS",
                count=parse_stats["skipped_malformed"],
                message="列数が13未満の行を読み飛ばした",
            )

        # 2. 上場企業の抽出
        mf = cfg.source.market_filter
        if mf.method != "securities_code_presence":
            raise PopulationNotImplementedError(
                f"market_filter.method={mf.method} は未実装です"
            )
        listed = [r for r in rows if normalize_securities_code(r.securities_code)]
        manifest.counts.skipped += len(rows) - len(listed)

        if mf.segment_allowlist:
            allow = _load_segment_allowlist(mf.segment_allowlist)
            before = len(listed)
            listed = [
                r for r in listed if normalize_securities_code(r.securities_code) in allow
            ]
            manifest.counts.skipped += before - len(listed)
            manifest.set_breakdown(
                market_filter={"segment_allowlist_size": len(allow), "matched": len(listed)}
            )
        elif mf.expects_segment and mf.segment_source == "none":
            manifest.add_warning(
                "MARKET_SEGMENT_UNAVAILABLE",
                message=(
                    f"{cfg.id} は市場区分での絞り込みを前提としているが、"
                    "segment_source が none で allowlist も無いため全上場企業が対象になっている。"
                    "JPX 非依存を維持したまま区分を得る手段が未確保（DESIGN.md 未解決事項）"
                ),
            )

        if limit:
            listed = listed[:limit]

        # 3. Entity の組み立てと業種付与
        mapper = IndustryMapper.load(mapping_path) if mapping_path else None
        if mapper is None:
            raise ValueError("source.industry.common_mapping が未設定です")

        entities, build_stats = _build_entities(listed, cfg, run_id, mapper, manifest)

        # 4. 市場区分のラベル付け（絞り込みではない）
        _annotate_segments(entities, cfg, manifest)

        # 5. enrich
        if not dry_run:
            _apply_enrichment(entities, cfg, manifest, limit=limit)

        # 6. 前月との差分
        if limit:
            # --limit は開発中の高速反復用。母集団を切り詰めた状態で差分を取ると
            # 対象外の企業が軒並み delisted になり、時系列を汚す。
            manifest.add_warning(
                "DIFF_SKIPPED_DUE_TO_LIMIT",
                message=f"--limit {limit} が指定されているため前月差分を計算していない",
            )
            this_month = month_date(run_id)
            for e in entities:
                e.first_seen_month = e.first_seen_month or this_month
                e.last_seen_month = this_month
        else:
            entities = _diff_with_previous(entities, run_id, manifest)

        active = [e for e in entities if e.status != EntityStatus.DELISTED]
        manifest.counts.success = len(active)
        manifest.set_breakdown(
            build=build_stats,
            official_url_missing=sum(1 for e in active if not e.official_url),
            industry_missing=sum(1 for e in active if not e.common12_code),
            unmapped_industry_labels=mapper.top_unmapped(),
        )
        if mapper.unmapped:
            manifest.add_warning(
                "INDUSTRY_UNMAPPED",
                count=sum(mapper.unmapped.values()),
                sample=[label for label, _ in mapper.top_unmapped(5)],
                message="共通12分類に写せなかった一次分類ラベルがある。写像CSVに追記すること",
            )

        _check_acceptance(entities, cfg, manifest)

        # 7. 書き出し
        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            df = records_to_frame(entities, ENTITY_ARROW_SCHEMA)
            n = write_parquet(
                df,
                out_dir / OUTPUT_FILENAME,
                ENTITY_ARROW_SCHEMA,
                sort_keys=ENTITY_SORT_KEYS,
                metadata={
                    "mailauth.phase": PHASE,
                    "mailauth.run_id": run_id,
                    "mailauth.population_id": cfg.id,
                    "mailauth.industry_map_version": mapper.map_version,
                    "mailauth.attribution": " / ".join(cfg.attribution),
                },
            )
            manifest.add_output(OUTPUT_FILENAME, records=n)

        return manifest.to_dict()
