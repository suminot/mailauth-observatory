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

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
from .bronze import BronzeWriter, measured_domains
from .plan import (
    build_dane_queries,
    build_plan,
    estimate_queries,
    restrict_to_configured,
)
from .selectors import SelectorDictionary

PHASE = "p4_measure"
INPUT_PHASE = "p3_domains"
INPUT_FILENAME = "domains.parquet"

#: 同時に応答を待つドメイン数の既定値。**投げる間隔（qps）とは別のつまみ。**
#:
#: 2026-09 の国内計測で、1ドメイン 7.7秒・実効 7.5 qps しか出ていなかった。
#: 1ドメイン約58本を1本ずつ引いており、**上流への往復（約130ミリ秒）が
#: そのまま積み上がる。** 設定の `qps: 80` は一度も効いていない。
#:
#: 8本だと 8/7.5 ≒ 1.07ドメイン/秒 ＝ 約62 qps で、**設定の上限 80 の内側に
#: 収まる。** これ以上増やすと qps の方が効きはじめ、速くならずに
#: 権威DNSへの本数だけが上限に張り付く
DEFAULT_CONCURRENCY = 8

#: 何ドメインぶんまとめて待ち合わせるか。**全件を一度に投げない** ──
#: 1ドメイン約58件の応答を保持するので、30,000ドメインを一度に抱えると
#: メモリに乗らない。同時本数より十分大きくしないと、塊の終わりで
#: 待ち合わせるたびに並行の利きが落ちる
MEASURE_CHUNK = 64


@dataclass
class _Measured:
    """1ドメインぶんの取得結果。**書き出しは呼び出し側（主スレッド）が行う。**"""

    domain: str
    answers: list[tuple[Any, Any]]
    control_responded: bool
    dkim_hit: bool


def _in_input_order(
    measure: Callable[[dict], _Measured], targets: list[dict], concurrency: int
) -> Iterator[_Measured]:
    """応答待ちだけを重ね、**結果は入力順に返す。**

    完了順に返すと、bronze に並ぶ順が実行ごとに変わる。同じ入力から同じ
    並びが出ないと、差分を取って中身を比べることができなくなる（原則6）。

    投げる間隔は `DnsResolver` が全スレッドで共有しているので、権威DNSから
    見た単位時間あたりの本数は直列のときと変わらない。変わるのは、
    こちらが応答を待っている時間だけである。
    """
    if concurrency <= 1:
        # **並行にしない経路を残す。** 注入したバックエンドで測る検査や、
        # 1件ずつ追いたいときに使う
        for target in targets:
            yield measure(target)
        return

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(targets), MEASURE_CHUNK):
            chunk = targets[start : start + MEASURE_CHUNK]
            # map は入力順に返す。完了順ではない
            yield from pool.map(measure, chunk)


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
    concurrency: int | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """P4 を実行し manifest の内容を返す。

    `backend` を注入できるのはテストのため。
    `tier` を指定するとその階層だけ計測する（開発中の部分実行用）。

    `resume` は**同じ run の続きから測る**。時間切れで殺された実行が残した
    bronze を読み、最後まで書けたドメインを飛ばす。既定で有効にしないのは、
    bronze が不変・追記のみである以上、**「続き」と「測り直し」は別の意図**
    だからである（黙って続きにすると、測り直したいときに測り直せない）。
    """
    measure_cfg = load_measure_config()
    rate = measure_cfg.get("rate", {})
    # **投げる間隔（qps）とは別のつまみ。** 応答待ちだけを重ねる。
    # 引数で渡せるのは検査のため（直列と並行で結果が同じことを確かめる）
    concurrency = max(
        int(rate.get("concurrency", DEFAULT_CONCURRENCY) if concurrency is None else concurrency),
        1,
    )
    extras = measure_cfg.get("extras", {})
    dkim_cfg = measure_cfg.get("dkim", {})

    out_dir = phase_dir(run_id, PHASE)
    domains_path = phase_output(run_id, INPUT_PHASE, INPUT_FILENAME)

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        config_hash=config_hash(config_path("configs/measure.yaml")),
        params={
            "method": method,
            "limit": limit,
            "dry_run": dry_run,
            "tier": tier,
            "resume": resume,
        },
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

        # **時間切れで殺された実行の続きから測る。**
        # 対象を決め終えてから引くので、飛ばした分も含めて「この run で
        # 測るべき件数」が input に残る（原則4：分母を縮めない）
        planned = len(targets)
        already: set[str] = set()
        resumed_rows = 0
        if resume:
            already, resumed_rows = measured_domains(bronze_dir(run_id), method)
            if already:
                targets = [t for t in targets if t["domain"] not in already]

        manifest.counts.input = planned

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
        # **「今回測った」と「前回分を合わせた」を分けて残す**（原則4）。
        # 一つの数字にすると、再開した月だけ件数の意味が変わる
        manifest.set_breakdown(
            resume={
                "enabled": resume,
                "planned": planned,
                "already_measured": len(already),
                "to_measure": len(targets),
                "rows_carried_over": resumed_rows,
            }
        )
        if resume and already:
            manifest.add_warning(
                "RESUMED_FROM_EXISTING_BRONZE",
                count=len(already),
                sample=sorted(already)[:5],
                message=(
                    f"同じ run の bronze に {len(already)} ドメインぶんが既にあったため"
                    "測り直していない。**「観測していない」のではなく"
                    "「前回の実行で観測済み」である**"
                ),
            )
        if resume and not already:
            manifest.add_warning(
                "RESUME_FOUND_NOTHING",
                message=(
                    "--resume を指定したが、続きから測れる bronze が無かった。"
                    "最初から測っている（bronze が消えている可能性がある）"
                ),
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
        # **件数はドメインで数える。** クエリ数と混ぜると、工程レポートの
        # 「工程間の件数」表が p4→p5 で 2,773（クエリ）と 2,790（レコード）を
        # 並べることになり、**どこで何件落ちたかという表の意図が読めなくなる。**
        # クエリ数は breakdown.queries_total にある
        fully_observed = 0
        partially_observed = 0
        none_observed = 0

        # **30,000ドメインで3時間を見込む工程である。** 進捗を出さないと、
        # 実行中は何割まで進んだかも残り時間も分からない
        tracker = Progress(len(targets), f"P4 DNS計測({method})")

        def measure_one(target: dict) -> _Measured:
            """1ドメインぶんを引く。**worker スレッドから呼ばれる。**

            ここでは引くだけで、書き出し・件数・進捗には触らない。
            `BronzeWriter` も `RunManifest` も `Progress` もスレッド安全に
            作っていないので、**触らせない**のが一番確実である。
            """
            domain = target["domain"]
            out: list[tuple[object, object]] = []

            # まず MX と SPF を引いて L2 のセレクタ推定に使う。
            # DANE の TLSA も MX ホストが分かってからでないと組めない。
            # **この2段は1ドメインの中では直列である**ので、並行にするのは
            # ドメイン単位にしてある（クエリ単位にすると段が崩れる）
            pre = [
                q
                for q in restrict_to_configured(
                    build_plan(domain, MeasureTier.C, selectors=[]),
                    target["tier"],
                    measure_cfg,
                )
                if q.purpose in (QueryPurpose.MX, QueryPurpose.SPF)
            ]
            mx_values: list[str] = []
            spf_inc: list[str] = []
            for query in pre:
                answer = backend.query(query)
                out.append((query, answer))
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

            # **設定に挙がっていない種類は投げない。** ここを通さないと
            # `tiers.<階層>.queries` は書いてあるだけの項目になる（原則7）
            plan = restrict_to_configured(plan, target["tier"], measure_cfg)

            control_responded = False
            dkim_hit = False
            for query in plan:
                answer = backend.query(query)
                out.append((query, answer))
                if query.purpose == QueryPurpose.DKIM_CONTROL and answer.record_present:
                    control_responded = True
                if query.purpose == QueryPurpose.DKIM and answer.record_present:
                    dkim_hit = True

            return _Measured(domain, out, control_responded, dkim_hit)

        with BronzeWriter(bronze_dir(run_id), method) as writer:
            for result in _in_input_order(measure_one, targets, concurrency):
                queries_before = queries_written
                for query, answer in result.answers:
                    raw = to_raw_response(
                        query,
                        answer,
                        run_id=run_id,
                        domain=result.domain,
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

                # **観測できた本数ではなく、ドメインの状態で分ける。**
                # 一部だけ引けたドメインを「成功」とだけ数えると、半分しか
                # 見ていないことが数字から消える（原則5）
                seen = [a.observed for _, a in result.answers]
                if seen and all(seen):
                    fully_observed += 1
                elif any(seen):
                    partially_observed += 1
                else:
                    none_observed += 1
                    # 1本も引けなかった理由を代表の rcode で残す。
                    # **クエリ単位で数えない** ── 1ドメインの失敗が
                    # セレクタの本数だけ膨らんで見える
                    manifest.add_failure(result.answers[0][1].rcode if seen else "no_queries")

                if result.control_responded:
                    # 実在しないセレクタに応答した。何にでも答えるDNSなので
                    # DKIM の検出結果は信用できない（偽陽性ガード）
                    wildcard_domains.append(result.domain)
                if result.dkim_hit:
                    dkim_detected_domains += 1

                # **1ドメインぶん書き終えた。ここまでは読み出せる。**
                # 途中で殺されても、閉じたフレームの分は再開の根拠になる
                writer.checkpoint()

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
                    "（bronze は不変・追記のみ）。"
                    + (
                        "--resume なので、既に測り終えたドメインは測り直していない"
                        if resume
                        else "同一ドメインの観測が重複している可能性がある。"
                        "続きから測るなら --resume、やり直すなら別の run_id を使うこと"
                    )
                ),
            )

        for part in parts:
            manifest.add_output(
                f"bronze/{part['path']}",
                records=int(part["records"]),
                bytes_=int(part["bytes"]),
            )

        # **ドメイン単位に揃える。** input も success も failed も同じ単位で、
        # 足し合わせると input に戻る（原則4）
        manifest.counts.success = fully_observed + partially_observed
        manifest.counts.skipped = len(already)
        manifest.set_breakdown(
            domains={
                "planned": planned,
                "measured_this_run": len(targets),
                "fully_observed": fully_observed,
                # **一部だけ引けた**。成功に含めるが、半分しか見ていないことは残す
                "partially_observed": partially_observed,
                "none_observed": none_observed,
                "carried_over": len(already),
            },
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
