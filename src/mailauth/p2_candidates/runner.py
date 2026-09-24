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
from ..ctlog import DEFAULT_CONCURRENCY, CrtShClient, CtResult, CtSource, DisabledCtSource
from ..exclusions import ExclusionRegistry, require_available
from ..exclusions import load as load_exclusions
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest, config_hash
from ..normalize import etld_plus_one
from ..paths import cache_root, config_path, month_date, phase_dir, phase_output
from ..progress import Progress
from ..records import (
    dmarc_report_domains,
    find_dmarc_records,
    find_spf_records,
    join_txt_strings,
    spf_redirect,
)
from ..resolver import DnsResolver, Resolver, shuffled

#: CT ログを何社ぶんまとめて先回りして取るか。
#:
#: **全件を先に取らない。** 候補総数の上限で途中で打ち切られることがあり、
#: そのとき使わないドメインまで crt.sh に投げたことになる。塊で区切れば
#: 取りすぎは最大1塊ぶんで済む。同時本数（既定4）より十分大きくしないと、
#: 塊の終わりで待ち合わせるたびに並行の利きが落ちる
PREFETCH_CHUNK = 64

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


#: rua 宛先が何社に共用されていたらベンダーと見なすか。
#: **1社では判断できない。** 2社以上が同じ宛先を使っていれば、
#: その両方が所有しているはずがない
SHARED_RUA_MIN_ENTITIES = 2

#: DNS から辿っただけの経路。**所有の裏付けにはならない。**
#: redirect 先も rua 宛先も、他社の基盤を指していることがある
DNS_DERIVED_METHODS = frozenset({DiscoveryMethod.SPF_REDIRECT, DiscoveryMethod.DMARC_RUA})


def _domain_entities(collectors: dict[str, _Collector]) -> dict[str, set[str]]:
    """domain -> それを候補に持つ entity_id の集合。"""
    out: dict[str, set[str]] = {}
    for entity_id, collector in collectors.items():
        for domain in collector.found:
            out.setdefault(domain, set()).add(entity_id)
    return out


class _Collector:
    """1社ぶんの候補を集める。同じドメインが複数経路で出たら経路を足す。

    除外リストに載っているドメインはここで落とす。**候補にならなければ
    以降のどの工程にも現れない**ので、除外を効かせる最も確実な位置である。
    """

    def __init__(
        self,
        entity_id: str,
        limit: int,
        *,
        excluded: ExclusionRegistry | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.limit = limit
        #: domain -> {method: source_detail}
        self.found: dict[str, dict[str, str]] = {}
        self.truncated = 0
        self.excluded = excluded
        #: 除外して落とした件数。**黙って落とさない**（原則4）
        self.skipped_excluded = 0

    def add(self, domain: str | None, method: str, detail: str = "") -> None:
        apex = etld_plus_one(domain) if domain else None
        if not apex:
            return
        if self.excluded is not None and self.excluded.excludes(apex):
            # **「除外した」は「観測できなかった」でも「無かった」でもない。**
            # 測らないと決めたということ。件数は manifest に出る
            if apex not in self.found:
                self.skipped_excluded += 1
                self.excluded.record(apex)
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


def match_report_vendor(domain: str, patterns: list[tuple[str, re.Pattern[str]]]) -> str | None:
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
    unaligned_rua: dict[str, set[str]],
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
                    if etld_plus_one(domain) != etld_plus_one(apex):
                        # **rua は自社ドメイン宛のときだけ候補にする。**
                        #
                        # 「example.co.jp の rua が vendor.jp を指している」が
                        # 示すのは「vendor.jp が example のレポートを受け取る」
                        # ことだけで、**example が vendor.jp を所有している証拠に
                        # はならない。** 候補に入れると他社のドメインを
                        # その企業の送信ドメインとして公開してしまう。
                        #
                        # 実測（2026-08）で securemx.jp が keyence.co.jp の
                        # ドメインとして confidence=likely まで通っていた。
                        # レポート処理サービス自身が MX/SPF/DMARC を持っている
                        # ため、ドメイン単体の実証では見分けが付かない。
                        #
                        # 辞書に無いものはベンダー名が分からないだけなので、
                        # 同定の作業リストとして件数を残す
                        unaligned_rua.setdefault(etld_plus_one(domain) or domain, set()).add(apex)
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

        # 計測対象からの除外。**読めなければ止める。** 外してほしいと言った
        # 相手を測ってしまうのは取り返しがつかない（exclusions.py 参照）
        excluded = load_exclusions()
        require_available(excluded)

        active = entities[entities["status"] != EntityStatus.DELISTED]
        # 企業単位の除外はここで落とす。**候補の起点にしない**
        if excluded.entity_ids:
            before = len(active)
            active = active[~active["entity_id"].astype(str).isin(excluded.entity_ids)]
            dropped = before - len(active)
            if dropped:
                excluded.record("entity", dropped)
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
                    # **月で区切る。** CT ログは追記されていくので、先月の応答を
                    # 今月の観測として使うと候補生成が初月の状態で凍結する
                    month=run_id,
                    qps=float(ct_cfg.get("qps", 0.5)),
                    timeout=float(ct_cfg.get("timeout_sec", 60)),
                    retries=int(ct_cfg.get("retries", 2)),
                    # **投げる間隔（qps）とは別のつまみ。** 応答待ちだけを重ねる
                    concurrency=int(ct_cfg.get("concurrency", DEFAULT_CONCURRENCY)),
                )
                if discovery.get("ct_log")
                else DisabledCtSource()
            )

        manual = (
            load_manual_domains(cfg.get("manual_domains", "")) if discovery.get("manual") else {}
        )

        vendor_patterns = load_report_vendor_patterns()
        vendor_hits: dict[str, int] = {}
        # 辞書に無く、自社ドメインでもない rua 宛先。**候補にはしない。**
        # ベンダー名が分からないだけなので、同定の作業リストとして残す
        unaligned_rua: dict[str, set[str]] = {}

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
        ct_stats = {
            "searched": 0,
            "from_cache": 0,
            # 当月以外のキャッシュを使った件数。**0 でなければ時系列が嘘になる**
            "stale_cache": 0,
            "cache_month": run_id,
            "errors": 0,
            "raw_names": 0,
            "truncated": 0,
        }
        total = 0
        hit_total_limit = False

        # **この工程は数時間かかることがある。** CT ログの取得が支配的で、
        # 1件あたりの所要は相手によって1秒から3分まで開く。進捗を出さないと、
        # 実行中は何割まで進んだかも残り時間も分からない
        # （GitHub Actions の API は実行中のジョブのログを返さない）
        tracker = Progress(len(order), "P2 候補生成")

        # -- CT ログの取得を先回りする ---------------------------------------
        # **1件ずつ取ると、応答を待っている時間がそのまま実時間になる。**
        # 実測（2026-09 の国内計測）で1件9.5秒以上、3,818社で5時間17分を
        # 超えてもまだ終わらなかった。投げる間隔は 0.5 qps のままにして、
        # 応答待ちだけを重ねる（ctlog.py 冒頭参照）。
        #
        # **塊に区切って取る。** 候補総数の上限で途中で打ち切られることが
        # あるので、全件を先に取ると、使わないドメインまで crt.sh に
        # 投げることになる。1塊ぶんの取りすぎで済ませる
        ct_enabled = bool(discovery.get("ct_log"))

        def _seed(i: int) -> str | None:
            value = rows.iloc[i].get("official_domain")
            return None if not value or str(value) == "nan" else str(value)

        ct_ready: dict[str, CtResult] = {}
        ct_tracker: Progress | None = None
        # 既に取りにいった起点ドメイン。**持株会社などで同じ起点が別の塊に
        # 現れる。** 塊ごとの重複排除では取りこぼすので、全体でも覚えておく。
        # 覚えないと進捗が 100% を超えて表示される
        ct_asked: set[str] = set()
        if ct_enabled:
            n_seeds = len({d for d in (_seed(i) for i in order) if d})
            ct_tracker = Progress(n_seeds, "P2 CT取得")

        def _note_ct(_domain: str, result: CtResult) -> None:
            # **呼び出し側のスレッドから順に呼ばれる。** Progress は
            # スレッド安全に作っていないので、worker から触らせない
            if ct_tracker is not None:
                ct_tracker.tick(
                    キャッシュ=1 if result.from_cache else 0,
                    失敗=1 if result.error else 0,
                )

        def _process(idx: int) -> None:
            """1社ぶんを組み立てる。CT の結果は先回りして取ったものを使う。"""
            nonlocal total
            row = rows.iloc[idx]
            cache_before = ct_stats["from_cache"]
            entity_id = str(row["entity_id"])
            collector = _Collector(entity_id, per_entity_limit, excluded=excluded)
            collectors[entity_id] = collector

            official = _seed(idx)

            if discovery.get("official_url") and official:
                collector.add(official, DiscoveryMethod.OFFICIAL_URL, "P1 gBizINFO company_url")

            if discovery.get("manual"):
                for domain, note in manual.get(entity_id, []):
                    collector.add(domain, DiscoveryMethod.MANUAL, note or "manual dictionary")

            if official:
                if ct_enabled:
                    # 先回りで取れていればそれを使う。取れていなければここで取る
                    # （**取りこぼしを黙って「候補なし」にしない**）
                    ct = ct_ready.get(official) or ct_source.search(official)
                    ct_stats["searched"] += 1
                    ct_stats["raw_names"] += ct.raw_names
                    if ct.from_cache:
                        ct_stats["from_cache"] += 1
                        # **当月以外のキャッシュを「今月の観測」として数えない。**
                        # 月で区切っているので通常は起きないが、起きたら見える形にする
                        if ct.cache_month != run_id:
                            ct_stats["stale_cache"] += 1
                    if ct.error:
                        ct_stats["errors"] += 1
                        # **失敗を一語にまとめない。** 時間切れなら待ち方、
                        # HTTP エラーなら頼み方を変えることになる
                        manifest.add_failure(f"ct_log_{ct.error_kind or 'error'}")
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
                    unaligned_rua,
                )

            total += len(collector.found)
            tracker.tick(
                候補=len(collector.found),
                CT取得=1 if (official and ct_enabled) else 0,
                キャッシュ=ct_stats["from_cache"] - cache_before,
            )

        for start in range(0, len(order), PREFETCH_CHUNK):
            chunk = order[start : start + PREFETCH_CHUNK]
            if ct_enabled:
                wanted = [d for d in (_seed(i) for i in chunk) if d and d not in ct_asked]
                ct_asked.update(wanted)
                # **前の塊で取ったものは持ち越す。** 捨てると、同じ起点を持つ
                # 企業が別の塊に現れたときに取り直しになる（キャッシュには
                # 当たるが、進捗の件数が合わなくなる）
                ct_ready.update(ct_source.prefetch(wanted, on_result=_note_ct))
            for idx in chunk:
                _process(idx)
                if total >= total_limit:
                    hit_total_limit = True
                    break
            if hit_total_limit:
                break

        # 打ち切られた場合も最後に1行出す
        if ct_tracker is not None:
            ct_tracker.finish()
        tracker.finish()

        if hit_total_limit:
            manifest.add_warning(
                "CANDIDATE_LIMIT_REACHED",
                message=(
                    f"候補総数が上限 {total_limit} に達したため展開を打ち切った。"
                    "処理していない企業が残っている"
                ),
            )

        # -- rua 由来のみの共用ドメインを落とす ------------------------------
        # **1つのドメインが複数の無関係な企業の rua 宛先になっているなら、
        # その全社が所有しているはずがない。** 第三者のレポート処理サービスである。
        #
        # 辞書（configs/vendors/dmarc_rua_vendors.yaml）でも弾いているが、
        # 実測すると辞書に無いベンダーが出てくる（powerdmarc.com / smtps.jp /
        # teams.ms を確認）。辞書は追いつかないので、**ベンダー名を知らなくても
        # 効く構造的な判定**を併せて持つ。
        #
        # 落とすのは rua だけで見つかったものに限る。official_url や ct_log の
        # 裏付けがあるドメインは、グループ共用の本物なので残す。
        rua_only_shared: dict[str, list[str]] = {}
        for domain, entity_ids in _domain_entities(collectors).items():
            if len(entity_ids) < SHARED_RUA_MIN_ENTITIES:
                continue
            methods = {m for eid in entity_ids for m in collectors[eid].found.get(domain, {})}
            # official_url / ct_log / manual は所有の裏付けなので、
            # それが1つでもあれば本物のグループ共用ドメインとして残す。
            # DNS 由来の経路（rua / redirect）だけで見つかったものは
            # **他社の基盤である可能性が高い**（ESP の redirect 先など）
            if methods and methods <= DNS_DERIVED_METHODS:
                rua_only_shared[domain] = sorted(entity_ids)

        for domain, entity_ids in rua_only_shared.items():
            for entity_id in entity_ids:
                collectors[entity_id].found.pop(domain, None)

        if rua_only_shared:
            manifest.add_warning(
                "SHARED_RUA_DOMAIN_DROPPED",
                count=len(rua_only_shared),
                sample=sorted(rua_only_shared)[:5],
                message=(
                    "複数企業の rua 宛先になっているドメインを候補から落とした。"
                    "**第三者のレポート処理サービスを企業のドメインとして計測しない。** "
                    "ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に、"
                    "本当にグループ共用なら configs/domains/manual_domains.csv に足すこと"
                ),
            )

        # 候補レコードの組み立て
        candidates: list[DomainCandidate] = []
        # discovered_at は実行時刻ではなく run の月initialにする。
        # 壁時計を埋めると同じ入力でも出力のバイト列が変わり、原則6（冪等）が
        # 壊れる。月次計測なので「いつ発見したか」の粒度は月で足りる。
        # 実行時刻そのものは manifest の started_at に残る。
        discovered_at = dt.datetime.combine(month_date(run_id), dt.time(0, 0), tzinfo=dt.UTC)
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

        if unaligned_rua:
            manifest.add_warning(
                "UNALIGNED_RUA_TARGET",
                count=len(unaligned_rua),
                sample=sorted(unaligned_rua)[:5],
                message=(
                    "自社ドメインでない rua 宛先があった。**候補にしていない。** "
                    "第三者のレポート処理サービスであり、その企業の送信ドメイン"
                    "ではない。ベンダー名が分かれば "
                    "configs/vendors/dmarc_rua_vendors.yaml に足すと "
                    "P6 の dmarc_vendor 推定が埋まる"
                ),
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
            # **crt.sh にどれだけ待たされたかを残す。** 2026-09 の実行は
            # P2 に4時間21分かかったが、残っていたのは失敗の件数だけで、
            # 時間切れだったのか重かったのかが分からなかった
            ct_response=(
                ct_source.response_stats()
                if hasattr(ct_source, "response_stats")
                else {"requests": 0, "note": "この CT ソースは応答時間を測らない"}
            ),
            resolver=getattr(resolver, "stats", {}),
            shared_domains=len(shared),
            # rua が第三者サービスを指していた件数。P6 の dmarc_vendor 推定の材料
            dmarc_report_vendors=dict(sorted(vendor_hits.items())),
            # 辞書に無いのに複数社で共用されていた rua 宛先。**同定の作業リスト。**
            # ベンダー名が分かれば dmarc_rua_vendors.yaml に足す
            shared_rua_dropped={d: v for d, v in sorted(rua_only_shared.items())},
            # 自社ドメインでない rua 宛先。**候補にしていない。**
            # ベンダー名が分かれば dmarc_rua_vendors.yaml に足す作業リスト
            unaligned_rua_targets={d: sorted(v) for d, v in sorted(unaligned_rua.items())},
            # **除外は黙って行わない。** 分母から抜いた分を記録する（原則4）
            excluded=excluded.to_dict(),
        )
        dropped_domains = sum(c.skipped_excluded for c in collectors.values())
        if dropped_domains or excluded.hits.get("entity"):
            manifest.add_warning(
                "EXCLUDED_BY_REQUEST",
                count=dropped_domains + excluded.hits.get("entity", 0),
                sample=sorted(d for d in excluded.domains if d)[:5],
                message=(
                    "計測対象からの除外の依頼により候補から落としたものがある。"
                    "**「観測できなかった」ではなく「測らないと決めた」である。** "
                    f"ドメイン {dropped_domains} 件 / 企業 "
                    f"{excluded.hits.get('entity', 0)} 件"
                ),
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
            # **原因が分かっている警告は、原因の方を指す。**
            # 起点となる公式サイトが取れなかった企業は候補ゼロになり、
            # 中央値を押し下げる。症状（中央値が低い）だけを毎月出しても、
            # 読み手は次に何をすればよいか分からない
            share = zero / entities if entities else 0.0
            cause = ""
            if percentiles["p50"] < lo and share >= 0.25:
                cause = (
                    f"。ただし {entities} 社のうち {zero} 社（{share:.1%}）が候補ゼロで、"
                    "その分が中央値を押し下げている。**中央値そのものではなく、"
                    "起点ドメインが取れていないことが原因**"
                )
            manifest.add_warning(
                "ACCEPTANCE_MEDIAN_OUT_OF_RANGE",
                message=(
                    f"1社あたり候補数の中央値 {percentiles['p50']} が想定 {lo}〜{hi} の外"
                    + cause
                ),
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
