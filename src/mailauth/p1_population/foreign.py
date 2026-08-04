"""米国・グローバル母集団の組み立て（DESIGN.md P1 / Sprint 1.5）。

国内側（EDINET 経路）と分けてあるのは、identity の主キーが違うためである。
国内は法人番号、こちらは LEI。同じ関数に押し込むと、どちらの前提で
書かれたコードなのかが読めなくなる。

処理の順序
  1. Wikidata（CC0）から会社名リストを再構築する
  2. ティッカーから CIK を引く（米国企業のみ）
  3. SEC EDGAR の submissions から公式サイトと SIC を取る（米国企業のみ）
  4. LEI を裏取りする。無ければ GLEIF に商号で問い合わせる
  5. SIC を共通12分類に写す
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import PopulationConfig
from ..contracts import Entity, EntityStatus
from ..manifest import RunManifest
from ..normalize import domain_from_url, normalize_name
from .gleif import GleifClient
from .identity import entity_id_for
from .industry import SicMapper
from .sec_edgar import SecEdgarClient, SecEdgarError
from .wikidata import ForeignRow


@dataclass
class ForeignStats:
    rows: int = 0
    no_identity: int = 0
    lei_from_wikidata: int = 0
    lei_from_gleif: int = 0
    lei_missing: int = 0
    cik_from_wikidata: int = 0
    cik_from_ticker: int = 0
    website_from_wikidata: int = 0
    website_from_sec: int = 0
    sic_found: int = 0
    industry_missing: int = 0
    by_id_key: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def build_entities(
    rows: list[ForeignRow],
    cfg: PopulationConfig,
    run_id: str,
    *,
    manifest: RunManifest,
    sec: SecEdgarClient | None = None,
    gleif: GleifClient | None = None,
    sic_mapper: SicMapper | None = None,
) -> tuple[list[Entity], ForeignStats]:
    """Wikidata の行から Entity を組む。

    `sec` / `gleif` は None を渡せる。ネットワークに出ない実行のためで、
    **その場合は補完していないことを manifest に記録する**（原則5）。
    """
    stats = ForeignStats(rows=len(rows))
    entities: list[Entity] = []

    for row in rows:
        # -- CIK --------------------------------------------------------
        cik = row.cik
        if cik:
            stats.cik_from_wikidata += 1
        elif sec is not None and row.ticker:
            cik = sec.cik_for_ticker(row.ticker)
            if cik:
                stats.cik_from_ticker += 1

        # -- SEC の submissions（米国提出者のみ） -----------------------
        website = row.website
        if website:
            stats.website_from_wikidata += 1
        sic, sic_label = row.sic, row.sic_label
        if sec is not None and cik:
            facts = sec.facts_for_cik(cik)
            if not website and facts.website:
                website = facts.website
                stats.website_from_sec += 1
            if not sic and facts.sic:
                sic, sic_label = facts.sic, facts.sic_label
        if sic:
            stats.sic_found += 1

        # -- LEI --------------------------------------------------------
        lei = row.lei
        if lei:
            stats.lei_from_wikidata += 1
        elif gleif is not None:
            record = gleif.by_name(row.name_en or row.name)
            if record:
                lei = record.lei
                stats.lei_from_gleif += 1
        if not lei:
            stats.lei_missing += 1

        # -- entity_id ---------------------------------------------------
        try:
            eid, key = entity_id_for(lei=lei, cik=cik, qid=row.qid)
        except ValueError:
            stats.no_identity += 1
            manifest.add_failure("identity_missing")
            continue
        stats.by_id_key[key] = stats.by_id_key.get(key, 0) + 1

        # -- 業種 --------------------------------------------------------
        common = sic_mapper.map(sic) if sic_mapper else None
        if common is None:
            stats.industry_missing += 1

        entities.append(
            Entity(
                entity_id=eid,
                run_id=run_id,
                # global500 は country: MULTI。個社の国は Wikidata 側の値を優先する
                country=row.country or cfg.country,
                population_ids=[cfg.id],
                name=row.name,
                name_en=row.name_en or None,
                name_normalized=normalize_name(row.name_en or row.name),
                lei=lei,
                cik=cik,
                ticker=row.ticker,
                official_url=website,
                official_domain=domain_from_url(website) if website else None,
                industry_scheme="SIC" if sic else None,
                industry_code=sic,
                industry_label=sic_label,
                common12_code=common.code if common else None,
                common12_label=common.label if common else None,
                industry_map_version=sic_mapper.map_version if sic_mapper else None,
                status=EntityStatus.ACTIVE,
                change_note="; ".join(row.notes) or None,
            )
        )

    return entities, stats


def report(stats: ForeignStats, manifest: RunManifest, *, cfg: PopulationConfig) -> None:
    """補完の欠落を warning として出す。**黙って欠損させない。**"""
    manifest.set_breakdown(
        foreign={
            "rows": stats.rows,
            "no_identity": stats.no_identity,
            "lei_from_wikidata": stats.lei_from_wikidata,
            "lei_from_gleif": stats.lei_from_gleif,
            "lei_missing": stats.lei_missing,
            "cik_from_wikidata": stats.cik_from_wikidata,
            "cik_from_ticker": stats.cik_from_ticker,
            "website_from_wikidata": stats.website_from_wikidata,
            "website_from_sec": stats.website_from_sec,
            "sic_found": stats.sic_found,
            "industry_missing": stats.industry_missing,
            "by_id_key": dict(sorted(stats.by_id_key.items())),
        }
    )

    if stats.lei_missing:
        rate = stats.lei_missing / stats.rows if stats.rows else 0
        manifest.add_warning(
            "LEI_MISSING",
            count=stats.lei_missing,
            message=(
                f"LEI が付かなかった企業が {stats.lei_missing} 件（{rate:.1%}）。"
                "identity の主キーが LEI なので、この分は cik / qid での同定に"
                "落ちている。母集団をまたぐ重複排除の精度が下がる"
            ),
        )
    if stats.industry_missing:
        manifest.add_warning(
            "INDUSTRY_UNMAPPED",
            count=stats.industry_missing,
            message=(
                "SIC から共通12分類に写せなかった企業がある。"
                "SEC EDGAR は米国提出者しか SIC を持たないため、"
                "米国外の企業は写像そのものが無い"
            ),
        )
    if cfg.id != "us-fortune500" and stats.sic_found < stats.rows:
        manifest.add_warning(
            "SIC_ONLY_FOR_US_FILERS",
            count=stats.rows - stats.sic_found,
            message=(
                "SEC EDGAR は米国提出者しかカバーしない。米国外の企業には"
                "一次分類が付かない。共通12分類も手動マッピングが要る"
            ),
        )


def make_clients(
    cfg: PopulationConfig, *, offline: bool
) -> tuple[SecEdgarClient | None, GleifClient | None, list[str]]:
    """設定の enrich に応じてクライアントを作る。

    **作れないものは None にして理由を返す。** 黙って補完をスキップすると、
    「LEI が無い会社だった」のか「引かなかった」のかが区別できない（原則5）。
    """
    notes: list[str] = []
    if offline:
        return None, None, ["offline のため SEC EDGAR と GLEIF に問い合わせていない"]

    enrich = [str(e) for e in (cfg.source.enrich or [])]
    sec: SecEdgarClient | None = None
    gleif: GleifClient | None = None

    if any(e.startswith("sec_edgar") for e in enrich):
        try:
            sec = SecEdgarClient()
            sec.load_tickers()
        except SecEdgarError as exc:
            sec = None
            notes.append(f"SEC EDGAR を使えない: {exc}")

    if any(e in ("gleif", "gleif_lei") for e in enrich):
        gleif = GleifClient()

    return sec, gleif, notes


def enrichment_summary(
    sec: SecEdgarClient | None, gleif: GleifClient | None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if sec is not None:
        out["sec_edgar"] = {
            "ticker_entries": sec.stats.ticker_entries,
            "submissions_fetched": sec.stats.submissions_fetched,
            "submissions_failed": sec.stats.submissions_failed,
            "website_found": sec.stats.website_found,
            "sic_found": sec.stats.sic_found,
            "notes": sec.stats.notes[:5],
        }
    if gleif is not None:
        out["gleif"] = {
            "looked_up": gleif.stats.looked_up,
            "found": gleif.stats.found,
            "failed": gleif.stats.failed,
            "ambiguous": gleif.stats.ambiguous,
            "notes": gleif.stats.notes[:5],
        }
    return out


def make_lei_free_entity(
    row: Any,
    cfg: PopulationConfig,
    run_id: str,
    *,
    sic_mapper: SicMapper | None = None,
) -> Entity:
    """SEC の上場リストの行から Entity を組む。

    LEI はここでは付けない。全社に GLEIF を引くと6,000リクエストになり、
    月次のたびに GLEIF へ不当な負荷をかける。**必要になってから引く。**
    entity_id は CIK ベースにしておき、LEI が付いたら
    `identity.promote_to_lei` で付け替える（旧 ID は is_duplicate_of に残る）。
    """
    common = sic_mapper.map(row.sic) if sic_mapper else None
    eid, _ = entity_id_for(cik=row.cik)
    return Entity(
        entity_id=eid,
        run_id=run_id,
        country=cfg.country,
        population_ids=[cfg.id],
        name=row.name,
        name_en=row.name,
        name_normalized=normalize_name(row.name),
        cik=row.cik,
        ticker=row.ticker,
        official_url=row.website,
        official_domain=domain_from_url(row.website) if row.website else None,
        industry_scheme="SIC" if row.sic else None,
        industry_code=row.sic,
        industry_label=row.sic_label,
        common12_code=common.code if common else None,
        common12_label=common.label if common else None,
        industry_map_version=sic_mapper.map_version if sic_mapper else None,
        status=EntityStatus.ACTIVE,
    )
