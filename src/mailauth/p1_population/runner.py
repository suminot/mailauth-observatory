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
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import PopulationConfig, credential
from ..contracts import (
    ENTITY_ARROW_SCHEMA,
    ENTITY_SORT_KEYS,
    EXCLUDED_ENTITY_ARROW_SCHEMA,
    EXCLUDED_ENTITY_SORT_KEYS,
    Entity,
    EntityStatus,
)
from ..io import read_parquet, records_to_frame, write_parquet
from ..manifest import RunManifest, config_hash
from ..normalize import (
    domain_from_url,
    normalize_houjin_bangou,
    normalize_name,
    normalize_securities_code,
)
from ..ordered import in_input_order
from ..paths import cache_root, config_path, month_date, phase_dir, phase_output, previous_run_id
from ..segments import load_segment_map
from .edinet import EdinetError, fetch_code_list, parse_code_list
from .enrich import GbizInfoClient, HoujinBangouClient
from .industry import IndustryMapper

PHASE = "p1_population"
OUTPUT_FILENAME = "entities.parquet"
#: 母集団から外した事業会社でないもの（名前と理由）。P7 が gold に写す
EXCLUDED_FILENAME = "excluded_entities.parquet"
#: 公式サイトの推測で、同時に確かめる企業数。投げる間隔は別に全体 1 秒 2 本
CO_JP_CONCURRENCY = 8
#: 公式サイトの推測で、1ページを読むのにかける時間の上限（秒）
CO_JP_PAGE_DEADLINE_SEC = 20

#: `.co.jp` の推測の結果を持ち越す日数。**流し直しのたびに 600 社のサイトを
#: 2,000 ページ読み直していた**（2026-11 の国内で 1 回 21 分）。相手のサイトは
#: 月の中ではまず変わらない。**読めなかった（unreachable）結果は持ち越さない**
CO_JP_CACHE_DAYS = 25


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
    offline: bool = False,
    kana: dict[str, str] | None = None,
) -> None:
    """enrich を順に適用する。認証情報が無いものはスキップして記録する。

    **`offline` を無視してはいけない。** gBizINFO と法人番号は認証情報が
    無ければ勝手に止まるが、**Wikidata は鍵が要らないので必ず外に出る。**
    偶然守られていた経路と、明示的に守る経路を混ぜない。
    """
    bangou_list = [e.houjin_bangou for e in entities if e.houjin_bangou]
    by_bangou = {e.houjin_bangou: e for e in entities if e.houjin_bangou}

    enrich_seconds: dict[str, float] = {}
    for name in cfg.source.enrich:
        started = time.monotonic()
        try:
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
                if result.error:
                    # **breakdown に入れるだけでは気付けない。**
                    # 500社ぶん失敗しても status=success で通り、被覆率だけが
                    # 静かに落ちる。「サイトが無い」のではなく「引けなかった」
                    manifest.add_warning(
                        "ENRICH_FAILED_GBIZINFO",
                        count=result.error,
                        message=(
                            f"{result.error} 社で gBizINFO を引けなかった。"
                            "その分 official_url が空になっている"
                        ),
                    )
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
                    manifest.add_warning(
                        "ENRICH_SKIPPED_HOUJIN_BANGOU", message=result.skipped_reason
                    )
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
                if result.error:
                    manifest.add_warning(
                        "ENRICH_FAILED_HOUJIN_BANGOU",
                        count=result.error,
                        message=(
                            f"{result.error} 社で国税庁を引けなかった。"
                            "その分だけ商号の裏取りができていない"
                        ),
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

            elif name == "wikidata_identity":
                if offline:
                    manifest.add_warning(
                        "ENRICH_SKIPPED_WIKIDATA",
                        message=(
                            "offline のため公式サイトを補っていない。"
                            "**「無い」のではなく「引いていない」**"
                        ),
                    )
                    continue
                _fill_missing_urls_from_wikidata(entities, cfg, manifest)

            elif name == "edinet_koukoku":
                _fill_from_edinet_koukoku(entities, manifest, limit=limit, offline=offline)

            elif name == "co_jp_homepage":
                _fill_from_co_jp_homepage(
                    entities, manifest, limit=limit, offline=offline, kana=kana
                )

            elif name == "jawiki_infobox":
                _fill_from_jawiki(entities, manifest, limit=limit, offline=offline)

            else:
                manifest.add_warning(
                    "ENRICH_UNKNOWN",
                    sample=[name],
                    message=f"未知の enrich 指定のためスキップした: {name}",
                )
        finally:
            # **どの経路が遅いかを残す。** 国内 2026-11 で P1 が 32 分かかったが、
            # 工程全体の秒数しか無く、どれを直せばよいか分からなかった
            enrich_seconds[name] = round(time.monotonic() - started, 1)
    manifest.set_breakdown(enrich_seconds=enrich_seconds)


def _fill_from_jawiki(
    entities: list[Entity],
    manifest: RunManifest,
    *,
    limit: int | None = None,
    offline: bool = False,
    fetch=None,
) -> None:
    """**まだ空欄の企業だけ**、日本語版ウィキペディアの基礎情報から（BACKLOG 28）。

    同じ会社の記事であることは、基礎情報の市場情報に書かれた証券コードで
    確かめる（jawiki.py）。`fetch` を渡せるのは検査のため。
    """
    from .jawiki import JawikiWebsites, live_fetch

    missing = [e for e in entities if not e.official_domain and e.securities_code]
    if limit:
        missing = missing[:limit]
    if not missing:
        manifest.set_breakdown(jawiki_infobox={"attempted": 0})
        return
    if fetch is None:
        if offline:
            manifest.add_warning(
                "ENRICH_SKIPPED_JAWIKI",
                message=(
                    "offline のためウィキペディアの基礎情報を読んでいない。"
                    "**「無い」のではなく「引いていない」**"
                ),
            )
            return
        if not credential("MAILAUTH_CONTACT_EMAIL"):
            manifest.add_warning(
                "ENRICH_SKIPPED_JAWIKI",
                message=(
                    "MAILAUTH_CONTACT_EMAIL が無いためウィキペディアを引いていない"
                    "（Wikimedia の作法で、連絡先つきの User-Agent を付ける）"
                ),
            )
            return
        fetch = live_fetch()

    reader = JawikiWebsites(fetch)
    taken = {e.official_domain for e in entities if e.official_domain}
    urls = reader.websites_for(
        ((e.entity_id, e.name, e.securities_code) for e in missing), taken
    )
    by_id = {e.entity_id: e for e in missing}
    for entity_id, url in urls.items():
        entity = by_id[entity_id]
        entity.official_url = url
        entity.official_domain = domain_from_url(url)
    if reader.stats.fetch_failed:
        manifest.add_warning(
            "ENRICH_FAILED_JAWIKI",
            count=reader.stats.fetch_failed,
            message=(
                "ウィキペディアを引けなかった企業がある。**「記事が無い」ではなく"
                "「引けなかった」**（原則5）"
            ),
        )
    manifest.set_breakdown(
        jawiki_infobox={
            **reader.stats.to_dict(),
            "missing_before": len(missing),
            "still_missing": sum(1 for e in entities if not e.official_domain),
        }
    )


def _prefer_filing_domains(
    entities: list[Entity], manifest: RunManifest, *, scan_path: str | None = None
) -> None:
    """有報の公告掲載方法に会社が書いた自社ドメインを、公式サイトとして優先する（BACKLOG 47）。

    gBizINFO・Wikidata などから来た公式サイトに、**第三者のページ**が入って
    いた（デンカ → denka.disclosure.site、三菱ケミカルグループ →
    pitchbook.com/profiles/...）。そのドメインを「その会社のもの」として測って
    いた。推測（`.co.jp`）が同名の別会社を当てていたものもある（オーテック →
    otec.co.jp、有報では o-tec.co.jp）。

    会社が政府の開示書類に自分で書いたドメインが**1つだけ**あり、それが
    いまの公式サイトと違えば、それに置き換える。**一覧の中で2社以上に出る
    ドメインは使わない**（親子で同じ公告ページを使う会社がある）。委託先は
    koukoku-scan が既に落としている。
    """
    from collections import Counter

    from .edinet_koukoku import SCAN_CSV, read_scan

    scan = read_scan(config_path(scan_path or SCAN_CSV))
    if not scan:
        manifest.set_breakdown(filing_domain={"scan_rows": 0})
        return
    single = {code: doms[0] for code, (_doc, doms, _on) in scan.items() if len(doms) == 1}
    uses = Counter(single.values())
    replaced: list[str] = []
    filled = agreed = shared = 0
    for e in entities:
        dom = single.get(e.edinet_code or "")
        if not dom:
            continue
        if uses[dom] > 1:
            shared += 1
            continue
        if e.official_domain == dom:
            agreed += 1
            continue
        if e.official_domain:
            replaced.append(f"{e.official_domain} → {dom}（{e.name}）")
        else:
            filled += 1
        e.official_domain = dom
        e.official_url = f"https://{dom}/"
    manifest.set_breakdown(
        filing_domain={
            "scan_rows": len(scan),
            "single_domain": len(single),
            "agreed": agreed,
            "replaced": len(replaced),
            "filled": filled,
            "shared_skipped": shared,
            "sample_replaced": replaced[:20],
        }
    )


def _fill_from_edinet_koukoku(
    entities: list[Entity],
    manifest: RunManifest,
    *,
    limit: int | None = None,
    offline: bool = False,
    reader=None,
    scan_path: str | None = None,
) -> None:
    """**まだ空欄の企業だけ**、有報の公告掲載方法の URL から（BACKLOG 43）。

    会社自身が政府の開示書類に書いた URL を、そのページに商号が載っている
    ことで確かめて採る（`edinet_koukoku.py`）。`reader` を渡せるのは検査のため。
    """
    import datetime as dt

    from .edinet_koukoku import EdinetKoukoku
    from .edinet_koukoku import live_api as live_edinet

    # **先に、全社の一覧（koukoku-scan、BACKLOG 46）で公式サイトを正す。**
    _prefer_filing_domains(entities, manifest, scan_path=scan_path)

    missing = [e for e in entities if not e.official_domain]
    if limit:
        missing = missing[:limit]
    if not missing:
        manifest.set_breakdown(edinet_koukoku={"attempted": 0})
        return
    if reader is None:
        if offline:
            manifest.add_warning(
                "ENRICH_SKIPPED_EDINET_KOUKOKU",
                message="offline のため有報を読んでいない。**「無い」のではなく「引いていない」**",
            )
            return
        key = credential("MAILAUTH_EDINET_SUBSCRIPTION_KEY")
        contact = credential("MAILAUTH_CONTACT_EMAIL")
        if not key or not contact:
            manifest.add_warning(
                "ENRICH_SKIPPED_EDINET_KOUKOKU",
                message=(
                    "MAILAUTH_EDINET_SUBSCRIPTION_KEY か MAILAUTH_CONTACT_EMAIL が無いため"
                    "有報を読んでいない（URL の確かめに企業のサイトを読むので連絡先も要る）"
                ),
            )
            return
        from .edinet import USER_AGENT
        from .edinet_koukoku import load_vendors

        guesser = _live_co_jp_guesser(contact)
        reader = EdinetKoukoku(
            live_edinet(key, user_agent=USER_AGENT),
            guesser.confirm,
            today=dt.date.today(),
            cache_dir=cache_root() / "edinet" / "koukoku",
            vendors=load_vendors(),
            exists=guesser.exists,
        )

    taken = {e.official_domain for e in entities if e.official_domain}
    domains = reader.websites_for(
        ((e.entity_id, e.edinet_code, e.name, e.name_en) for e in missing), taken
    )
    by_id = {e.entity_id: e for e in missing}
    for entity_id, domain in domains.items():
        entity = by_id[entity_id]
        entity.official_url = f"https://{domain}/"
        entity.official_domain = domain
    stats = reader.stats
    if stats.fetch_failed or stats.list_failed_days:
        manifest.add_warning(
            "ENRICH_FAILED_EDINET_KOUKOKU",
            count=stats.fetch_failed + stats.list_failed_days,
            message=(
                "EDINET を引けなかった日・有報がある。**「書いていない」ではなく"
                "「引けなかった」**（原則5）"
            ),
        )
    manifest.set_breakdown(
        edinet_koukoku={
            **stats.to_dict(),
            "missing_before": len(missing),
            "still_missing": sum(1 for e in entities if not e.official_domain),
        }
    )


def _fill_from_co_jp_homepage(
    entities: list[Entity],
    manifest: RunManifest,
    *,
    limit: int | None = None,
    offline: bool = False,
    guesser=None,
    kana: dict[str, str] | None = None,
    cache_dir: Path | None = None,
) -> None:
    """**まだ空欄の企業だけ**、英字社名から .co.jp を推測してサイトで確かめる。

    gBizINFO と Wikidata のあとに置く。既にある値は上書きしない。
    `guesser` を渡せるのは検査のため（ネットワークに出ない）。
    """
    missing = [e for e in entities if not e.official_domain]
    if limit:
        missing = missing[:limit]
    if not missing:
        manifest.set_breakdown(co_jp_homepage={"attempted": 0})
        return
    if guesser is None:
        if offline:
            manifest.add_warning(
                "ENRICH_SKIPPED_CO_JP_HOMEPAGE",
                message=(
                    "offline のため .co.jp の推測をしていない。"
                    "**「無い」のではなく「引いていない」**"
                ),
            )
            return
        contact = credential("MAILAUTH_CONTACT_EMAIL")
        if not contact:
            # **連絡先を名乗らずに他社のサイトを読まない**（SEC と同じ作法）
            manifest.add_warning(
                "ENRICH_SKIPPED_CO_JP_HOMEPAGE",
                message=(
                    "MAILAUTH_CONTACT_EMAIL が無いため .co.jp の推測をしていない。"
                    "企業のサイトを読むので、連絡先つきの User-Agent を付ける"
                ),
            )
            return
        guesser = _live_co_jp_guesser(contact)
        if cache_dir is None:
            cache_dir = cache_root() / "co_jp_homepage"

    filled = 0
    cache = _CoJpCache(cache_dir) if cache_dir is not None else None
    # **企業ごとに並行に確かめる。** 相手はそれぞれ別の会社のサイトで、
    # 1社ずつ待つと応答待ち（時間切れ 15 秒）がそのまま積み上がる。
    # 2026-11 の国内は 606 社に 1,534 秒かかっていた。投げる間隔は
    # `_live_co_jp_guesser` の錠で全体 1 秒 2 本のまま変わらない
    def _one(e: Entity) -> str | None:
        # 英字社名が無い会社は、EDINET の読み（edinet_code で引く）から推測する
        args = (e.name, e.name_en, (kana or {}).get(e.edinet_code or ""))
        if cache is not None:
            hit = cache.get(args)
            if hit is not None:
                return hit[0]
        domain, kind = guesser.guess(*args)
        if cache is not None and kind != "unreachable":
            cache.put(args, domain, kind)
        return domain

    results = in_input_order(_one, missing, concurrency=CO_JP_CONCURRENCY, window=64)
    for entity, domain in zip(missing, results, strict=True):
        if domain:
            entity.official_url = f"https://{domain}/"
            entity.official_domain = domain
            filled += 1
    manifest.set_breakdown(
        co_jp_homepage={
            **guesser.stats.to_dict(),
            "cache_hit": cache.hits if cache is not None else 0,
            "missing_before": len(missing),
            "filled": filled,
            "still_missing": sum(1 for e in entities if not e.official_domain),
        }
    )


def _live_co_jp_guesser(contact: str, *, transport=None):
    """実際に DNS とサイトを引く推測器。公開リゾルバと連絡先つき UA を使う。"""
    import httpx

    from ..config import load_yaml
    from ..resolver import DnsResolver
    from .co_jp_guess import MAX_BYTES, CoJpGuesser, decode_page

    res_cfg = load_yaml("configs/candidates.yaml").get("resolver", {})
    resolver = DnsResolver(
        nameservers=res_cfg.get("nameservers") or None,
        timeout=float(res_cfg.get("timeout_sec", 5)),
        retries=int(res_cfg.get("retries", 2)),
        qps=float(res_cfg.get("qps", 20)),
    )
    client = httpx.Client(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": f"mailauth-observatory/0.1 ({contact})"},
        # 検査で差し替える（ネットワークに出ない）
        transport=transport,
    )
    last = [0.0]
    throttle = threading.Lock()

    def exists(name: str) -> bool:
        answer = resolver.query(name, "NS")
        return bool(answer.observed and answer.record_present)

    def fetch(url: str) -> str | None:
        # **1秒に2本まで（全体で）。** 相手は上場企業の公式サイトで、1社あたり
        # 数本しか読まない。錠を持ったまま眠り、投げる瞬間だけを直列にする
        # （応答を待つ時間は企業ごとに重なる。crt.sh の `_throttle` と同じ）
        with throttle:
            wait = 0.5 - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
        # **1ページにかける時間に上限を置く。** httpx の timeout は接続・読み
        # 1回ごとの上限で、少しずつ送り続ける相手には効かない。8 本の作業の
        # うち1本でも掴まれると、そのぶん全体が遅れる
        deadline = time.monotonic() + CO_JP_PAGE_DEADLINE_SEC
        try:
            with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                    if time.monotonic() > deadline:
                        # 読めた分で確かめる（商号はたいてい先頭の <title> にある）
                        break
                # **charset はヘッダに無ければ meta を見る**（decode_page）
                return decode_page(body, resp.charset_encoding)
        except httpx.HTTPError:
            return None

    def cert_org(domain: str) -> str | None:
        """`www.<domain>`、だめなら `<domain>` の証明書の組織名（O）。

        **検証に通った証明書だけ**を読む。HTTP は送らない（握手だけ）。
        """
        import socket
        import ssl

        for host in (f"www.{domain}", domain):
            with throttle:
                wait = 0.5 - (time.monotonic() - last[0])
                if wait > 0:
                    time.sleep(wait)
                last[0] = time.monotonic()
            try:
                context = ssl.create_default_context()
                with socket.create_connection((host, 443), timeout=10) as sock:
                    with context.wrap_socket(sock, server_hostname=host) as tls:
                        cert = tls.getpeercert() or {}
            except (OSError, ssl.SSLError, ValueError):
                continue
            for rdn in cert.get("subject", ()):
                for key, value in rdn:
                    if key == "organizationName":
                        return str(value)
            return None
        return None

    return CoJpGuesser(exists=exists, fetch=fetch, cert_org=cert_org)


def _fill_missing_urls_from_wikidata(
    entities: list[Entity], cfg: PopulationConfig, manifest: RunManifest
) -> None:
    """公式サイトが取れなかった企業だけを Wikidata（CC0）で埋める。

    **国内上場企業の 47.7% で official_url が取れていない**（2026-09 実測）。
    gBizINFO の company_url が半分の企業で空で、起点ドメインが無い企業は
    P2 で候補ゼロになり、**そのまま分母から黙って消える。**

    **既にある値は上書きしない。** gBizINFO は政府の一次情報で、Wikidata は
    利用者が編集するもの。埋めるのは空欄だけにして、どちらが入ったかを
    件数で残す（原則4）。
    """
    query_path = getattr(cfg.source, "wikidata_identity_query", None)
    if not query_path:
        manifest.add_warning(
            "ENRICH_SKIPPED_WIKIDATA",
            message=(
                f"{cfg.id} に source.wikidata_identity_query が無いため"
                "公式サイトを補っていない。**「無い」のではなく「引いていない」**"
            ),
        )
        return

    from .wikidata import WikidataError, fetch_identity

    missing_before = [e for e in entities if not e.official_domain]
    try:
        identity, stats = fetch_identity(query_path, key="houjin_bangou")
    except WikidataError as exc:
        # **引いて失敗したのであって、引いていないのではない。**
        # 同じ符号にすると、被覆率が落ちた月に「鍵が無かったのか」
        # 「相手が落ちていたのか」が文面を読まないと分からない（原則5）
        manifest.add_warning("ENRICH_FAILED_WIKIDATA", message=str(exc))
        return
    if stats.get("from_cache"):
        manifest.add_warning(
            "ENRICH_WIKIDATA_FROM_CACHE",
            message=(
                "Wikidata を引き直しても引けなかったので、"
                f"{stats['from_cache']} に引いた結果を使った"
            ),
        )

    filled = 0
    lei_filled = 0
    for entity in missing_before:
        extra = identity.get(entity.houjin_bangou or "")
        if not extra:
            continue
        website = extra.get("website")
        if website:
            domain = domain_from_url(website)
            if domain:
                entity.official_url = website
                entity.official_domain = domain
                filled += 1
        if extra.get("lei") and not entity.lei:
            entity.lei = extra["lei"]
            lei_filled += 1

    manifest.set_breakdown(
        wikidata_identity={
            **stats,
            "missing_before": len(missing_before),
            "filled": filled,
            "lei_filled": lei_filled,
            "still_missing": sum(1 for e in entities if not e.official_domain),
        }
    )
    # **法人番号で当たらなかった分を、証券コードで拾い直す。**
    # 弱い鍵なので、衝突は捨て、社名の一致を見る（`_fill_from_weak_key`）
    weak_query = getattr(cfg.source, "wikidata_ticker_query", None)
    if weak_query:
        manifest.set_breakdown(
            wikidata_securities_code=_fill_from_weak_key(
                entities,
                query_path=weak_query,
                key="securities_code",
                key_of=lambda e: getattr(e, "securities_code", None),
                name_of=lambda e: e.name,
                manifest=manifest,
                label="securities_code",
            )
        )

    if not filled and missing_before:
        manifest.add_warning(
            "WIKIDATA_FILLED_NOTHING",
            count=len(missing_before),
            message=(
                f"公式サイトの無い {len(missing_before)} 社に対し、Wikidata から"
                "1件も埋められなかった。**法人番号での突合が効いていない疑いがある** "
                f"（Wikidata 側の法人番号 {stats['key_count']} 件、"
                f"読めなかった値 {stats['unusable_keys']} 件）"
            ),
        )


def _fill_from_weak_key(
    entities: list,
    *,
    query_path: str,
    key: str,
    key_of,
    name_of,
    manifest: RunManifest,
    label: str,
) -> dict[str, Any]:
    """一意でない鍵（ティッカー・証券コード）で、残りを埋める。

    **取り逃がしと誤った突合は、悪さの桁が違う。** 突合できなければ
    その企業が分母から落ちるだけだが、誤って突合すると**別の会社の
    測定値をその企業のものとして出す。** だから2段で防ぐ。

      1. 同じ鍵に2件以上当たったら、どちらも使わない（`drop_on_conflict`）
      2. 社名が一致しなければ使わない（`names_agree`）

    弾いた数も残す ── 「当たらなかった」のか「当てたが名前が違った」のかで
    次の手が違う（原則5）。
    """
    from .wikidata import (
        WikidataError,
        fetch_identity,
        multi_values,
        names_agree,
    )

    missing_before = [e for e in entities if not e.official_domain]
    if not missing_before:
        return {"missing_before": 0, "filled": 0}

    try:
        identity, stats = fetch_identity(
            query_path, key=key, fields=("website", "lei", "label"),
            drop_on_conflict=True,
        )
    except WikidataError as exc:
        manifest.add_warning(f"ENRICH_FAILED_WIKIDATA_{label.upper()}", message=str(exc))
        return {"missing_before": len(missing_before), "filled": 0, "error": str(exc)}
    if stats.get("from_cache"):
        manifest.add_warning(
            f"ENRICH_WIKIDATA_FROM_CACHE_{label.upper()}",
            message=f"Wikidata を引けなかったので、{stats['from_cache']} に引いた結果を使った",
        )

    filled = 0
    name_rejected = 0
    no_key = 0
    for entity in missing_before:
        raw = key_of(entity)
        if not raw:
            no_key += 1
            continue
        extra = identity.get(str(raw).strip().upper()) or identity.get(str(raw).strip())
        if not extra:
            continue
        labels = multi_values(extra.get("label"))
        if not any(names_agree(name_of(entity), lbl) for lbl in labels):
            # **名前が合わないものは使わない。** 鍵が当たっただけで
            # 別の会社のサイトを紐づけるのが、この経路で一番怖い
            name_rejected += 1
            continue
        website = extra.get("website")
        domain = domain_from_url(website) if website else None
        if not domain:
            continue
        entity.official_url = website
        entity.official_domain = domain
        filled += 1
        if extra.get("lei") and not entity.lei:
            entity.lei = extra["lei"]

    out = {
        **stats,
        "missing_before": len(missing_before),
        "filled": filled,
        "name_rejected": name_rejected,
        "without_key": no_key,
        "still_missing": sum(1 for e in entities if not e.official_domain),
    }
    if name_rejected:
        manifest.add_warning(
            "WIKIDATA_NAME_MISMATCH",
            count=name_rejected,
            message=(
                f"{label} で鍵は当たったが社名が一致しない項目を {name_rejected} 件"
                "使わなかった。**別の会社のサイトを紐づけないための歯止めである。**"
                "多すぎるなら鍵か国の絞り込みを見直すこと"
            ),
        )
    return out


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
    entities: list[Entity],
    run_id: str,
    manifest: RunManifest,
    *,
    excluded_ciks: set[str] | frozenset[str] = frozenset(),
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
        # **外したのであって、上場廃止ではない**（事業会社でないもの）
        if str(before.get("cik") or "") in excluded_ciks:
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
    offline: bool = False,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    """P1 を実行し、manifest の内容を返す。

    `offline=True` は外部 API に問い合わせない実行。補完しなかったことは
    manifest に記録する。`rows` はテストで取得層を差し替えるためのもの。
    """
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
            "offline": offline,
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
        if cfg.source.primary == "sec_edgar":
            return _run_sec(
                cfg,
                run_id,
                manifest=manifest,
                out_dir=out_dir,
                limit=limit,
                dry_run=dry_run,
                offline=offline,
                rows=rows,
            )
        if cfg.source.primary == "wikidata":
            return _run_foreign(
                cfg,
                run_id,
                manifest=manifest,
                out_dir=out_dir,
                limit=limit,
                dry_run=dry_run,
                offline=offline,
                rows=rows,
            )
        if cfg.source.primary != "edinet_code_list":
            raise PopulationNotImplementedError(
                f"source.primary={cfg.source.primary} は未実装です"
                "（対応しているのは edinet_code_list / sec_edgar / wikidata）"
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
        # **証券コードが残っていても、上場廃止のものは入れない。** EDINET は
        # 上場廃止後もコードを消さないことがあり、2026-10-31 のコードリストでは
        # 「非上場」なのにコードを持つ提出者が2社あった（NOK、テクノマセマティカル）。
        # 列が空のときは判断できないので落とさない
        delisted = [r for r in listed if (r.listing_status or "").strip() == "非上場"]
        if delisted:
            listed = [r for r in listed if (r.listing_status or "").strip() != "非上場"]
            manifest.add_warning(
                "DELISTED_WITH_SECURITIES_CODE",
                count=len(delisted),
                sample=[f"{r.securities_code} {r.name}" for r in delisted[:5]],
                message=(
                    "証券コードはあるが、コードリストの上場区分が「非上場」の提出者を"
                    "母集団に入れていない"
                ),
            )
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
            _apply_enrichment(
                entities,
                cfg,
                manifest,
                limit=limit,
                offline=offline,
                kana={r.edinet_code: r.name_kana for r in listed if r.name_kana},
            )

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


def _run_foreign(
    cfg: PopulationConfig,
    run_id: str,
    *,
    manifest: RunManifest,
    out_dir: Path,
    limit: int | None,
    dry_run: bool,
    offline: bool,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    """米国・グローバル母集団（Wikidata 起点）。

    国内側と分けてあるのは identity の主キーが違うためである。
    国内は法人番号、こちらは LEI（DESIGN.md configs/populations/global500.yaml）。
    """
    from .foreign import build_entities, enrichment_summary, make_clients
    from .foreign import report as report_foreign
    from .industry import SicMapper
    from .wikidata import WikidataError, fetch

    query_path = getattr(cfg.source, "wikidata_query", None)
    if not query_path:
        raise PopulationNotImplementedError(
            f"{cfg.id} に source.wikidata_query がありません"
        )

    if rows is None:
        if offline:
            raise WikidataError(
                "offline のため Wikidata を引けません。"
                "取得層を差し替えるか offline を外してください"
            )
        rows, fetch_stats = fetch(query_path)
        manifest.set_breakdown(wikidata=fetch_stats)
    else:
        manifest.set_breakdown(wikidata={"injected_rows": len(rows)})

    manifest.counts.input = len(rows)
    if limit:
        rows = rows[:limit]

    mapping_path = cfg.source.industry.common_mapping
    sic_mapper = SicMapper.load(mapping_path) if mapping_path else None
    if sic_mapper is None:
        manifest.add_warning(
            "NO_INDUSTRY_MAPPING",
            message=(
                f"{cfg.id} に source.industry.common_mapping が無いため"
                "共通12分類を付けていない。業種軸での集計ができない"
            ),
        )

    sec, gleif, client_notes = make_clients(cfg, offline=offline)
    for note in client_notes:
        manifest.add_warning("ENRICH_SKIPPED", message=note)

    try:
        entities, stats = build_entities(
            rows,
            cfg,
            run_id,
            manifest=manifest,
            sec=sec,
            gleif=gleif,
            sic_mapper=sic_mapper,
        )
    finally:
        if sec is not None:
            sec.close()
        if gleif is not None:
            gleif.close()

    report_foreign(stats, manifest, cfg=cfg)
    manifest.set_breakdown(enrichment=enrichment_summary(sec, gleif))

    if limit:
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
    _check_acceptance(entities, cfg, manifest)

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
                "mailauth.attribution": " / ".join(cfg.attribution),
            },
        )
        manifest.add_output(OUTPUT_FILENAME, records=n)

    return manifest.to_dict()


def _run_sec(
    cfg: PopulationConfig,
    run_id: str,
    *,
    manifest: RunManifest,
    out_dir: Path,
    limit: int | None,
    dry_run: bool,
    offline: bool,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    """米国上場企業（SEC EDGAR 起点）。

    **これは Fortune 500 ではない。** 所属リストは CC0 / CC BY-SA の
    ソースから500社規模で再構築できないため、国内側と同じく
    「全上場を測り、絞り込みはビューで行う」形にしてある。
    """
    from .foreign import make_lei_free_entity
    from .industry import SicMapper
    from .sec_edgar import (
        DEFAULT_EXCHANGES,
        SecEdgarClient,
        SecEdgarError,
        load_exchange_listing,
    )

    exchanges = getattr(cfg.source, "exchanges", None) or list(DEFAULT_EXCHANGES)

    client: SecEdgarClient | None = None
    if rows is None:
        if offline:
            raise SecEdgarError(
                "offline のため SEC EDGAR を引けません。"
                "取得層を差し替えるか offline を外してください"
            )
        client = SecEdgarClient()
        rows, listing_stats = load_exchange_listing(client, exchanges=exchanges)
        manifest.set_breakdown(sec_listing=listing_stats)
    else:
        manifest.set_breakdown(sec_listing={"injected_rows": len(rows)})

    manifest.counts.input = len(rows)
    if limit:
        rows = rows[:limit]

    mapping_path = cfg.source.industry.common_mapping
    sic_mapper = SicMapper.load(mapping_path) if mapping_path else None

    # submissions で website と SIC を埋める。1社1リクエストなので重い。
    # 失敗しても止めない。件数は manifest に出る（原則4）
    wants_submissions = any(
        str(e) == "sec_edgar_submissions" for e in (cfg.source.enrich or [])
    )
    annual_reports: dict[str, Any] = {}
    registrations: dict[str, Any] = {}
    proxies: dict[str, Any] = {}
    quarterlies: dict[str, Any] = {}
    renamed: dict[str, str] = {}
    releases: dict[str, list[str]] = {}
    forms_by_cik: dict[str, frozenset[str]] = {}
    if client is not None and wants_submissions and not dry_run:
        for row in rows:
            facts = client.facts_for_cik(row.cik)
            annual_reports[row.cik] = facts.annual_report
            registrations[row.cik] = getattr(facts, "registration", None)
            proxies[row.cik] = getattr(facts, "proxy", None)
            quarterlies[row.cik] = getattr(facts, "quarterly", None)
            if getattr(facts, "renamed_at", None):
                renamed[row.cik] = facts.renamed_at
            releases[row.cik] = list(getattr(facts, "releases", None) or [])
            forms_by_cik[row.cik] = frozenset(getattr(facts, "forms", None) or ())
            if facts.website and not row.website:
                row.website = facts.website
            if facts.sic and not row.sic:
                row.sic, row.sic_label = facts.sic, facts.sic_label
    elif wants_submissions:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_SUBMISSIONS",
            message=(
                "submissions を引いていないため official_url と SIC が空になる。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    # **事業会社でないもの（SPAC・ファンド・ETF）を外す**（運営者の判断、
    # 2026-10-21）。自前のメール基盤を持たず、残すと「起点なし」として率を
    # 下げるか、運用会社のドメインに数十社が寄る。外した会社は名前と理由を
    # 記録し、公開する（P7 → gold → P8）
    excluded_rows: list[tuple[Any, str]] = []
    if getattr(cfg.source, "exclude_non_operating", False):
        if forms_by_cik:
            from .non_operating import classify

            kept = []
            for row in rows:
                reason = classify(row.sic, forms_by_cik.get(row.cik), row.name)
                if reason:
                    excluded_rows.append((row, reason))
                else:
                    kept.append(row)
            rows = kept
            by_reason: dict[str, int] = {}
            for _, reason in excluded_rows:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            manifest.set_breakdown(
                non_operating_excluded={
                    "total": len(excluded_rows),
                    **dict(sorted(by_reason.items())),
                }
            )
            if excluded_rows:
                manifest.add_warning(
                    "NON_OPERATING_EXCLUDED",
                    count=len(excluded_rows),
                    sample=[r.name for r, _ in excluded_rows[:5]],
                    message=(
                        "事業会社でないもの（SPAC・ファンド・ETF）を母集団から外した。"
                        "**「測れなかった」ではなく「測らないと決めた」**。名前と理由は "
                        f"{EXCLUDED_FILENAME} に残し、公開する"
                    ),
                )
        else:
            # 届出の様式が無いと判定できない。**判定しなかったことを黙らない**
            manifest.add_warning(
                "NON_OPERATING_NOT_CLASSIFIED",
                message=(
                    "submissions を引いていないため、事業会社でないものを見分けて"
                    "いない。SPAC・ファンド・ETF が母集団に残っている（原則5）"
                ),
            )

    # 公式サイトと LEI は Wikidata（CC0）から1クエリで補う。
    # SEC の website 欄は実測でほぼ空だった（原則5 の観点では
    # 「サイトが無い」ではなく「SEC が持っていない」）
    identity: dict[str, dict[str, str]] = {}
    wants_wikidata = any(
        str(e) == "wikidata_identity" for e in (cfg.source.enrich or [])
    )
    identity_query = getattr(cfg.source, "wikidata_identity_query", None)
    if wants_wikidata and identity_query and not offline:
        from .wikidata import WikidataError, fetch_cik_identity

        try:
            identity, identity_stats = fetch_cik_identity(identity_query)
            manifest.set_breakdown(wikidata_identity=identity_stats)
        except WikidataError as exc:
            # 引いて失敗した。引いていないのとは別（上と同じ理由）
            manifest.add_warning("ENRICH_FAILED_WIKIDATA", message=str(exc))
    elif wants_wikidata:
        manifest.add_warning(
            "ENRICH_SKIPPED_WIKIDATA",
            message=(
                "Wikidata を引いていないため official_url と LEI が空になる。"
                "「無い」のではなく「引いていない」（原則5）"
            ),
        )

    entities: list[Entity] = []
    industry_missing = 0
    matched_identity = 0
    for row in rows:
        extra = identity.get(row.cik) or {}
        if extra.get("website") and not row.website:
            row.website = extra["website"]
            matched_identity += 1
        entity = make_lei_free_entity(row, cfg, run_id, sic_mapper=sic_mapper)
        if extra.get("lei"):
            entity.lei = extra["lei"]
        if entity.common12_code is None:
            industry_missing += 1
        entities.append(entity)

    # **CIK で当たらなかった分を、ティッカーで拾い直す。**
    # 6,068社中 1,553社しか当たらない（2026-09 実測）。弱い鍵なので、
    # 衝突は捨て、社名の一致を見る（`_fill_from_weak_key`）
    ticker_query = getattr(cfg.source, "wikidata_ticker_query", None)
    weak_stats: dict[str, Any] = {}
    if ticker_query and not offline:
        weak_stats = _fill_from_weak_key(
            entities,
            query_path=ticker_query,
            key="ticker",
            key_of=lambda e: e.ticker,
            name_of=lambda e: e.name_en or e.name,
            manifest=manifest,
            label="ticker",
        )

    # **それでも残った分を、年次報告書の本文から拾う**（BACKLOG 15）。
    # 会社が「Our website address is …」と自分で書いた箇所だけを読む。
    # Wikidata の後に置くのは、1社1文書を落とす重い経路だから
    annual_stats: dict[str, Any] = {}
    wants_annual = any(str(e) == "sec_annual_report" for e in (cfg.source.enrich or []))
    if wants_annual and client is not None and annual_reports:
        from .annual_report import AnnualReportWebsites

        annual_stats = _fill_from_sec_filings(
            entities,
            annual_reports,
            AnnualReportWebsites(client),
            manifest,
            shared_code="SEC_ANNUAL_REPORT_SHARED_DOMAIN",
            failed_code="ENRICH_FAILED_SEC_ANNUAL_REPORT",
            what="年次報告書",
        )
    elif wants_annual:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_ANNUAL_REPORT",
            message=(
                "submissions を引いていないため年次報告書を読んでいない。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    # **公式サイトのある会社も、年次報告書に会社が自分で書いたサイトを優先する**
    # （BACKLOG 48）。Wikidata の公式サイトが社名変更の前のまま（Accendra Health
    # → owens-minor.com、Magnera → glatfelter.com）や、別の国向けのサイト
    # （Apple → apple.com.cn）のことがある。前の公式サイトは alt_domains に残す
    prefer_stats: dict[str, Any] = {}
    wants_prefer = any(
        str(e) == "sec_annual_report_prefer" for e in (cfg.source.enrich or [])
    )
    if wants_prefer and client is not None and annual_reports:
        from .annual_report import AnnualReportWebsites

        prefer_stats = _prefer_sec_stated(
            entities, annual_reports, AnnualReportWebsites(client), not_before=renamed
        )

    # **年次報告書がまだ無い会社（新規上場）は、目論見書・登録届出から**
    # （BACKLOG 28）。S-1 / F-1 / 424B4 の「Corporate Information」に会社が
    # 自分で書いた「Our website address is …」を、年次報告書と同じ読み方で読む
    registration_stats: dict[str, Any] = {}
    wants_registration = any(
        str(e) == "sec_registration" for e in (cfg.source.enrich or [])
    )
    if wants_registration and client is not None and registrations:
        from .annual_report import AnnualReportWebsites

        registration_stats = _fill_from_sec_filings(
            entities,
            registrations,
            AnnualReportWebsites(client),
            manifest,
            shared_code="SEC_REGISTRATION_SHARED_DOMAIN",
            failed_code="ENRICH_FAILED_SEC_REGISTRATION",
            what="目論見書・登録届出",
            # 年次報告書と違い、SPAC の合併登録（S-4 / F-4）は相手の会社の
            # 説明を含む。**他社に既に付いているドメインは、社名と合う
            # ときだけ採る**
            check_taken=True,
            # 目論見書は何年も前のことがある。**社名を変える前の届出は読まない**
            not_before=renamed,
        )
    elif wants_registration:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_REGISTRATION",
            message=(
                "submissions を引いていないため目論見書・登録届出を読んでいない。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    # **それでも残った分を、委任状説明書（DEF 14A）から**（BACKLOG 28）。
    # 年次報告書の書き方が型に合わない老舗（Telephone & Data Systems・
    # Seaboard など）も、招集通知には「our website at …」と書いている
    proxy_stats: dict[str, Any] = {}
    wants_proxy = any(str(e) == "sec_proxy" for e in (cfg.source.enrich or []))
    if wants_proxy and client is not None and proxies:
        from .annual_report import AnnualReportWebsites

        proxy_stats = _fill_from_sec_filings(
            entities,
            proxies,
            AnnualReportWebsites(client),
            manifest,
            shared_code="SEC_PROXY_SHARED_DOMAIN",
            failed_code="ENRICH_FAILED_SEC_PROXY",
            what="委任状説明書",
            check_taken=True,
            not_before=renamed,
        )
    elif wants_proxy:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_PROXY",
            message=(
                "submissions を引いていないため委任状説明書を読んでいない。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    # **それでも残った分を、四半期報告書（10-Q）から**（BACKLOG 39）
    quarterly_stats: dict[str, Any] = {}
    wants_quarterly = any(str(e) == "sec_quarterly" for e in (cfg.source.enrich or []))
    if wants_quarterly and client is not None and quarterlies:
        from .annual_report import AnnualReportWebsites

        quarterly_stats = _fill_from_sec_filings(
            entities,
            quarterlies,
            AnnualReportWebsites(client),
            manifest,
            shared_code="SEC_QUARTERLY_SHARED_DOMAIN",
            failed_code="ENRICH_FAILED_SEC_QUARTERLY",
            what="四半期報告書",
            check_taken=True,
            not_before=renamed,
        )
    elif wants_quarterly:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_QUARTERLY",
            message=(
                "submissions を引いていないため四半期報告書を読んでいない。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    # **年次報告書でも埋まらなかった分を、プレスリリースから拾う**（BACKLOG 15）。
    # 「About 〇〇」と問い合わせ先のメール（ドメイン部だけ）を読み、
    # **社名と合うドメインだけを採る**（IR 代行のドメインが混ざるため）
    release_stats: dict[str, Any] = {}
    wants_releases = any(str(e) == "sec_press_release" for e in (cfg.source.enrich or []))
    if wants_releases and client is not None and releases:
        from .press_release import PressReleaseWebsites

        release_reader = PressReleaseWebsites(client)
        still = [e for e in entities if not e.official_domain]
        for entity in still:
            website = release_reader.website_for(
                entity.cik, releases.get(entity.cik or "", []), entity.name
            )
            domain = domain_from_url(website) if website else None
            if domain:
                entity.official_url = website
                entity.official_domain = domain
        release_stats = {
            **release_reader.stats.to_dict(),
            "missing_before": len(still),
            "still_missing": sum(1 for e in entities if not e.official_domain),
        }
        if release_reader.stats.fetch_failed:
            manifest.add_warning(
                "ENRICH_FAILED_SEC_PRESS_RELEASE",
                count=release_reader.stats.fetch_failed,
                message=(
                    "プレスリリースを取得できなかったものがある。**「書いていなかった」"
                    "ではなく「読めなかった」**（原則5）"
                ),
            )
    elif wants_releases:
        manifest.add_warning(
            "ENRICH_SKIPPED_SEC_PRESS_RELEASE",
            message=(
                "submissions を引いていないためプレスリリースを読んでいない。"
                "「サイトが無い」のではなく「引いていない」（原則5）"
            ),
        )

    manifest.set_breakdown(
        wikidata_ticker=weak_stats,
        sec_annual_report=annual_stats,
        sec_annual_report_prefer=prefer_stats,
        sec_registration=registration_stats,
        sec_proxy=proxy_stats,
        sec_quarterly=quarterly_stats,
        sec_press_release=release_stats,
        sec_enrichment={
            "submissions_fetched": client.stats.submissions_fetched if client else 0,
            "submissions_failed": client.stats.submissions_failed if client else 0,
            "website_found": client.stats.website_found if client else 0,
            "sic_found": client.stats.sic_found if client else 0,
            "notes": (client.stats.notes[:5] if client else []),
        },
        industry_missing=industry_missing,
        exchanges=list(exchanges),
        wikidata_matched=matched_identity,
        lei_present=sum(1 for e in entities if e.lei),
    )
    if client is not None:
        client.close()

    # **「業種が無い」と「業種はあるが写せない」を分ける。**
    #
    # 前は一緒に数えて「写像CSVに追記すること」と出していた。2026-09-30 の
    # 米国実測では 417 件のうち **401 件が SIC を持っていなかった** ──
    # 写像を足しても1件も直らない。**当てはまらない手当てを指示していた。**
    unmapped_codes = sic_mapper.unmapped if sic_mapper else {}
    no_source = sic_mapper.no_source if sic_mapper else 0
    if unmapped_codes:
        manifest.add_warning(
            "INDUSTRY_UNMAPPED",
            count=sum(unmapped_codes.values()),
            sample=[c for c, _ in (sic_mapper.top_unmapped(5) if sic_mapper else [])],
            message=(
                "SIC はあるが共通12分類に写せなかった。"
                "**configs/industry/sic_to_common12.csv に追記すると直る。**"
            ),
        )
    if no_source:
        manifest.add_warning(
            "INDUSTRY_NOT_PROVIDED",
            count=no_source,
            message=(
                "一次情報（SEC）に業種が入っていない企業がある。"
                "**写像を足しても直らない。** ファンド・投資信託や ADR の"
                "提出者で、SEC 側に業種が無いか 0000 / 8880 が入っている"
            ),
        )

    if limit:
        manifest.add_warning(
            "DIFF_SKIPPED_DUE_TO_LIMIT",
            message=f"--limit {limit} が指定されているため前月差分を計算していない",
        )
        this_month = month_date(run_id)
        for e in entities:
            e.first_seen_month = e.first_seen_month or this_month
            e.last_seen_month = this_month
    else:
        entities = _diff_with_previous(
            entities,
            run_id,
            manifest,
            excluded_ciks={row.cik for row, _ in excluded_rows},
        )

    active = [e for e in entities if e.status != EntityStatus.DELISTED]
    manifest.counts.success = len(active)
    _check_acceptance(entities, cfg, manifest)

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
                "mailauth.attribution": " / ".join(cfg.attribution),
            },
        )
        manifest.add_output(OUTPUT_FILENAME, records=n)
        if excluded_rows or getattr(cfg.source, "exclude_non_operating", False):
            n_ex = write_parquet(
                [
                    {
                        "population_id": cfg.id,
                        "run_id": run_id,
                        "cik": row.cik,
                        "ticker": row.ticker,
                        "name": row.name,
                        "exchange": row.exchange,
                        "sic": row.sic,
                        "reason": reason,
                    }
                    for row, reason in excluded_rows
                ],
                out_dir / EXCLUDED_FILENAME,
                EXCLUDED_ENTITY_ARROW_SCHEMA,
                sort_keys=EXCLUDED_ENTITY_SORT_KEYS,
                metadata={"mailauth.phase": PHASE, "mailauth.run_id": run_id},
            )
            manifest.add_output(EXCLUDED_FILENAME, records=n_ex)

    return manifest.to_dict()


def _prefer_sec_stated(
    entities: list[Any],
    reports: dict[str, Any],
    reader: Any,
    *,
    not_before: dict[str, str] | None = None,
) -> dict[str, Any]:
    """公式サイトのある会社も、年次報告書の「Our website is …」を優先する（BACKLOG 48）。

    probe（run 2026-11-06、米国 300 社）：同じ 254・違う 24・書いていない 22。
    違うものには社名変更の前のサイト・別の国向けのサイトがあった。

    **前の公式サイトは捨てずに `alt_domains` に残す**（P2 が候補に足す）。
    ブランドのドメイン（shopify.com）でメールを出し、年次報告書には会社の
    サイト（shopifyinvestors.com）を書く会社がある。**他の会社の公式サイトと
    同じドメインは、社名と合うときだけ使う**（子会社が親のサイトを書くなど）。
    """
    from collections import Counter

    from .annual_report import name_matches_domain

    uses = Counter(e.official_domain for e in entities if e.official_domain)
    checked = same = replaced = shared = none = 0
    sample: list[str] = []
    for e in entities:
        if not e.official_domain:
            continue
        report = reports.get(e.cik or "")
        if report is None:
            continue
        checked += 1
        website = reader.website_for(
            report, e.name, not_before=(not_before or {}).get(e.cik or "")
        )
        domain = domain_from_url(website) if website else None
        if not domain:
            none += 1
            continue
        if domain == e.official_domain:
            same += 1
            continue
        if uses[domain] > 0 and not name_matches_domain(e.name, domain):
            shared += 1
            continue
        if len(sample) < 20:
            sample.append(f"{e.official_domain} → {domain}（{e.name}）")
        if e.official_domain not in e.alt_domains:
            e.alt_domains.append(e.official_domain)
        uses[e.official_domain] -= 1
        uses[domain] += 1
        e.official_url = website
        e.official_domain = domain
        replaced += 1
    return {
        "checked": checked,
        "same": same,
        "replaced": replaced,
        "not_stated": none,
        "shared_skipped": shared,
        "sample_replaced": sample,
    }


def _fill_from_sec_filings(
    entities: list[Any],
    reports: dict[str, Any],
    reader: Any,
    manifest: Any,
    *,
    shared_code: str,
    failed_code: str,
    what: str,
    check_taken: bool = False,
    not_before: dict[str, str] | None = None,
) -> dict[str, Any]:
    """起点の無い会社だけ、SEC の届出の本文から自社サイトを読んで埋める。

    **複数社の届出に同じドメインが書かれていたら、社名と合う会社にだけ
    残す。** 同じ企業グループ（brookfield.com）なら正しいが、IR 代行や
    運用会社のドメインが紛れ込むこともある（2026-10-21 の全数実行で
    14 ドメイン・42 社）。辞書（THIRD_PARTY）に無いものもここで止まる。

    `check_taken` なら、**既に他社に付いているドメイン**も同じく社名と
    合うときだけ採る。`not_before`（CIK → 社名を変えた日）より前の届出は
    読まない。
    """
    from collections import Counter

    from .annual_report import name_matches_domain

    missing = [e for e in entities if not e.official_domain]
    found: list[tuple[Any, str, str]] = []
    for entity in missing:
        website = reader.website_for(
            reports.get(entity.cik or ""),
            entity.name,
            not_before=(not_before or {}).get(entity.cik or ""),
        )
        domain = domain_from_url(website) if website else None
        if domain:
            found.append((entity, website, domain))

    uses = Counter(domain for _, _, domain in found)
    if check_taken:
        uses.update(e.official_domain for e in entities if e.official_domain)
    shared_dropped: dict[str, list[str]] = {}
    for entity, website, domain in found:
        if uses[domain] > 1 and not name_matches_domain(entity.name, domain):
            shared_dropped.setdefault(domain, []).append(entity.entity_id)
            continue
        entity.official_url = website
        entity.official_domain = domain
    stats = {
        **reader.stats.to_dict(),
        "missing_before": len(missing),
        "shared_dropped": sum(len(v) for v in shared_dropped.values()),
        "shared_dropped_domains": sorted(shared_dropped)[:20],
        "still_missing": sum(1 for e in entities if not e.official_domain),
    }
    if shared_dropped:
        manifest.add_warning(
            shared_code,
            count=sum(len(v) for v in shared_dropped.values()),
            sample=sorted(shared_dropped)[:5],
            message=(
                f"複数社の{what}に同じドメインが書かれていて、社名とも合わない"
                "ので使わなかった。**IR 代行や運用会社のドメインである可能性が"
                "高い。** 第三者と分かったら annual_report.THIRD_PARTY に足す"
            ),
        )
    if reader.stats.fetch_failed:
        manifest.add_warning(
            failed_code,
            count=reader.stats.fetch_failed,
            message=(
                f"{what}を取得できなかった企業がある。**「サイトの記述が"
                "無かった」ではなく「読めなかった」**（原則5）"
            ),
        )
    return stats


class _CoJpCache:
    """`.co.jp` の推測の結果を、社名・英字社名・読みの組で持ち越す。

    `GUESS_VERSION` が違うもの、`CO_JP_CACHE_DAYS` より古いものは使わない。
    並行に呼ばれるが、1社1ファイルなので書き込みはぶつからない。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hits = 0
        self._lock = threading.Lock()

    def _path(self, args: tuple) -> Path:
        import hashlib

        key = "\x1f".join(a or "" for a in args)
        return self.root / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}.json"

    def get(self, args: tuple) -> tuple[str | None, str] | None:
        import json
        from datetime import UTC, datetime, timedelta

        from .co_jp_guess import GUESS_VERSION

        path = self._path(args)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(data["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if data.get("version") != GUESS_VERSION:
            return None
        if datetime.now(UTC) - at > timedelta(days=CO_JP_CACHE_DAYS):
            return None
        with self._lock:
            self.hits += 1
        return data.get("domain"), str(data.get("kind") or "")

    def put(self, args: tuple, domain: str | None, kind: str) -> None:
        import json
        from datetime import UTC, datetime

        from .co_jp_guess import GUESS_VERSION

        path = self._path(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"domain": domain, "kind": kind, "version": GUESS_VERSION,
                 "at": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
