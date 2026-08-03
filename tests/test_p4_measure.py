"""P4 DNS計測。テストは一切ネットワークに出ない（バックエンドを注入する）。"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailauth.contracts import DOMAIN_ARROW_SCHEMA, MeasureTier, QueryPurpose
from mailauth.io import write_parquet
from mailauth.p4_measure import MissingInputError, UnknownBackendError
from mailauth.p4_measure import run as run_p4
from mailauth.p4_measure.bronze import BronzeWriter, iter_bronze_files, read_bronze
from mailauth.p4_measure.plan import (
    build_dane_queries,
    build_plan,
    control_label,
    estimate_queries,
)
from mailauth.p4_measure.runner import make_backend
from mailauth.p4_measure.selectors import SelectorDictionary
from mailauth.paths import bronze_dir, phase_output
from mailauth.resolver import StaticResolver, make_answer

RUN = "2026-08"

M365_ANSWERS = {
    ("send.example.jp", "MX"): make_answer(
        "send.example.jp", "MX", ["send-example-jp.mail.protection.outlook.com."]
    ),
    ("send.example.jp", "TXT"): make_answer(
        "send.example.jp", "TXT", ["v=spf1 include:spf.protection.outlook.com -all"]
    ),
    ("_dmarc.send.example.jp", "TXT"): make_answer(
        "_dmarc.send.example.jp", "TXT", ["v=DMARC1; p=reject; rua=mailto:a@send.example.jp"]
    ),
    ("selector1._domainkey.send.example.jp", "TXT"): make_answer(
        "selector1._domainkey.send.example.jp", "TXT", ["v=DKIM1; k=rsa; p=MIIBIjAN"]
    ),
    ("_mta-sts.send.example.jp", "TXT"): make_answer(
        "_mta-sts.send.example.jp", "TXT", ["v=STSv1; id=20260801"]
    ),
}


class FakeBackend:
    """注入用のバックエンド。実ネットワークに出ない。"""

    name = "fake"
    version = "test"
    resolver_label = "fake->static"

    def __init__(self, answers: dict | None = None) -> None:
        self._resolver = StaticResolver(answers or M365_ANSWERS)
        self.stats = {"queries": 0, "cache_hits": 0, "tcp_failed": 0}
        self.seen: list[tuple[str, str]] = []

    def query(self, query):
        self.stats["queries"] += 1
        self.seen.append((query.name, query.purpose))
        return self._resolver.query(query.name, query.rtype)


def write_domains(rows: list[dict]) -> None:
    """P3 の出力を直接作る。P4 の入力は domains.parquet だけ（原則3）。"""
    base = {
        "run_id": RUN,
        "domain_role": "primary",
        "confidence": "confirmed",
        "is_measured": True,
        "measure_tier": MeasureTier.A,
        "evidence_count": 5,
    }
    write_parquet(
        [{**base, **r} for r in rows],
        phase_output(RUN, "p3_domains", "domains.parquet"),
        DOMAIN_ARROW_SCHEMA,
    )


# ===========================================================================
# クエリ計画
# ===========================================================================


def test_tier_c_skips_dkim_selectors():
    """送信していないドメインに50セレクタは投げない。作法が悪く検出もされない。"""
    plan = build_plan("a.jp", MeasureTier.C, selectors=["s1", "s2"])
    purposes = {q.purpose for q in plan}
    assert QueryPurpose.DKIM not in purposes
    assert QueryPurpose.DKIM_CONTROL not in purposes
    assert QueryPurpose.MTA_STS not in purposes
    # パーク分類に必要なのは MX / SPF / DMARC / Null MX だけ
    assert QueryPurpose.MX in purposes
    assert QueryPurpose.SPF in purposes
    assert QueryPurpose.DMARC in purposes
    assert QueryPurpose.DKIM_WILDCARD in purposes  # 失効鍵の検出は階層Cでも行う
    assert len(plan) == 5


def test_tier_a_is_the_full_measurement():
    plan = build_plan("a.jp", MeasureTier.A, selectors=[f"s{i}" for i in range(50)])
    purposes = [q.purpose for q in plan]
    assert purposes.count(QueryPurpose.DKIM) == 50
    assert purposes.count(QueryPurpose.DKIM_CONTROL) == 1
    for expected in (
        QueryPurpose.MTA_STS,
        QueryPurpose.TLS_RPT,
        QueryPurpose.BIMI,
        QueryPurpose.DMARC_SUBDOMAIN,
    ):
        assert expected in purposes


def test_control_label_is_deterministic_and_domain_specific():
    """対照クエリは決まった値。原則6（同じ入力なら同じ出力）のため。"""
    assert control_label("a.jp") == control_label("a.jp")
    assert control_label("a.jp") != control_label("b.jp")


def test_dmarc_subdomain_query_targets_a_nonexistent_label():
    """np=（存在しないサブドメイン用ポリシー）の実効確認。"""
    plan = build_plan("a.jp", MeasureTier.C, selectors=[])
    sub = next(q for q in plan if q.purpose == QueryPurpose.DMARC_SUBDOMAIN)
    assert sub.name.startswith("_dmarc.")
    assert sub.name.endswith(".a.jp")
    assert sub.name != "_dmarc.a.jp"


def test_dane_queries_are_built_from_mx_hosts():
    """TLSA は MX ホストが分かってからでないと組めない。"""
    queries = build_dane_queries(["mx1.a.jp", "mx2.a.jp"])
    assert [q.name for q in queries] == ["_25._tcp.mx1.a.jp", "_25._tcp.mx2.a.jp"]
    assert all(q.rtype == "TLSA" for q in queries)
    assert len(build_dane_queries([f"mx{i}.a.jp" for i in range(20)])) == 9  # MX二段の上限


def test_query_estimate_matches_the_design_scale():
    """DESIGN.md の試算（階層A 約59 / 階層C 約5）に収まること。"""
    est = estimate_queries({MeasureTier.A: 8000, MeasureTier.C: 22000}, selector_count=50)
    assert est["per_tier_c"] == 5
    assert 55 <= est["per_tier_a"] <= 62
    # 6時間上限に触れない規模であること
    assert est["total_estimate"] < 700_000


# ===========================================================================
# DKIM セレクタ辞書
# ===========================================================================


def test_l1_is_always_included():
    d = SelectorDictionary.load()
    selectors = d.selectors_for([], [])
    assert 40 <= len(selectors) <= 60
    assert "selector1" in selectors


def test_l2_prioritizes_the_detected_provider():
    """MX が *.outlook.com なら selector1 / selector2 を最優先にする。"""
    d = SelectorDictionary.load()
    selectors = d.selectors_for(["x.mail.protection.outlook.com"], [])
    assert selectors[:2] == ["selector1", "selector2"]
    assert d.provider_hits["microsoft365"] == 1


def test_l2_matches_on_spf_include_too():
    d = SelectorDictionary.load()
    d.selectors_for([], ["_spf.google.com"])
    assert "google_workspace" in d.provider_hits


def test_l2_matches_sakura_style_mechanism():
    """さくらは専用 include を持たず a: で表現する。"""
    d = SelectorDictionary.load()
    d.selectors_for([], ["a:www1234.sakura.ne.jp"])
    assert "sakura" in d.provider_hits


def test_l3_stays_disabled():
    """Tatang 辞書は GPL-3.0。法務確認が済むまで無効のまま。"""
    d = SelectorDictionary.load()
    assert d.l3_enabled is False
    assert d.state()["l3_status"] == "planned"


def test_gateway_signing_domains_are_listed():
    """IIJ の dxg.dox.jp はアライメント不可。実基盤と誤認しないため除外リストを持つ。"""
    assert "dxg.dox.jp" in SelectorDictionary.load().gateway_signing_domains()


# ===========================================================================
# bronze（原則1 ── 生データは不変）
# ===========================================================================


def test_bronze_roundtrip(tmp_path):
    from mailauth.contracts import DnsAnswer as ContractAnswer
    from mailauth.contracts import RawResponse

    response = RawResponse(
        run_id=RUN,
        ts=pd.Timestamp("2026-08-01T02:00:00Z").to_pydatetime(),
        domain="a.jp",
        query_name="a.jp",
        query_type="TXT",
        purpose=QueryPurpose.SPF,
        method="fake",
        resolver="static",
        protocol="udp",
        rcode="NOERROR",
        observed=True,
        record_present=True,
        answers=[ContractAnswer(type="TXT", data=["v=spf1 ", "-all"])],
    )
    with BronzeWriter(tmp_path, "fake") as writer:
        writer.write(response)
    files = iter_bronze_files(tmp_path)
    assert len(files) == 1
    rows = read_bronze(files[0])
    assert len(rows) == 1
    # 分割TXTは連結せず配列のまま保存する。連結は P5 の責務
    assert rows[0]["answers"][0]["data"] == ["v=spf1 ", "-all"]


def test_bronze_splits_into_parts(tmp_path):
    from mailauth.contracts import RawResponse

    def make(i):
        return RawResponse(
            run_id=RUN,
            ts=pd.Timestamp("2026-08-01T02:00:00Z").to_pydatetime(),
            domain=f"d{i}.jp",
            query_name=f"d{i}.jp",
            query_type="MX",
            purpose=QueryPurpose.MX,
            method="fake",
            resolver="static",
            protocol="udp",
            rcode="NODATA",
            observed=True,
            record_present=False,
        )

    with BronzeWriter(tmp_path, "fake", part_size=3) as writer:
        for i in range(7):
            writer.write(make(i))
    assert len(iter_bronze_files(tmp_path)) == 3
    assert sum(len(read_bronze(f)) for f in iter_bronze_files(tmp_path)) == 7


def test_bronze_appends_without_touching_existing_parts(tmp_path):
    """bronze は不変・追記のみ。再実行で既存パートを壊さない（原則1 + 原則6）。"""
    from mailauth.contracts import RawResponse

    def make(domain):
        return RawResponse(
            run_id=RUN,
            ts=pd.Timestamp("2026-08-01T02:00:00Z").to_pydatetime(),
            domain=domain,
            query_name=domain,
            query_type="MX",
            purpose=QueryPurpose.MX,
            method="fake",
            resolver="static",
            protocol="udp",
            rcode="NODATA",
            observed=True,
            record_present=False,
        )

    with BronzeWriter(tmp_path, "fake") as w1:
        w1.write(make("first.jp"))
        assert w1.preexisting_parts == []
    first_bytes = (tmp_path / "method=fake" / "part-0000.jsonl.zst").read_bytes()

    with BronzeWriter(tmp_path, "fake") as w2:
        w2.write(make("second.jp"))
        assert w2.preexisting_parts == ["part-0000.jsonl.zst"]

    files = iter_bronze_files(tmp_path)
    assert [f.name for f in files] == ["part-0000.jsonl.zst", "part-0001.jsonl.zst"]
    assert files[0].read_bytes() == first_bytes  # 既存は無改変
    assert read_bronze(files[1])[0]["domain"] == "second.jp"


def test_bronze_partitions_by_method(tmp_path):
    """手法ごとに分ける。同一対象を複数手法で引いて差分を出すため。"""
    from mailauth.contracts import RawResponse

    for method in ("zdns", "dnspython"):
        with BronzeWriter(tmp_path, method) as writer:
            writer.write(
                RawResponse(
                    run_id=RUN,
                    ts=pd.Timestamp("2026-08-01T02:00:00Z").to_pydatetime(),
                    domain="a.jp",
                    query_name="a.jp",
                    query_type="MX",
                    purpose=QueryPurpose.MX,
                    method=method,
                    resolver="x",
                    protocol="udp",
                    rcode="NODATA",
                    observed=True,
                    record_present=False,
                )
            )
    paths = {f.parent.name for f in iter_bronze_files(tmp_path)}
    assert paths == {"method=zdns", "method=dnspython"}


# ===========================================================================
# P4 の通し
# ===========================================================================


def test_p4_requires_p3_output():
    with pytest.raises(MissingInputError, match="p3-domains"):
        run_p4(run_id=RUN, backend=FakeBackend())


def test_p4_measures_and_writes_bronze():
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])
    result = run_p4(run_id=RUN, backend=FakeBackend())

    assert result["status"] == "success"
    assert result["counts"]["input"] == 1  # 対象ドメイン数
    assert result["outputs"], "bronze のパートが manifest に記録されていない"
    assert result["outputs"][0]["records"] > 50  # 階層A のフル計測

    rows = read_bronze(iter_bronze_files(bronze_dir(RUN))[0])
    purposes = {r["purpose"] for r in rows}
    assert {"mx", "spf", "dmarc", "dkim", "dkim_control", "mta_sts", "dane"} <= purposes


def test_p4_l2_reorders_selectors_from_the_observed_mx():
    """MX を先に引いて事業者を推定し、そのセレクタを優先する。"""
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])
    result = run_p4(run_id=RUN, backend=FakeBackend())
    assert result["breakdown"]["l2_provider_hits"] == {"microsoft365": 1}
    assert result["breakdown"]["dkim_detected_domains"] == 1


def test_p4_detects_wildcard_dns():
    """実在しないセレクタに応答したら偽陽性を疑う（対照クエリ）。"""
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "wild.example.jp"}])

    class WildcardBackend(FakeBackend):
        def query(self, query):
            self.stats["queries"] += 1
            # 何を引いても TXT を返す DNS
            if query.rtype == "TXT":
                return make_answer(query.name, "TXT", ["v=DKIM1; p=AAA"])
            return make_answer(query.name, query.rtype, [])

    result = run_p4(run_id=RUN, backend=WildcardBackend())
    assert result["breakdown"]["wildcard_detected"] == 1
    assert "WILDCARD_DNS" in {w["code"] for w in result["warnings"]}


def test_p4_skips_domains_not_marked_for_measurement():
    """P3 が is_measured=false にしたドメインは測らない。

    「取れなかった」ものを測り直して「無かった」に変えないため。
    """
    write_domains(
        [
            {"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"},
            {
                "domain_id": "d:2",
                "entity_id": "jp:1",
                "domain": "skip.example.jp",
                "is_measured": False,
                "exclusion_reason": "primary_probe_failed",
            },
        ]
    )
    result = run_p4(run_id=RUN, backend=FakeBackend())
    assert result["counts"]["input"] == 1
    domains = {r["domain"] for r in read_bronze(iter_bronze_files(bronze_dir(RUN))[0])}
    assert domains == {"send.example.jp"}


def test_p4_tier_filter():
    write_domains(
        [
            {"domain_id": "d:1", "entity_id": "jp:1", "domain": "a.example.jp"},
            {
                "domain_id": "d:2",
                "entity_id": "jp:1",
                "domain": "b.example.jp",
                "measure_tier": MeasureTier.C,
                "confidence": "parked",
            },
        ]
    )
    result = run_p4(run_id=RUN, backend=FakeBackend(), tier=MeasureTier.C)
    assert result["counts"]["input"] == 1
    assert result["breakdown"]["by_tier"] == {"C": 1}
    # 階層C なので DKIM セレクタは投げていない
    purposes = {r["purpose"] for r in read_bronze(iter_bronze_files(bronze_dir(RUN))[0])}
    assert "dkim" not in purposes


def test_p4_dry_run_estimates_without_measuring():
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])
    backend = FakeBackend()
    result = run_p4(run_id=RUN, backend=backend, dry_run=True)
    assert result["outputs"] == []
    assert backend.stats["queries"] == 0
    assert result["breakdown"]["query_estimate"]["total_estimate"] > 0
    assert not iter_bronze_files(bronze_dir(RUN))


def test_p4_records_failures_by_rcode():
    from mailauth.resolver import DnsAnswer

    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "broken.example.jp"}])

    class FailingBackend(FakeBackend):
        def query(self, query):
            self.stats["queries"] += 1
            return DnsAnswer(
                name=query.name,
                rtype=query.rtype,
                observed=False,
                record_present=None,
                rcode="SERVFAIL",
            )

    result = run_p4(run_id=RUN, backend=FailingBackend())
    assert result["status"] == "partial"
    assert result["failure_breakdown"]["SERVFAIL"] > 0
    # observed=false なので record_present は null のまま保存される（原則5）
    rows = read_bronze(iter_bronze_files(bronze_dir(RUN))[0])
    assert all(r["observed"] is False and r["record_present"] is None for r in rows)


def test_p4_reports_tcp_unavailability():
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])

    backend = FakeBackend()
    backend.stats["tcp_failed"] = 3
    result = run_p4(run_id=RUN, backend=backend)
    assert "TCP53_UNAVAILABLE" in {w["code"] for w in result["warnings"]}


def test_unknown_backend_is_rejected():
    """どの手法で測ったかは成果物の意味を変える。暗黙に差し替えない。"""
    with pytest.raises(UnknownBackendError, match="dnspython"):
        make_backend("securitytrails", {})


def test_zdns_backend_fails_loudly_when_missing(monkeypatch):
    from mailauth.p4_measure.backends import zdns_backend

    monkeypatch.setattr(zdns_backend.shutil, "which", lambda _: None)
    with pytest.raises(zdns_backend.ZdnsNotAvailableError, match="zdns"):
        zdns_backend.ZdnsBackend()


def test_p4_shuffles_domain_order():
    """同一権威への連続クエリを避ける作法。順序は seed で再現可能。"""
    rows = [
        {"domain_id": f"d:{i}", "entity_id": "jp:1", "domain": f"d{i}.example.jp"}
        for i in range(12)
    ]
    write_domains(rows)
    b1 = FakeBackend({})
    run_p4(run_id=RUN, backend=b1)
    measured_order = [name for name, purpose in b1.seen if purpose == QueryPurpose.MX]
    assert measured_order != [r["domain"] for r in rows]  # 入力順ではない
    assert sorted(measured_order) == sorted(r["domain"] for r in rows)  # 全件測っている


def test_p4_dane_only_when_mx_exists():
    """MX が無ければ TLSA は組めない。無駄なクエリを投げない。"""
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "nomx.example.jp"}])
    backend = FakeBackend({})  # 何も無い
    run_p4(run_id=RUN, backend=backend)
    assert not any(purpose == QueryPurpose.DANE for _, purpose in backend.seen)


def test_p4_writes_dnssec_flags():
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])
    run_p4(run_id=RUN, backend=FakeBackend())
    rows = read_bronze(iter_bronze_files(bronze_dir(RUN))[0])
    assert all("dnssec" in r for r in rows)


def test_p3_output_schema_is_the_only_input_contract():
    """P4 は P3 の出力ファイルだけを見る（原則3）。"""
    write_domains([{"domain_id": "d:1", "entity_id": "jp:1", "domain": "send.example.jp"}])
    frame = pq.read_table(phase_output(RUN, "p3_domains", "domains.parquet"))
    assert isinstance(frame, pa.Table)
    assert {"domain", "is_measured", "measure_tier"} <= set(frame.schema.names)


def test_raw_data_preserves_whitespace_in_split_txt():
    """原則1。255バイト境界で分割された TXT の空白を削ると意味が変わる。

        ["v=spf1 include:_spf.example.com ", "-all"]  → 連結して有効な SPF
        ["v=spf1 include:_spf.example.com",  "-all"]  → "…com-all" で SPF にならない
    """
    from mailauth.contracts import DnsAnswer as ContractAnswer
    from mailauth.records import is_spf_record, join_txt_strings

    chunks = ["v=spf1 include:_spf.example.com ", "-all"]
    answer = ContractAnswer(type="TXT", data=chunks)
    assert answer.data == chunks
    joined = join_txt_strings(answer.data)
    assert joined == "v=spf1 include:_spf.example.com -all"
    assert is_spf_record(joined)


def test_bronze_roundtrip_preserves_whitespace(tmp_path):
    from mailauth.contracts import DnsAnswer as ContractAnswer
    from mailauth.contracts import RawResponse
    from mailauth.records import join_txt_strings

    response = RawResponse(
        run_id=RUN,
        ts=pd.Timestamp("2026-08-01T02:00:00Z").to_pydatetime(),
        domain="a.jp",
        query_name="a.jp",
        query_type="TXT",
        purpose=QueryPurpose.SPF,
        method="fake",
        resolver="static",
        protocol="udp",
        rcode="NOERROR",
        observed=True,
        record_present=True,
        answers=[ContractAnswer(type="TXT", data=["v=spf1 ip4:198.51.100.1 ", "-all"])],
    )
    with BronzeWriter(tmp_path, "fake") as writer:
        writer.write(response)
    rows = read_bronze(iter_bronze_files(tmp_path)[0])
    chunks = rows[0]["answers"][0]["data"]
    assert chunks == ["v=spf1 ip4:198.51.100.1 ", "-all"]
    assert join_txt_strings(chunks) == "v=spf1 ip4:198.51.100.1 -all"
