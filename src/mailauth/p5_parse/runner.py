"""P5 パースの本体。

bronze の生レスポンスを構造化し、仕様に照らして解釈する。
**このフェーズは何度でも作り直せる。** パーサにバグが見つかったら
bronze から再実行する（原則1 の実質的な意味）。

bronze には触らない。読むだけ。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections import defaultdict
from typing import Any

from ..contracts import (
    FACT_ARROW_SCHEMA,
    FACT_SORT_KEYS,
    DkimStatus,
    Fact,
    QueryPurpose,
)
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest
from ..normalize import etld_plus_one
from ..ordered import in_input_order
from ..p4_measure.bronze import iter_bronze_files, read_bronze
from ..paths import bronze_dir, month_date, phase_dir, phase_output
from ..records import find_spf_records, is_null_mx, join_txt_strings
from ..records import mx_hosts as extract_mx_hosts
from ..resolver import Resolver
from . import dkim as dkim_mod
from . import dmarc as dmarc_mod
from . import extras as extras_mod
from . import spf as spf_mod
from .orgdomain import resolve as resolve_org_domain

PHASE = "p5_parse"
OUTPUT_FILENAME = "facts.parquet"
PARSER_VERSION = "1.0.0"

#: 返していないドメインを何件まで抱えるか（`ordered.in_input_order`）。
#: 同時本数より十分大きくしないと、先頭の1件が遅いたびに手が止まる
PARSE_WINDOW = 256


class MissingInputError(RuntimeError):
    pass


def fact_id(domain_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{domain_id}|{run_id}".encode()).hexdigest()
    return f"f:{digest[:16]}"


def _txt_values(record: dict) -> list[str]:
    """bronze の answers から TXT を取り出して連結する。

    **連結はここで行う。** bronze は character-string の配列のまま
    保存されているので、順序どおりに連結して単一文字列として解釈する
    （RFC 1035 §3.3、RFC 9989 §4.7 も MUST で規定）。
    """
    out: list[str] = []
    for answer in record.get("answers") or []:
        data = answer.get("data")
        if isinstance(data, list):
            out.append(join_txt_strings(data))
        elif data is not None:
            out.append(str(data))
    return out


def _raw_values(record: dict) -> list[str]:
    out: list[str] = []
    for answer in record.get("answers") or []:
        data = answer.get("data")
        if isinstance(data, list):
            out.append("".join(data))
        elif data is not None:
            out.append(str(data))
    return out


def group_bronze_by_domain(records: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """bronze を domain -> purpose -> [レコード] に畳む。"""
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        grouped[str(record.get("domain"))][str(record.get("purpose"))].append(record)
    return grouped


def parse_domain(
    domain: str,
    by_purpose: dict[str, list[dict]],
    *,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """1ドメインぶんの bronze を解釈して fact のフィールドを返す。"""
    out: dict[str, Any] = {}

    # -- 観測の可否（原則5） ---------------------------------------------
    all_records = [r for records in by_purpose.values() for r in records]
    observed_any = any(r.get("observed") for r in all_records)
    out["observed"] = observed_any
    # レコードの有無は purpose ごとに違うので、代表値は「何か1つでも
    # レコードが存在したか」。個別の有無は各フィールドが持つ
    out["record_present"] = (
        any(r.get("record_present") for r in all_records) if observed_any else None
    )

    # -- MX ----------------------------------------------------------------
    mx_records = by_purpose.get(QueryPurpose.MX, [])
    mx_values = [v for r in mx_records if r.get("observed") for v in _raw_values(r)]
    out["raw_mx"] = json.dumps(mx_values, ensure_ascii=False) if mx_values else None
    null_mx = is_null_mx(mx_values)
    hosts = extract_mx_hosts(mx_values)
    out["mx_present"] = bool(hosts) and not null_mx
    out["mx_hosts"] = hosts
    # Null MX は「MX が無い」とは別の事実。パーク分類で両者を分ける（P6）
    out["mx_null"] = null_mx if mx_records else None

    # -- SPF ---------------------------------------------------------------
    spf_txts = [v for r in by_purpose.get(QueryPurpose.SPF, []) if r.get("observed")
                for v in _txt_values(r)]
    spf = spf_mod.parse(spf_txts)
    out.update(
        raw_spf=spf.raw,
        spf_present=spf.present,
        spf_valid=spf.valid,
        spf_error=spf.error,
        spf_all_qualifier=spf.all_qualifier,
        spf_lookup_count=spf.lookup_count,
        spf_void_count=spf.void_count,
        spf_exceeds_limit=spf.exceeds_limit,
        spf_includes=spf.includes,
        spf_mechanisms=spf.mechanisms,
        spf_is_flattened=spf.is_flattened,
        spf_is_dynamic=spf.is_dynamic,
    )

    # apex TXT のうち SPF 以外。所有権確認 TXT の照合に使う（P6）。
    # SPF は専用フィールドがあるので重複させない
    spf_set = set(find_spf_records(spf_txts))
    out["verification_txt"] = sorted(
        {t for t in spf_txts if t and t not in spf_set}
    )

    # -- DMARC -------------------------------------------------------------
    dmarc_txts = [v for r in by_purpose.get(QueryPurpose.DMARC, []) if r.get("observed")
                  for v in _txt_values(r)]
    dmarc = dmarc_mod.parse(dmarc_txts)
    out.update(
        raw_dmarc=dmarc.raw,
        dmarc_present=dmarc.present,
        dmarc_p=dmarc.p,
        dmarc_sp=dmarc.sp,
        dmarc_np=dmarc.np,
        dmarc_pct=dmarc.pct,
        dmarc_t=dmarc.t,
        dmarc_psd=dmarc.psd,
        dmarc_adkim=dmarc.adkim,
        dmarc_aspf=dmarc.aspf,
        dmarc_rua=dmarc.rua,
        dmarc_ruf=dmarc.ruf,
        dmarc_has_duplicate_tag=dmarc.has_duplicate_tag,
        dmarc_multiple_records=dmarc.multiple_records,
        effective_7489=dmarc.effective_7489,
        effective_9989=dmarc.effective_9989,
        sp_effective_7489=dmarc.sp_effective_7489,
        sp_effective_9989=dmarc.sp_effective_9989,
        sp_weaker=dmarc.sp_weaker,
        policy_label=dmarc.policy_label,
        blind_enforcement=dmarc.blind_enforcement,
        spec_version=dmarc.spec_version,
    )

    # rua の外部宛先。個人情報を集めないためドメインだけを見る
    external = [d for d in dmarc.rua if dmarc_mod.report_domain_is_external(domain, d)]
    out["rua_external"] = bool(external)
    out["rua_authorized"] = None
    out["rua_domain_unregistered"] = None
    if external and resolver is not None:
        authorized = True
        # **「未登録」は強い主張である。** 断定できたときだけ True にする。
        # 三値で持つ: True=断定 / False=登録されている / None=確認できなかった
        unregistered: bool | None = False
        for ext in external:
            name = dmarc_mod.authorization_record_name(domain, ext)
            answer = resolver.query(name, "TXT")
            if answer.observed and not answer.record_present:
                authorized = False

            # 宛先ドメインが登録されているか。Hureau et al.（PAM 2024）は
            # 未登録ドメイン宛の rua が 9,121件あり、第三者が登録すれば
            # レポートが漏洩しうると指摘している。
            #
            # **見るのは登録可能ドメイン（eTLD+1）であって、rua の
            # ホスト名そのものではない。** rua の宛先は `rua.` `dmarc.`
            # のようなサブドメインが普通で、サブドメインに NS が無いのは
            # ゾーンを切っていないだけである。実測（2026-08）で
            # rx.rakuten.co.jp / ml.tepco.co.jp が「未登録」と判定されていた。
            # 楽天や東電のレポートが第三者に奪われうる、という事実に反する
            # 主張を公開しかけていた。
            #
            # **NODATA と NXDOMAIN も区別する。** NODATA は「その名前は
            # 在るが NS が無い」であって未登録ではない。第三者が登録できる
            # のは名前自体が存在しない（NXDOMAIN）場合である
            registrable = etld_plus_one(ext) or ext
            ns = resolver.query(registrable, "NS")
            if not ns.observed:
                # 取れなかった。**「未登録」とは言えない**（原則5）
                if unregistered is False:
                    unregistered = None
            elif ns.rcode == "NXDOMAIN":
                unregistered = True
        out["rua_authorized"] = authorized
        out["rua_domain_unregistered"] = unregistered

    # -- MX の宛先が実在するか ----------------------------------------------
    out.update(_mx_reachability(hosts if out["mx_present"] else [], resolver))

    # -- Organizational Domain の二重解決 ----------------------------------
    org = resolve_org_domain(domain, resolver)
    out.update(
        org_domain_psl=org.psl,
        org_domain_treewalk=org.treewalk,
        org_domain_divergence=org.divergence,
    )

    # -- DKIM（三値表現） --------------------------------------------------
    found: dict[str, str] = {}
    cnames: dict[str, str] = {}
    selectors_tried = 0
    for record in by_purpose.get(QueryPurpose.DKIM, []):
        selectors_tried += 1
        selector = str(record.get("query_name", "")).split("._domainkey.")[0]
        # CNAME は鍵が読めたかに関わらず拾う。委譲先そのものが証拠になる
        for target in record.get("cname_chain") or []:
            if target:
                cnames[selector] = str(target)
        if record.get("observed") and record.get("record_present"):
            values = _txt_values(record)
            if values:
                found[selector] = values[0]

    control = by_purpose.get(QueryPurpose.DKIM_CONTROL, [])
    control_responded = any(r.get("record_present") for r in control)

    wildcard_txt = None
    for record in by_purpose.get(QueryPurpose.DKIM_WILDCARD, []):
        if record.get("observed") and record.get("record_present"):
            values = _txt_values(record)
            if values:
                wildcard_txt = values[0]

    dkim = dkim_mod.build_result(
        found=found,
        selectors_tried=selectors_tried,
        control_responded=control_responded,
        wildcard_record=wildcard_txt,
        cnames=cnames,
        applicable=selectors_tried > 0,
    )
    out.update(
        dkim_status=dkim.status,
        dkim_selectors=dkim.selectors,
        dkim_key_bits=dkim.key_bits,
        dkim_testing_flag=dkim.testing_flag,
        dkim_revoked=dkim.revoked,
        dkim_wildcard_suspect=dkim.wildcard_suspect,
        dkim_cname_targets=dkim.cname_targets,
        dkim_wildcard_revoked=dkim.wildcard_revoked_key,
    )

    # -- 周辺プロトコル ----------------------------------------------------
    def txts(purpose: str) -> list[str]:
        return [
            v
            for r in by_purpose.get(purpose, [])
            if r.get("observed")
            for v in _txt_values(r)
        ]

    mta_sts = extras_mod.parse_mta_sts(txts(QueryPurpose.MTA_STS))
    tls_rpt = extras_mod.parse_tls_rpt(txts(QueryPurpose.TLS_RPT))
    bimi = extras_mod.parse_bimi(txts(QueryPurpose.BIMI))

    # DNSSEC は他のクエリに相乗りしている。AD フラグが立った観測があるか
    dnssec_signed = any(
        (r.get("dnssec") or {}).get("ad") for r in all_records if r.get("observed")
    )
    tlsa = [v for r in by_purpose.get(QueryPurpose.DANE, []) if r.get("observed")
            for v in _raw_values(r)]
    dane = extras_mod.parse_dane(tlsa, dnssec_signed=dnssec_signed)

    out.update(
        mta_sts_present=mta_sts.present,
        mta_sts_id=mta_sts.id,
        mta_sts_mode=mta_sts.mode,
        tls_rpt_present=tls_rpt.present,
        tls_rpt_rua=tls_rpt.rua,
        bimi_present=bimi.present,
        bimi_has_vmc=bimi.has_vmc,
        dnssec_signed=dnssec_signed,
        dane_present=dane.present,
        dane_orphan=dane.orphan,
    )

    out["_null_mx"] = null_mx
    out["_notes"] = (
        spf.notes + dmarc.notes + org.notes + dkim.notes
        + mta_sts.notes + tls_rpt.notes + bimi.notes + dane.notes
    )
    return out


def _mx_reachability(hosts: list[str], resolver: Resolver | None) -> dict[str, Any]:
    """MX の宛先が実在するか。**メールが届かない／第三者に取られうる**状態を見る。

    2026-11 の国内の未知 MX を調べていて見つけた。終わったサービス
    （Sophos Reflexion の `asp.reflexion.net`、Postini の `*.psmtp.com`）や、
    綴りを誤ったホスト（`alt2.aspmx.l.google.co`）を指したままの MX がある。
    名前が無い（NXDOMAIN）ホストには配送できない（RFC 5321 §5.1）。

    返すもの（どれも**断定できたときだけ**値を立てる。原則5）

      mx_hosts_nxdomain       名前が無いと確定した MX ホスト
      mx_unreachable          どの MX ホストにも宛先が無い（NXDOMAIN か、
                              名前はあるがアドレスが無い）と確定した。
                              1つでもアドレスがあれば False、引けなかった
                              ホストが残れば None
      mx_domain_unregistered  名前の無い MX ホストの登録ドメインが未登録。
                              **第三者が登録すれば、そのドメイン宛のメールを
                              受け取れる。** rua の未登録（Hureau et al.,
                              PAM 2024）と同じ形の主張で、同じ慎重さで扱う
    """
    out: dict[str, Any] = {
        "mx_hosts_nxdomain": [],
        "mx_unreachable": None,
        "mx_domain_unregistered": None,
    }
    if resolver is None or not hosts:
        return out

    nxdomain: list[str] = []
    no_address = 0
    unknown = 0
    reachable = 0
    for host in hosts:
        name = host.strip().rstrip(".").lower()
        a = resolver.query(name, "A")
        if not a.observed:
            unknown += 1
            continue
        if a.record_present:
            reachable += 1
            continue
        if a.rcode == "NXDOMAIN":
            nxdomain.append(name)
            continue
        # 名前はあるが A が無い。IPv6 だけのホストがありうる
        aaaa = resolver.query(name, "AAAA")
        if not aaaa.observed:
            unknown += 1
        elif aaaa.record_present:
            reachable += 1
        else:
            no_address += 1

    out["mx_hosts_nxdomain"] = sorted(nxdomain)
    if reachable:
        out["mx_unreachable"] = False
    elif unknown == 0:
        out["mx_unreachable"] = True

    if nxdomain:
        out["mx_domain_unregistered"] = _any_registrable_unregistered(nxdomain, resolver)
    else:
        out["mx_domain_unregistered"] = False if unknown == 0 else None
    return out


def _any_registrable_unregistered(names: list[str], resolver: Resolver) -> bool | None:
    """名前の無いホストのうち、登録ドメインごと空いているものがあるか。

    **「未登録」は強い主張である。** 次の両方が確かめられたときだけ True。

      1. 登録ドメイン（eTLD+1）の NS が NXDOMAIN（NODATA は「在る」）
      2. その上の接尾辞（TLD など）は実在する。**PSL は知らない TLD も
         接尾辞として扱う**ので、`mail.anp` は `mail.anp` が登録ドメインに
         なる。`.anp` という TLD が無ければ誰も登録できない

    IP アドレスを書いた MX（`160.16.74.120`）は名前ではないので数えない。
    """
    verdict: bool | None = False
    for name in names:
        try:
            ipaddress.ip_address(name)
            continue
        except ValueError:
            pass
        registrable = etld_plus_one(name)
        if not registrable or "." not in registrable:
            continue
        ns = resolver.query(registrable, "NS")
        if not ns.observed:
            verdict = None if verdict is False else verdict
            continue
        if ns.rcode != "NXDOMAIN":
            continue
        suffix = registrable.split(".", 1)[1]
        parent = resolver.query(suffix, "NS")
        if not parent.observed:
            verdict = None if verdict is False else verdict
            continue
        if parent.rcode == "NXDOMAIN":
            continue
        return True
    return verdict


def apply_inherited_policy(
    facts: list[Fact], fact_by_domain: dict[str, Fact]
) -> dict[str, int]:
    """継承したポリシーを埋める。**自分のレコードは上書きしない。**

    `effective_7489` は「自分のレコードから読んだ値」のままにして、
    実際に効いている強度は `applied_7489` に別に持つ。どちらを見て
    いるかが後から分かるようにするため（原則5 と同じ考え方）。

    直近の上位で止める ── `a.b.example.co.jp` の途中の
    `b.example.co.jp` を測っていない場合、そこに自前の `_dmarc` が
    あっても見えない。**その限界は `applied_source` に残らない**ので、
    サイトの限界に書いてある。
    """
    counts = {"dmarc_inherited": 0, "dmarc_inherit_source_missing": 0}
    # **名前は1回だけ引き直す。** fact ごとに探すと O(n^2) になり、
    # 30,000 ドメインでここだけ数分かかる（BACKLOG 7 の規模の話）
    name_by_id = {f.fact_id: name for name, f in fact_by_domain.items()}
    for fact in facts:
        if fact.dmarc_present:
            fact.applied_7489 = fact.effective_7489
            fact.applied_source = "own"
            continue
        if fact.is_apex:
            # apex に DMARC が無ければ、継承元も無い
            fact.applied_source = "none"
            continue
        parent = etld_plus_one(name_by_id.get(fact.fact_id) or "")
        source = fact_by_domain.get(parent or "")
        if source is None or not source.dmarc_present:
            fact.applied_source = "none"
            counts["dmarc_inherit_source_missing"] += 1
            continue
        fact.applied_7489 = source.sp_effective_7489
        fact.applied_source = "inherited_sp"
        fact.dmarc_inherited_from = parent
        counts["dmarc_inherited"] += 1
    return counts


def run(
    run_id: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    resolver: Resolver | None = None,
    concurrency: int = 1,
) -> dict[str, Any]:
    """P5 を実行し manifest の内容を返す。

    `resolver` は Tree Walk と External Destination Verification に使う。
    無ければその2つをスキップし、manifest に記録する。

    `concurrency` は同時に応答を待つドメイン数（BACKLOG 7）。**結果は入力順に
    受け取る**ので、facts の中身と並びは1本のときと変わらない。投げる間隔は
    resolver が全スレッドで共有して守る。
    """
    out_dir = phase_dir(run_id, PHASE)
    bronze = bronze_dir(run_id)

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        tool_versions={"parser": PARSER_VERSION},
        params={"limit": limit, "dry_run": dry_run, "resolver": resolver is not None},
    ) as manifest:
        files = iter_bronze_files(bronze)
        if not files:
            raise MissingInputError(
                f"{bronze} に bronze がありません。先に p4-measure を実行してください"
            )

        records: list[dict] = []
        for path in files:
            records.extend(read_bronze(path))
        manifest.counts.input = len(records)

        domains_frame = read_parquet(phase_output(run_id, "p3_domains", "domains.parquet"))
        ids: dict[str, tuple[str, str]] = {}
        #: domain -> apex か。**分母は apex で閉じている**（BACKLOG 14）。
        #: P3 が決めたものを引き継ぐ。ここで名前から作り直さない
        apex_by_domain: dict[str, bool] = {}
        #: いまの P3 が計測対象にしているドメイン。**facts はここに閉じる**
        measured_names: set[str] = set()
        if domains_frame is not None:
            for _, row in domains_frame.iterrows():
                name = str(row["domain"])
                ids[name] = (str(row["domain_id"]), str(row["entity_id"]))
                flag = row.get("is_measured")
                if flag is not None and flag == flag and bool(flag):
                    measured_names.add(name)
                value = row.get("is_apex")
                apex_by_domain[name] = True if value is None or value != value else bool(value)

        grouped = group_bronze_by_domain(records)
        domain_names = sorted(grouped)
        # **同じ run の bronze には、前の試行の残りが混ざりうる。** bronze は
        # 追記のみで、P4 は --resume で前の試行の分を持ち越す。そのあいだに
        # P3 を流し直すと（2026-10 の米国計測でリゾルバを直したとき）、前の
        # P3 が測ったが今の P3 は測らないドメインが bronze に残る。facts に
        # 入れると、その企業は古いドメインでも数えられる（原則4：分母は P3 の
        # 計測対象で閉じる）
        if domains_frame is not None:
            outside = [d for d in domain_names if d not in measured_names]
            if outside:
                domain_names = [d for d in domain_names if d in measured_names]
                manifest.add_warning(
                    "BRONZE_OUTSIDE_MEASURED_SET",
                    count=len(outside),
                    sample=outside[:5],
                    message=(
                        "同じ run の bronze に、いまの P3 が計測対象にしていないドメインが"
                        "あった（前の試行の残り）。facts に入れていない"
                    ),
                )
                manifest.set_breakdown(bronze_outside_measured_set=len(outside))
        if limit:
            domain_names = domain_names[:limit]

        if resolver is None:
            manifest.add_warning(
                "NO_RESOLVER",
                message=(
                    "resolver が無いため Tree Walk と External Destination Verification を"
                    "スキップした。org_domain_treewalk と rua_authorized は null になる"
                ),
            )

        facts: list[Fact] = []
        #: domain -> その fact。**継承の解決に使う**（下の第2周）
        fact_by_domain: dict[str, Fact] = {}
        counters: dict[str, int] = defaultdict(int)
        notes_sample: list[str] = []
        month = month_date(run_id)

        def parse_one(domain: str) -> tuple[str, dict | None, Exception | None]:
            try:
                return domain, parse_domain(domain, grouped[domain], resolver=resolver), None
            except Exception as exc:  # noqa: BLE001 - 1件のパース失敗で全体を止めない
                return domain, None, exc

        # **DNS に出るのは Tree Walk と rua の宛先確認だけ**だが、1本ずつ待つと
        # 国内 3,437 ドメインで 11 分かかっていた（2026-11、BACKLOG 7）。
        # resolver が無ければ待つものが無いので並行にしない
        workers = max(1, int(concurrency)) if resolver is not None else 1
        manifest.set_breakdown(concurrency=workers)
        for domain, parsed, exc in in_input_order(
            parse_one, domain_names, concurrency=workers, window=PARSE_WINDOW
        ):
            if exc is not None or parsed is None:
                manifest.add_failure(f"parse_error:{type(exc).__name__}")
                notes_sample.append(f"{domain}: {type(exc).__name__}: {exc}")
                continue

            domain_id, entity_id = ids.get(domain, (f"d:unknown:{domain}", "unknown"))
            dane_orphan = parsed.get("dane_orphan", False)
            parsed.pop("_null_mx", None)
            notes = parsed.pop("_notes", [])

            fact = Fact(
                    fact_id=fact_id(domain_id, run_id),
                    domain_id=domain_id,
                    entity_id=entity_id,
                    run_id=run_id,
                    measured_month=month,
                    is_apex=apex_by_domain.get(domain, True),
                    parser_version=PARSER_VERSION,
                    **parsed,
            )
            facts.append(fact)
            fact_by_domain[domain] = fact

            if parsed.get("spf_error") == spf_mod.SpfError.PERMERROR:
                counters["spf_permerror"] += 1
            if parsed.get("spf_error") == spf_mod.SpfError.MULTIPLE_RECORDS:
                counters["spf_multiple_records"] += 1
            if parsed.get("spf_exceeds_limit"):
                counters["spf_exceeds_10_lookup"] += 1
            if parsed.get("dmarc_multiple_records"):
                counters["dmarc_multiple_records"] += 1
            if parsed.get("dmarc_has_duplicate_tag"):
                counters["dmarc_duplicate_tag"] += 1
            if parsed.get("org_domain_divergence"):
                counters["org_domain_divergence"] += 1
            if parsed.get("dkim_wildcard_suspect"):
                counters["dkim_wildcard_suspect"] += 1
            if parsed.get("rua_authorized") is False:
                counters["rua_unauthorized"] += 1
            if parsed.get("rua_domain_unregistered"):
                counters["rua_domain_unregistered"] += 1
            if parsed.get("mx_unreachable"):
                counters["mx_unreachable"] += 1
            if parsed.get("mx_hosts_nxdomain"):
                counters["mx_has_nxdomain_host"] += 1
            if parsed.get("mx_domain_unregistered"):
                counters["mx_domain_unregistered"] += 1
            if dane_orphan:
                counters["dane_orphan"] += 1
            if parsed.get("effective_7489") != parsed.get("effective_9989"):
                counters["spec_divergence"] += 1
            if parsed.get("dkim_status") == DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS:
                counters["dkim_not_found_in_known_selectors"] += 1
            if notes:
                notes_sample.extend(notes[:2])

        # **自分の `_dmarc` を持たないサブドメインは、上位の `sp=` が効く。**
        #
        # RFC 9989 §4.8。これを繋がないと、apex が `p=reject` の会社の
        # サブドメインが「DMARC 無し」として数えられる ── **実際には
        # 守られているのに未対応側に入る**（BACKLOG 14 の d）。
        #
        # **新しい問い合わせは要らない。** サブドメインを残す条件が
        # 「所有の裏付けがある apex の配下」なので、その apex は同じ実行で
        # 必ず測っている。
        inherited = apply_inherited_policy(facts, fact_by_domain)
        counters.update(inherited)

        manifest.counts.success = len(facts)
        manifest.set_breakdown(
            **dict(counters),
            domains_parsed=len(facts),
            bronze_files=len(files),
            notes_sample=notes_sample[:20],
        )
        if counters.get("org_domain_divergence"):
            manifest.add_warning(
                "ORG_DOMAIN_DIVERGENCE",
                count=counters["org_domain_divergence"],
                message="PSL と Tree Walk で Organizational Domain の判定が割れた",
            )
        if counters.get("dkim_wildcard_suspect"):
            manifest.add_warning(
                "DKIM_WILDCARD_SUSPECT",
                count=counters["dkim_wildcard_suspect"],
                message="対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性",
            )
        if counters.get("rua_domain_unregistered"):
            manifest.add_warning(
                "RUA_DOMAIN_UNREGISTERED",
                count=counters["rua_domain_unregistered"],
                message=(
                    "rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる"
                    "（Hureau et al., PAM 2024）"
                ),
            )

        if counters.get("mx_unreachable"):
            manifest.add_warning(
                "MX_UNREACHABLE",
                count=counters["mx_unreachable"],
                message=(
                    "どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）"
                    "ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや"
                    "綴りの誤りを指したままのものがある"
                ),
            )
        if counters.get("mx_domain_unregistered"):
            manifest.add_warning(
                "MX_DOMAIN_UNREGISTERED",
                count=counters["mx_domain_unregistered"],
                message=(
                    "MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業の"
                    "ドメイン宛のメールを受け取れる**（rua の未登録と同じ形）"
                ),
            )

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            n = write_parquet(
                facts,
                out_dir / OUTPUT_FILENAME,
                FACT_ARROW_SCHEMA,
                sort_keys=FACT_SORT_KEYS,
                metadata={
                    "mailauth.phase": PHASE,
                    "mailauth.run_id": run_id,
                    "mailauth.parser_version": PARSER_VERSION,
                },
            )
            manifest.add_output(OUTPUT_FILENAME, records=n)

        return manifest.to_dict()
