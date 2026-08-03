"""P2 ドメイン候補生成の本体。

企業1社に対し、その企業が保有している可能性のあるドメイン群を展開する。
ここではまだ絞らない。**再現率を優先**する（DESIGN.md P2）。

四つの経路
  1. official_url  P1 が gBizINFO から取った公式サイトのドメイン
  2. ct_log        crt.sh の SAN から eTLD+1 を抽出
  3. spf_redirect  SPF の redirect= が別組織を指していれば候補に
  4. dmarc_rua     _dmarc の rua 宛先が自社ドメインなら候補に
  5. manual        手動メンテナンスの CSV。グループ会社・事業ブランド用
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import re
import statistics
from pathlib import Path
from typing import Any

from ..config import load_yaml
from ..contracts import (
    DOMAIN_CANDIDATE_ARROW_SCHEMA,
    DOMAIN_CANDIDATE_SORT_KEYS,
    DiscoveryMethod,
    DomainCandidate,
    EntityStatus,
)
from ..ctlog import CrtShClient, CtSource, DisabledCtSource
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest, config_hash
from ..normalize import etld_plus_one
from ..paths import cache_root, config_path, month_date, phase_dir, phase_output
from ..records import (
    dmarc_report_domains,
    find_dmarc_records,
    find_spf_records,
    join_txt_strings,
    spf_redirect,
)
from ..resolver import DnsResolver, Resolver, shuffled

PHASE = "p2_candidates"
OUTPUT_FILENAME = "domain_candidates.parquet"
INPUT_PHASE = "p1_population"
INPUT_FILENAME = "entities.parquet"


class MissingInputError(RuntimeError):
    """前工程の出力が無い。空の結果を作らず理由を添えて止める。"""


def candidate_id(entity_id: str, domain: str, method: str) -> str:
    """決まった入力から決まった ID を作る（原則6）。"""
    digest = hashlib.sha256(f"{entity_id}|{domain}|{method}".encode()).hexdigest()
    return f"c:{digest[:16]}"


def load_manual_domains(path: str | Path) -> dict[str, list[tuple[str, str]]]:
    """手動辞書を entity_id -> [(domain, note)] にして読む。"""
    p = config_path(str(path))
    if not p.is_file():
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            entity_id = (row.get("entity_id") or "").strip()
            domain = etld_plus_one((row.get("domain") or "").strip())
            if not entity_id or not domain:
                continue
            out.setdefault(entity_id, []).append((domain, (row.get("note") or "").strip()))
    return out


class _Collector:
    """1社ぶんの候補を集める。同じドメインが複数経路で出たら経路を足す。"""

    def __init__(self, entity_id: str, limit: int) -> None:
        self.entity_id = entity_id
        self.limit = limit
        #: domain -> {method: source_detail}
        self.found: dict[str, dict[str, str]] = {}
        self.truncated = 0

    def add(self, domain: str | None, method: str, detail: str = "") -> None:
        apex = etld_plus_one(domain) if domain else None
        if not apex:
            return
        if apex not in self.found and len(self.found) >= self.limit:
            self.truncated += 1
            return
        self.found.setdefault(apex, {}).setdefault(method, detail)


def load_report_vendor_patterns(
    path: str = "configs/vendors/dmarc_rua_vendors.yaml",
) -> list[tuple[str, re.Pattern[str]]]:
    """DMARC レポート処理ベンダーのドメイン判定規則を読む。

    rua の宛先が第三者サービスなら、それは「その企業のドメイン」ではない。
    候補に入れると TwoFive の dmarc25.jp が数百社に紐づいてしまい、
    他社のドメインを計測することにもなる。
    """
    try:
        data = load_yaml(path)
    except FileNotFoundError:
        return []
    out: list[tuple[str, re.Pattern[str]]] = []
    for rule in data.get("rules", []):
        pattern = (rule.get("match") or {}).get("pattern")
        if pattern:
            out.append((rule.get("vendor", "unknown"), re.compile(pattern, re.IGNORECASE)))
    return out


def match_report_vendor(
    domain: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> str | None:
    for vendor, pattern in patterns:
        if pattern.search(domain):
            return vendor
    return None


def _discover_from_dns(
    apex: str,
    resolver: Resolver,
    collector: _Collector,
    use_redirect: bool,
    use_rua: bool,
    vendor_patterns: list[tuple[str, re.Pattern[str]]],
    vendor_hits: dict[str, int],
) -> None:
    """SPF redirect と DMARC rua から候補を足す。

    どちらも DNS 由来なので、確度判定では「独立した裏付け」に数えない
    （configs/candidates.yaml の confidence.independent_methods）。
    """
    if use_redirect:
        txt = resolver.query(apex, "TXT")
        if txt.observed and txt.record_present:
            records = [join_txt_strings(c) for c in txt.txt_strings] or txt.values
            for record in find_spf_records(records):
                target = spf_redirect(record)
                if target:
                    collector.add(target, DiscoveryMethod.SPF_REDIRECT, f"redirect from {apex}")

    if use_rua:
        dmarc = resolver.query(f"_dmarc.{apex}", "TXT")
        if dmarc.observed and dmarc.record_present:
            records = [join_txt_strings(c) for c in dmarc.txt_strings] or dmarc.values
            for record in find_dmarc_records(records):
                for domain in dmarc_report_domains(record, "rua"):
                    vendor = match_report_vendor(domain, vendor_patterns)
                    if vendor:
                        # 第三者のレポート処理サービス。その企業のドメインではない。
                        # P6 の dmarc_vendor 推定には有用なので件数だけ残す
                        vendor_hits[vendor] = vendor_hits.get(vendor, 0) + 1
                        continue
                    collector.add(domain, DiscoveryMethod.DMARC_RUA, f"rua of {apex}")


def run(
    run_id: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    resolver: Resolver | None = None,
    ct_source: CtSource | None = None,
    config: str = "configs/candidates.yaml",
) -> dict[str, Any]:
    """P2 を実行し manifest の内容を返す。

    `resolver` と `ct_source` を注入できるのはテストのため。
    省略すると実際に DNS と crt.sh に問い合わせる。
    """
    cfg = load_yaml(config)
    discovery = cfg.get("discovery", {})
    limits = cfg.get("limits", {})
    res_cfg = cfg.get("resolver", {})
    ct_cfg = cfg.get("ct", {})

    out_dir = phase_dir(run_id, PHASE)
    entities_path = phase_output(run_id, INPUT_PHASE, INPUT_FILENAME)

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        config_hash=config_hash(config_path(config)),
        params={"limit": limit, "dry_run": dry_run, "config": config},
    ) as manifest:
        entities = read_parquet(entities_path)
        if entities is None:
            raise MissingInputError(
                f"{entities_path} がありません。先に p1-population を実行してください"
            )

        active = entities[entities["status"] != EntityStatus.DELISTED]
        if limit:
            active = active.head(limit)
        manifest.counts.input = len(active)

        if resolver is None:
            resolver = DnsResolver(
                nameservers=res_cfg.get("nameservers") or None,
                timeout=float(res_cfg.get("timeout_sec", 5)),
                retries=int(res_cfg.get("retries", 2)),
                qps=float(res_cfg.get("qps", 20)),
            )
        if ct_source is None:
            ct_source = (
                CrtShClient(
                    cache_dir=cache_root() / "crtsh",
                    qps=float(ct_cfg.get("qps", 0.5)),
                    timeout=float(ct_cfg.get("timeout_sec", 60)),
                    retries=int(ct_cfg.get("retries", 2)),
                )
                if discovery.get("ct_log")
                else DisabledCtSource()
            )

        manual = (
            load_manual_domains(cfg.get("manual_domains", ""))
            if discovery.get("manual")
            else {}
        )

        vendor_patterns = load_report_vendor_patterns()
        vendor_hits: dict[str, int] = {}

        per_entity_limit = int(limits.get("max_per_entity", 500))
        total_limit = int(limits.get("max_candidates_total", 30000))

        # 起点となる official_domain が無い企業は、手動辞書が無ければ候補ゼロになる
        without_domain = [
            str(r["entity_id"])
            for _, r in active.iterrows()
            if not r.get("official_domain") or str(r.get("official_domain")) == "nan"
        ]
        if without_domain and len(without_domain) == len(active) and not manual:
            manifest.add_warning(
                "NO_SEED_DOMAINS",
                count=len(without_domain),
                message=(
                    "全企業に official_domain が無く、手動辞書も空のため候補を展開できない。"
                    "gBizINFO のトークン（MAILAUTH_GBIZINFO_TOKEN）を設定して P1 を再実行するか、"
                    "configs/domains/manual_domains.csv に起点ドメインを追加すること"
                ),
            )

        # 権威DNSへの連続クエリを避けるため、処理順をシャッフルする
        order = list(range(len(active)))
        if res_cfg.get("shuffle", True):
            order = [int(i) for i in shuffled([str(i) for i in order], res_cfg.get("shuffle_seed"))]

        rows = active.reset_index(drop=True)
        collectors: dict[str, _Collector] = {}
        method_counts: dict[str, int] = {}
        ct_stats = {"searched": 0, "from_cache": 0, "errors": 0, "raw_names": 0, "truncated": 0}
        total = 0
        hit_total_limit = False

        for idx in order:
            row = rows.iloc[idx]
            entity_id = str(row["entity_id"])
            collector = _Collector(entity_id, per_entity_limit)
            collectors[entity_id] = collector

            official = row.get("official_domain")
            official = None if not official or str(official) == "nan" else str(official)

            if discovery.get("official_url") and official:
                collector.add(official, DiscoveryMethod.OFFICIAL_URL, "P1 gBizINFO company_url")

            if discovery.get("manual"):
                for domain, note in manual.get(entity_id, []):
                    collector.add(domain, DiscoveryMethod.MANUAL, note or "manual dictionary")

            if official:
                if discovery.get("ct_log"):
                    ct = ct_source.search(official)
                    ct_stats["searched"] += 1
                    ct_stats["raw_names"] += ct.raw_names
                    if ct.from_cache:
                        ct_stats["from_cache"] += 1
                    if ct.error:
                        ct_stats["errors"] += 1
                        manifest.add_failure("ct_log_error")
                    ct_max = int(ct_cfg.get("max_per_entity", 200))
                    for found in ct.found[:ct_max]:
                        collector.add(found, DiscoveryMethod.CT_LOG, f"crt.sh %.{official}")
                    if len(ct.found) > ct_max:
                        ct_stats["truncated"] += len(ct.found) - ct_max

                _discover_from_dns(
                    official,
                    resolver,
                    collector,
                    bool(discovery.get("spf_redirect")),
                    bool(discovery.get("dmarc_rua")),
                    vendor_patterns,
                    vendor_hits,
                )

            total += len(collector.found)
            if total >= total_limit:
                hit_total_limit = True
                break

        if hit_total_limit:
            manifest.add_warning(
                "CANDIDATE_LIMIT_REACHED",
                message=(
                    f"候補総数が上限 {total_limit} に達したため展開を打ち切った。"
                    "処理していない企業が残っている"
                ),
            )

        # 候補レコードの組み立て
        candidates: list[DomainCandidate] = []
        # discovered_at は実行時刻ではなく run の月initialにする。
        # 壁時計を埋めると同じ入力でも出力のバイト列が変わり、原則6（冪等）が
        # 壊れる。月次計測なので「いつ発見したか」の粒度は月で足りる。
        # 実行時刻そのものは manifest の started_at に残る。
        discovered_at = dt.datetime.combine(
            month_date(run_id), dt.time(0, 0), tzinfo=dt.UTC
        )
        per_entity: list[int] = []
        domain_to_entities: dict[str, set[str]] = {}

        for entity_id, collector in collectors.items():
            per_entity.append(len(collector.found))
            for domain, methods in collector.found.items():
                domain_to_entities.setdefault(domain, set()).add(entity_id)
                for method, detail in methods.items():
                    method_counts[str(method)] = method_counts.get(str(method), 0) + 1
                    candidates.append(
                        DomainCandidate(
                            candidate_id=candidate_id(entity_id, domain, method),
                            entity_id=entity_id,
                            run_id=run_id,
                            domain=domain,
                            discovery_method=method,
                            discovered_at=discovered_at,
                            source_detail=detail or None,
                            is_apex=True,
                        )
                    )
            if collector.truncated:
                manifest.add_warning(
                    "PER_ENTITY_LIMIT_REACHED",
                    count=collector.truncated,
                    sample=[entity_id],
                    message=f"1社あたりの候補上限 {per_entity_limit} を超えた分を捨てた",
                )

        shared = {d: sorted(e) for d, e in domain_to_entities.items() if len(e) > 1}
        if shared:
            manifest.add_warning(
                "DOMAIN_SHARED_BY_ENTITIES",
                count=len(shared),
                sample=list(shared)[:5],
                message="同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性",
            )

        zero = sum(1 for n in per_entity if n == 0)
        manifest.counts.success = len(collectors) - zero
        manifest.counts.skipped = zero

        percentiles = _percentiles(per_entity)
        manifest.set_breakdown(
            by_discovery_method=dict(sorted(method_counts.items())),
            domains_per_entity=percentiles,
            entities_with_zero_candidates=zero,
            unique_domains=len(domain_to_entities),
            candidate_rows=len(candidates),
            ct=ct_stats,
            resolver=getattr(resolver, "stats", {}),
            shared_domains=len(shared),
            # rua が第三者サービスを指していた件数。P6 の dmarc_vendor 推定の材料
            dmarc_report_vendors=dict(sorted(vendor_hits.items())),
        )
        _check_acceptance(cfg, percentiles, zero, len(collectors), manifest)

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            n = write_parquet(
                candidates,
                out_dir / OUTPUT_FILENAME,
                DOMAIN_CANDIDATE_ARROW_SCHEMA,
                sort_keys=DOMAIN_CANDIDATE_SORT_KEYS,
                metadata={"mailauth.phase": PHASE, "mailauth.run_id": run_id},
            )
            manifest.add_output(OUTPUT_FILENAME, records=n)

        return manifest.to_dict()


def _percentiles(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"p50": 0, "p90": 0, "max": 0, "mean": 0.0}
    ordered = sorted(values)
    return {
        "p50": int(statistics.median(ordered)),
        "p90": int(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]),
        "max": int(ordered[-1]),
        "mean": round(sum(ordered) / len(ordered), 2),
    }


def _check_acceptance(
    cfg: dict, percentiles: dict, zero: int, entities: int, manifest: RunManifest
) -> None:
    """DESIGN.md P2 の受け入れ基準。満たさなくても止めず記録する。"""
    acc = (cfg.get("acceptance") or {}).get("p2") or {}
    checks: dict[str, Any] = {}

    lo = acc.get("median_per_entity_min")
    hi = acc.get("median_per_entity_max")
    if lo is not None and hi is not None:
        ok = lo <= percentiles["p50"] <= hi
        checks["median_in_range"] = ok
        if not ok:
            manifest.add_warning(
                "ACCEPTANCE_MEDIAN_OUT_OF_RANGE",
                message=f"1社あたり候補数の中央値 {percentiles['p50']} が想定 {lo}〜{hi} の外",
            )

    p90_max = acc.get("p90_per_entity_max")
    if p90_max is not None:
        ok = percentiles["p90"] <= p90_max
        checks["p90_within_limit"] = ok
        if not ok:
            manifest.add_warning(
                "ACCEPTANCE_P90_TOO_HIGH",
                message=f"p90 が {percentiles['p90']} で上限 {p90_max} を超えている",
            )

    max_zero = acc.get("max_zero_candidate_rate")
    if max_zero is not None and entities:
        rate = zero / entities
        checks["zero_candidate_rate"] = round(rate, 4)
        if rate > max_zero:
            manifest.add_warning(
                "ACCEPTANCE_ZERO_CANDIDATES",
                message=f"候補ゼロの企業が {rate:.1%} で閾値 {max_zero:.1%} を超えている",
            )

    manifest.set_breakdown(acceptance=checks)
