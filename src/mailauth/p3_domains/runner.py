"""P3 メールドメイン確定の本体。

候補ドメインのうち、実際にメール送信に使われているものを絞り込み、
三段の確度フラグを付ける。**ここが本システムの中核**（DESIGN.md P3）。

各候補について軽量な DNS 一次実証を行う。本格計測は P4。
  1. MX の存在を確認（Null MX の判定を含む）
  2. apex の TXT から SPF の存在を確認
  3. `_dmarc` の存在を確認
  4. From整合（候補ドメインと SPF/DMARC が同一 eTLD+1 か）を判定
  5. 確度フラグを付与
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import load_yaml
from ..contracts import (
    DOMAIN_ARROW_SCHEMA,
    DOMAIN_SORT_KEYS,
    Confidence,
    Domain,
    EntityStatus,
)
from ..exclusions import load as load_exclusions
from ..exclusions import require_available
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest, config_hash
from ..paths import config_path, phase_dir, phase_output
from ..records import (
    find_dmarc_records,
    find_spf_records,
    is_null_mx,
    join_txt_strings,
    spf_all_qualifier,
    spf_includes,
)
from ..records import (
    mx_hosts as extract_mx_hosts,
)
from ..resolver import DnsResolver, Resolver, shuffled
from .classify import (
    Probe,
    assign_tier,
    build_evidence,
    classify_confidence,
    classify_role,
    evidence_count,
)

PHASE = "p3_domains"
OUTPUT_FILENAME = "domains.parquet"
INPUT_PHASE = "p2_candidates"
INPUT_FILENAME = "domain_candidates.parquet"

#: 同時に応答を待つドメイン数。**投げる間隔（qps）とは別のつまみ。**
#: P4 と同じ理由（往復が直列に積み上がる）で、応答待ちだけを重ねる。
#: 2026-09 の国内計測では 2,158ドメインで23分かかっていた
DEFAULT_CONCURRENCY = 8

#: 何ドメインぶんまとめて待ち合わせるか。同時本数より十分大きくしないと、
#: 塊の終わりで待ち合わせるたびに並行の利きが落ちる
PROBE_CHUNK = 64


def _probe_in_order(
    probe: Callable[[tuple[str, str]], Probe],
    keys: list[tuple[str, str]],
    concurrency: int,
) -> Iterator[Probe]:
    """応答待ちだけを重ね、**結果は入力順に返す。**

    完了順に返すと、同じ入力から出力の並びが変わる（原則6）。
    投げる間隔は `DnsResolver` が全スレッドで共有しているので、
    権威DNSから見た単位時間あたりの本数は直列のときと変わらない。
    """
    if concurrency <= 1:
        for key in keys:
            yield probe(key)
        return

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(keys), PROBE_CHUNK):
            # map は入力順に返す。完了順ではない
            yield from pool.map(probe, keys[start : start + PROBE_CHUNK])


class MissingInputError(RuntimeError):
    pass


def domain_id(entity_id: str, domain: str) -> str:
    digest = hashlib.sha256(f"{entity_id}|{domain}".encode()).hexdigest()
    return f"d:{digest[:16]}"


def probe_domain(
    domain: str,
    resolver: Resolver,
    *,
    discovery_methods: list[str],
    independent_methods: set[str],
) -> Probe:
    """1ドメインの軽量な一次実証。

    From整合は、SPF が候補ドメイン自身の apex にあるかで判定する。
    候補ドメインに SPF があれば、そのドメインを名乗る送信は
    そのドメインのポリシーで評価されるため整合していると見なせる。
    """
    probe = Probe(
        domain=domain,
        discovery_methods=sorted(discovery_methods),
        independent_evidence_count=len(set(discovery_methods) & independent_methods),
    )

    mx = resolver.query(domain, "MX")
    if not mx.observed:
        probe.failures.append(f"MX:{mx.rcode}")
        probe.observed = False
    elif mx.record_present:
        probe.null_mx = is_null_mx(mx.values)
        hosts = extract_mx_hosts(mx.values)
        probe.mx_hosts = hosts
        # Null MX は「受け取らない」宣言なので MX 実在とは見なさない
        probe.mx_exists = bool(hosts) and not probe.null_mx

    txt = resolver.query(domain, "TXT")
    if not txt.observed:
        probe.failures.append(f"TXT:{txt.rcode}")
        probe.observed = False
    elif txt.record_present:
        records = [join_txt_strings(c) for c in txt.txt_strings] or txt.values
        spf_records = find_spf_records(records)
        if spf_records:
            probe.spf_exists = True
            # 複数ある場合は PermError だが、その判定は P5。ここでは先頭を見る
            first = spf_records[0]
            probe.spf_all_qualifier = spf_all_qualifier(first)
            probe.spf_includes = spf_includes(first)
            probe.spf_aligned = True

    dmarc = resolver.query(f"_dmarc.{domain}", "TXT")
    if not dmarc.observed:
        probe.failures.append(f"DMARC:{dmarc.rcode}")
    elif dmarc.record_present:
        records = [join_txt_strings(c) for c in dmarc.txt_strings] or dmarc.values
        probe.dmarc_exists = bool(find_dmarc_records(records))

    return probe


def run(
    run_id: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    resolver: Resolver | None = None,
    config: str = "configs/candidates.yaml",
) -> dict[str, Any]:
    cfg = load_yaml(config)
    res_cfg = cfg.get("resolver", {})
    # **投げる間隔（qps）とは別のつまみ。** 応答待ちだけを重ねる
    concurrency = max(int(res_cfg.get("concurrency", DEFAULT_CONCURRENCY)), 1)
    independent = set(
        (cfg.get("confidence") or {}).get("independent_methods")
        or ["official_url", "ct_log", "manual"]
    )

    out_dir = phase_dir(run_id, PHASE)
    candidates_path = phase_output(run_id, INPUT_PHASE, INPUT_FILENAME)
    entities_path = phase_output(run_id, "p1_population", "entities.parquet")

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        config_hash=config_hash(config_path(config)),
        params={"limit": limit, "dry_run": dry_run, "config": config},
    ) as manifest:
        candidates = read_parquet(candidates_path)
        if candidates is None:
            raise MissingInputError(
                f"{candidates_path} がありません。先に p2-candidates を実行してください"
            )
        entities = read_parquet(entities_path)
        official_by_entity: dict[str, str | None] = {}
        if entities is not None:
            for _, row in entities.iterrows():
                if str(row.get("status")) == EntityStatus.DELISTED:
                    continue
                value = row.get("official_domain")
                official_by_entity[str(row["entity_id"])] = (
                    None if not value or str(value) == "nan" else str(value)
                )

        # 同じ (entity, domain) が経路ごとに複数行ある。ドメイン単位に畳む
        grouped: dict[tuple[str, str], list[str]] = {}
        for _, row in candidates.iterrows():
            key = (str(row["entity_id"]), str(row["domain"]))
            grouped.setdefault(key, []).append(str(row["discovery_method"]))

        # 除外の再確認。**P2 の出力が古い可能性がある。** 除外の依頼は
        # 前月の候補には効いていないので、ここでも落とす（多重防御）
        excluded = load_exclusions()
        require_available(excluded)
        dropped_by_request = 0
        if excluded.entries:
            kept: dict[tuple[str, str], list[str]] = {}
            for (entity_id, domain), methods in grouped.items():
                if excluded.excludes(domain, entity_id=entity_id):
                    dropped_by_request += 1
                    excluded.record(domain)
                    continue
                kept[(entity_id, domain)] = methods
            grouped = kept

        keys = list(grouped)
        if res_cfg.get("shuffle", True):
            # 同一権威への連続クエリを避ける
            order = shuffled([f"{i}" for i in range(len(keys))], res_cfg.get("shuffle_seed"))
            keys = [keys[int(i)] for i in order]
        if limit:
            keys = keys[:limit]

        manifest.counts.input = len(keys)

        if resolver is None:
            resolver = DnsResolver(
                nameservers=res_cfg.get("nameservers") or None,
                timeout=float(res_cfg.get("timeout_sec", 5)),
                retries=int(res_cfg.get("retries", 2)),
                qps=float(res_cfg.get("qps", 20)),
            )

        domains: list[Domain] = []
        by_confidence: dict[str, int] = {}
        by_role: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        entities_with_confirmed: set[str] = set()
        all_entities: set[str] = set()
        failed_probes = 0

        def _probe(key: tuple[str, str]) -> Probe:
            """1ドメインぶんの一次実証。**worker スレッドから呼ばれる。**

            引くだけで、件数にも出力にも触らない。`DnsResolver` は投げる間隔と
            キャッシュと統計を錠で守ってあるので、複数スレッドから呼んでよい。
            """
            _entity_id, domain = key
            return probe_domain(
                domain,
                resolver,
                discovery_methods=grouped[key],
                independent_methods=independent,
            )

        # **応答待ちだけを重ねる。** 結果は入力順に受け取る
        # （完了順にすると出力の並びが実行ごとに変わる）
        for (entity_id, domain), probe in zip(
            keys, _probe_in_order(_probe, keys, concurrency), strict=True
        ):
            all_entities.add(entity_id)
            if not probe.observed:
                failed_probes += 1
                for failure in probe.failures:
                    manifest.add_failure(failure.split(":", 1)[-1])

            confidence = classify_confidence(
                mx_exists=probe.mx_exists,
                spf_aligned=probe.spf_aligned,
                dkim_found=probe.dkim_found,
                dmarc_exists=probe.dmarc_exists,
                independent_evidence_count=probe.independent_evidence_count,
                spf_hard_deny=probe.spf_hard_deny,
            )
            role = classify_role(domain, official_by_entity.get(entity_id), confidence)
            tier = assign_tier(confidence)

            by_confidence[confidence] = by_confidence.get(confidence, 0) + 1
            by_role[role] = by_role.get(role, 0) + 1
            by_tier[tier] = by_tier.get(tier, 0) + 1
            if confidence == Confidence.CONFIRMED:
                entities_with_confirmed.add(entity_id)

            domains.append(
                Domain(
                    domain_id=domain_id(entity_id, domain),
                    entity_id=entity_id,
                    run_id=run_id,
                    domain=domain,
                    domain_role=role,
                    confidence=confidence,
                    mx_exists=probe.mx_exists,
                    spf_exists=probe.spf_exists,
                    spf_aligned=probe.spf_aligned,
                    dmarc_exists=probe.dmarc_exists,
                    dkim_found=probe.dkim_found,
                    evidence_count=evidence_count(probe),
                    evidence=json.dumps(build_evidence(probe), ensure_ascii=False),
                    # 一次実証が取れなかったドメインは計測対象にしない。
                    # 「取れなかった」を「無かった」として下流に渡さないため
                    is_measured=probe.observed,
                    measure_tier=tier,
                    exclusion_reason=None if probe.observed else "primary_probe_failed",
                    null_mx=probe.null_mx,
                    spf_hard_deny=probe.spf_hard_deny,
                )
            )

        measured = sum(1 for d in domains if d.is_measured)
        manifest.counts.success = measured
        manifest.counts.skipped = len(domains) - measured

        stats = getattr(resolver, "stats", {})
        if stats.get("tcp_failed"):
            # TXT が多いドメインでは UDP 応答が truncated になる。
            # TCP/53 が通らない経路では SPF を一切観測できず、
            # 「SPF が無い」と誤読しかねないので大きく警告する
            manifest.add_warning(
                "TCP53_UNAVAILABLE",
                count=int(stats["tcp_failed"]),
                message=(
                    "UDP応答が truncated なドメインで TCP/53 への切り替えが失敗した。"
                    "該当ドメインは observed=false（取れなかった）として計測対象外にしてある。"
                    "SPF が無いわけではない。実行環境が TCP/53 を通すか確認すること"
                ),
            )

        without_confirmed = sorted(all_entities - entities_with_confirmed)
        manifest.set_breakdown(
            by_confidence=dict(sorted(by_confidence.items())),
            by_role=dict(sorted(by_role.items())),
            by_measure_tier=dict(sorted(by_tier.items())),
            entities_with_no_confirmed_domain=len(without_confirmed),
            entities_with_no_confirmed_sample=without_confirmed[:5],
            failed_probes=failed_probes,
            resolver=getattr(resolver, "stats", {}),
            null_mx_domains=sum(1 for d in domains if d.null_mx),
            spf_hard_deny_domains=sum(1 for d in domains if d.spf_hard_deny),
            # **除外は黙って行わない。** 分母から抜いた分を記録する（原則4）
            excluded=excluded.to_dict(),
        )
        if dropped_by_request:
            manifest.add_warning(
                "EXCLUDED_BY_REQUEST",
                count=dropped_by_request,
                sample=sorted(d for d in excluded.domains if d)[:5],
                message=(
                    "計測対象からの除外の依頼により候補から落とした。"
                    "**「観測できなかった」ではなく「測らないと決めた」である。** "
                    "P2 の出力より後に依頼が入った場合ここで落ちる"
                ),
            )
        _check_acceptance(cfg, by_confidence, len(domains), len(without_confirmed),
                          len(all_entities), manifest)

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            n = write_parquet(
                domains,
                out_dir / OUTPUT_FILENAME,
                DOMAIN_ARROW_SCHEMA,
                sort_keys=DOMAIN_SORT_KEYS,
                metadata={"mailauth.phase": PHASE, "mailauth.run_id": run_id},
            )
            manifest.add_output(OUTPUT_FILENAME, records=n)

        return manifest.to_dict()


def _check_acceptance(
    cfg: dict,
    by_confidence: dict[str, int],
    total: int,
    without_confirmed: int,
    entities: int,
    manifest: RunManifest,
) -> None:
    """DESIGN.md P3 の受け入れ基準。満たさなくても止めず記録する。"""
    acc = (cfg.get("acceptance") or {}).get("p3") or {}
    checks: dict[str, Any] = {}

    min_rate = acc.get("min_confirmed_rate")
    if min_rate is not None and total:
        rate = by_confidence.get(Confidence.CONFIRMED, 0) / total
        checks["confirmed_rate"] = round(rate, 4)
        if rate < min_rate:
            manifest.add_warning(
                "ACCEPTANCE_CONFIRMED_RATE_LOW",
                message=f"confirmed が {rate:.1%} で下限 {min_rate:.1%} に届いていない",
            )

    max_rate = acc.get("max_entities_without_confirmed_rate")
    if max_rate is not None and entities:
        rate = without_confirmed / entities
        checks["entities_without_confirmed_rate"] = round(rate, 4)
        if rate > max_rate:
            manifest.add_warning(
                "ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED",
                message=(
                    f"confirmed ドメインを持たない企業が {rate:.1%} で"
                    f"閾値 {max_rate:.1%} を超えている。手動確認の候補"
                ),
            )

    manifest.set_breakdown(acceptance=checks)
