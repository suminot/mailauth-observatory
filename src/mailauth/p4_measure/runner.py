"""P4 DNS計測の本体。

確定したドメインについて、メール認証に関わる全レコードを取得し、
生のまま bronze に保存する（原則1）。

倫理的な作法として次を実装している（DESIGN.md P4 実装メモ）。
  - SPF include 先と MX ホストの解決結果をキャッシュ（必須。大手プロバイダの
    権威DNSへの負荷回避）
  - ドメイン順のシャッフル
  - レート制御
"""

from __future__ import annotations

from typing import Any

from ..config import load_measure_config
from ..contracts import MeasureTier, QueryPurpose
from ..exclusions import load as load_exclusions
from ..exclusions import require_available
from ..io import read_parquet
from ..manifest import RunManifest, config_hash
from ..paths import bronze_dir, config_path, phase_dir, phase_output
from ..progress import Progress
from ..records import find_spf_records, join_txt_strings, spf_includes
from ..records import mx_hosts as extract_mx_hosts
from ..resolver import DnsResolver, shuffled
from .backends.base import to_raw_response
from .backends.dnspython_backend import DnspythonBackend
from .bronze import BronzeWriter
from .plan import build_dane_queries, build_plan, estimate_queries
from .selectors import SelectorDictionary

PHASE = "p4_measure"
INPUT_PHASE = "p3_domains"
INPUT_FILENAME = "domains.parquet"


class MissingInputError(RuntimeError):
    pass


class UnknownBackendError(RuntimeError):
    pass


def make_backend(method: str, measure_cfg: dict):
    """バックエンドを作る。無いものに黙って落ちない。

    どの手法で測ったかは成果物の意味を変えるため、暗黙の差し替えはしない。

    `dnspython@1.1.1.1` の形でリゾルバを固定できる。リゾルバ間の差分を
    見るためで、bronze のパーティションもこのラベルで分かれる
    （DESIGN.md P4「リゾルバ戦略」のクロスチェック）。
    """
    res = measure_cfg.get("resolver", {})
    rate = measure_cfg.get("rate", {})
    upstream = [str(u) for u in (res.get("upstream") or [])]
    common = {
        "timeout": float(rate.get("timeout_sec", 5)),
        "retries": int(rate.get("retries", 3)),
    }

    if "@" in method:
        method, _, nameserver = method.partition("@")
        if method != "dnspython":
            raise UnknownBackendError(
                f"リゾルバの固定は dnspython のみ対応している: {method}@{nameserver}"
            )
        upstream = [nameserver]

    if method == "dnspython":
        return DnspythonBackend(
            resolver=DnsResolver(
                nameservers=upstream or None,
                qps=float(rate.get("qps", 80)),
                backoff=tuple(float(b) for b in (rate.get("backoff") or [1, 3, 9])),
                **common,
            )
        )
    if method == "zdns":
        from .backends.zdns_backend import ZdnsBackend

        return ZdnsBackend(
            nameservers=upstream,
            timeout=common["timeout"],
            threads=int(rate.get("threads", 80)),
        )
    raise UnknownBackendError(f"未知のバックエンド: {method}（使えるのは dnspython / zdns）")


def run(
    run_id: str,
    *,
    method: str = "dnspython",
    limit: int | None = None,
    dry_run: bool = False,
    backend=None,
    tier: str | None = None,
) -> dict[str, Any]:
    """P4 を実行し manifest の内容を返す。

    `backend` を注入できるのはテストのため。
    `tier` を指定するとその階層だけ計測する（開発中の部分実行用）。
    """
    measure_cfg = load_measure_config()
    rate = measure_cfg.get("rate", {})
    extras = measure_cfg.get("extras", {})
    dkim_cfg = measure_cfg.get("dkim", {})

    out_dir = phase_dir(run_id, PHASE)
    domains_path = phase_output(run_id, INPUT_PHASE, INPUT_FILENAME)

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        config_hash=config_hash(config_path("configs/measure.yaml")),
        params={"method": method, "limit": limit, "dry_run": dry_run, "tier": tier},
    ) as manifest:
        frame = read_parquet(domains_path)
        if frame is None:
            raise MissingInputError(
                f"{domains_path} がありません。先に p3-domains を実行してください"
            )

        measured = frame[frame["is_measured"].fillna(False).astype(bool)]
        if tier:
            measured = measured[measured["measure_tier"] == tier]

        # 除外の最終確認。**ここが最後の砦である。** P3 の出力が古くても、
        # DNS クエリを1本も出さないことをここで保証する
        excluded = load_exclusions()
        require_available(excluded)

        targets = []
        dropped_by_request = 0
        for _, r in measured.iterrows():
            domain = str(r["domain"])
            if excluded.excludes(domain, entity_id=str(r.get("entity_id") or "") or None):
                dropped_by_request += 1
                excluded.record(domain)
                continue
            targets.append(
                {
                    "domain": domain,
                    "domain_id": str(r["domain_id"]),
                    "tier": str(r["measure_tier"] or MeasureTier.C),
                }
            )
        # 同一権威への連続クエリを避ける（倫理的な作法）
        if rate.get("shuffle_domains", True):
            order = shuffled([str(i) for i in range(len(targets))], 20260801)
            targets = [targets[int(i)] for i in order]
        if limit:
            targets = targets[:limit]

        manifest.counts.input = len(targets)

        selectors = SelectorDictionary.load(measure_config=measure_cfg)
        tier_counts: dict[str, int] = {}
        for t in targets:
            tier_counts[t["tier"]] = tier_counts.get(t["tier"], 0) + 1
        estimate = estimate_queries(tier_counts, len(selectors.l1))
        manifest.set_breakdown(
            query_estimate=estimate,
            dkim_dictionary=selectors.state(),
            by_tier=dict(sorted(tier_counts.items())),
            # **除外は黙って行わない。** 分母から抜いた分を記録する（原則4）
            excluded=excluded.to_dict(),
        )
        if dropped_by_request:
            manifest.add_warning(
                "EXCLUDED_BY_REQUEST",
                count=dropped_by_request,
                sample=sorted(d for d in excluded.domains if d)[:5],
                message=(
                    "計測対象からの除外の依頼により、DNS クエリを出していない"
                    "ドメインがある。**「観測できなかった」ではなく"
                    "「測らないと決めた」である**"
                ),
            )

        if backend is None:
            backend = make_backend(method, measure_cfg)

        if dry_run:
            manifest.add_warning(
                "DRY_RUN",
                message=(
                    f"dry_run のため計測していない。見積り {estimate['total_estimate']} クエリ"
                ),
            )
            return manifest.to_dict()

        by_rcode: dict[str, int] = {}
        by_purpose: dict[str, int] = {}
        wildcard_domains: list[str] = []
        dkim_detected_domains = 0
        tcp_fallback = 0
        queries_written = 0

        # **30,000ドメインで3時間を見込む工程である。** 進捗を出さないと、
        # 実行中は何割まで進んだかも残り時間も分からない
        tracker = Progress(len(targets), f"P4 DNS計測({method})")

        with BronzeWriter(bronze_dir(run_id), method) as writer:
            for target in targets:
                queries_before = queries_written
                domain = target["domain"]
                # まず MX と SPF を引いて L2 のセレクタ推定に使う。
                # DANE の TLSA も MX ホストが分かってからでないと組めない
                pre = [
                    q
                    for q in build_plan(domain, MeasureTier.C, selectors=[])
                    if q.purpose in (QueryPurpose.MX, QueryPurpose.SPF)
                ]
                mx_values: list[str] = []
                spf_inc: list[str] = []
                for query in pre:
                    answer = backend.query(query)
                    raw = to_raw_response(
                        query,
                        answer,
                        run_id=run_id,
                        domain=domain,
                        method=method,
                        tool_version=getattr(backend, "version", "unknown"),
                        resolver_label=getattr(backend, "resolver_label", method),
                    )
                    writer.write(raw)
                    queries_written += 1
                    by_rcode[answer.rcode] = by_rcode.get(answer.rcode, 0) + 1
                    by_purpose[query.purpose] = by_purpose.get(query.purpose, 0) + 1
                    if answer.used_tcp:
                        tcp_fallback += 1
                    if not answer.observed:
                        manifest.add_failure(answer.rcode)

                    if query.purpose == QueryPurpose.MX and answer.record_present:
                        mx_values = extract_mx_hosts(answer.values)
                    if query.purpose == QueryPurpose.SPF and answer.record_present:
                        records = [join_txt_strings(c) for c in answer.txt_strings] or answer.values
                        for record in find_spf_records(records):
                            spf_inc.extend(spf_includes(record))

                # L2 で事業者を推定してセレクタを並べ替える
                domain_selectors = (
                    selectors.selectors_for(mx_values, spf_inc)
                    if target["tier"] == MeasureTier.A
                    else []
                )
                plan = [
                    q
                    for q in build_plan(
                        domain,
                        target["tier"],
                        selectors=domain_selectors,
                        extras=extras,
                        dkim_control=bool(dkim_cfg.get("negative_control", True)),
                        dkim_wildcard=bool(dkim_cfg.get("wildcard_selector_probe", True)),
                    )
                    if q.purpose not in (QueryPurpose.MX, QueryPurpose.SPF)
                ]
                if extras.get("dane") and target["tier"] == MeasureTier.A and mx_values:
                    plan += build_dane_queries(mx_values)

                control_responded = False
                dkim_hit = False
                for query in plan:
                    answer = backend.query(query)
                    raw = to_raw_response(
                        query,
                        answer,
                        run_id=run_id,
                        domain=domain,
                        method=method,
                        tool_version=getattr(backend, "version", "unknown"),
                        resolver_label=getattr(backend, "resolver_label", method),
                    )
                    writer.write(raw)
                    queries_written += 1
                    by_rcode[answer.rcode] = by_rcode.get(answer.rcode, 0) + 1
                    by_purpose[query.purpose] = by_purpose.get(query.purpose, 0) + 1
                    if answer.used_tcp:
                        tcp_fallback += 1
                    if not answer.observed:
                        manifest.add_failure(answer.rcode)

                    if query.purpose == QueryPurpose.DKIM_CONTROL and answer.record_present:
                        control_responded = True
                    if query.purpose == QueryPurpose.DKIM and answer.record_present:
                        dkim_hit = True

                if control_responded:
                    # 実在しないセレクタに応答した。何にでも答えるDNSなので
                    # DKIM の検出結果は信用できない（偽陽性ガード）
                    wildcard_domains.append(domain)
                if dkim_hit:
                    dkim_detected_domains += 1

                tracker.tick(クエリ=queries_written - queries_before)

        tracker.finish()

        # parts は writer が閉じたあとに確定する。with の中で読むと
        # 最後のパートが記録されず outputs が空になる
        parts = list(writer.parts)
        total_records = writer.records
        if writer.preexisting_parts:
            manifest.add_warning(
                "BRONZE_APPENDED_TO_EXISTING",
                count=len(writer.preexisting_parts),
                sample=writer.preexisting_parts[:5],
                message=(
                    "同じ run に既存の bronze があったため、上書きせず新しいパートに追記した"
                    "（bronze は不変・追記のみ）。同一ドメインの観測が重複している可能性がある。"
                    "やり直しなら別の run_id を使うこと"
                ),
            )

        for part in parts:
            manifest.add_output(
                f"bronze/{part['path']}",
                records=int(part["records"]),
                bytes_=int(part["bytes"]),
            )

        manifest.counts.success = queries_written - manifest.counts.failed
        manifest.set_breakdown(
            by_rcode=dict(sorted(by_rcode.items())),
            by_purpose=dict(sorted(by_purpose.items())),
            queries_total=queries_written,
            bronze_records=total_records,
            tcp_fallback=tcp_fallback,
            wildcard_detected=len(wildcard_domains),
            wildcard_sample=wildcard_domains[:5],
            dkim_detected_domains=dkim_detected_domains,
            l2_provider_hits=dict(sorted(selectors.provider_hits.items())),
            backend={"method": method, "version": getattr(backend, "version", "unknown")},
            resolver=getattr(backend, "stats", {}),
        )

        if wildcard_domains:
            manifest.add_warning(
                "WILDCARD_DNS",
                count=len(wildcard_domains),
                sample=wildcard_domains[:5],
                message=(
                    "実在しないセレクタに応答したドメイン。何にでも答える DNS のため "
                    "DKIM の検出結果は偽陽性の可能性がある"
                ),
            )

        stats = getattr(backend, "stats", {})
        if stats.get("tcp_failed"):
            manifest.add_warning(
                "TCP53_UNAVAILABLE",
                count=int(stats["tcp_failed"]),
                message=(
                    "UDP応答が truncated なクエリで TCP/53 への切り替えが失敗した。"
                    "レコードが無いのではなく取れていない。実行環境を確認すること"
                ),
            )
        if stats.get("queries"):
            hits = stats.get("cache_hits", 0)
            manifest.set_breakdown(cache_hit_rate=round(hits / (hits + stats["queries"]), 4))

        return manifest.to_dict()
