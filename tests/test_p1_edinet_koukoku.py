"""有報の公告掲載方法から自社サイトを読む（BACKLOG 43）。

**URL がそのまま会社のものとは限らない**（公告の掲載を外部に任せる会社が
ある）ので、検査の多くは「別の会社のドメインを付けない」ことを見ている。

テストは一切ネットワークに出ない。EDINET の API と、ページでの確かめは
差し替える。
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import yaml

from mailauth.p1_population.edinet_koukoku import (
    EdinetKoukoku,
    document_text,
    koukoku_domains,
    newspaper_notice,
)

TODAY = dt.date(2026, 11, 6)  # 金曜


def _zip(body_html: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRL/PublicDoc/0000000_header.htm", "<html><body>表紙</body></html>")
        z.writestr("XBRL/PublicDoc/0105000_honbun.htm", f"<html><body>{body_html}</body></html>")
        z.writestr("XBRL/AuditDoc/audit.htm", "公告掲載方法 https://www.auditor.example.jp/")
    return buf.getvalue()


def _koukoku(url_part: str) -> str:
    return (
        "<table><tr><td>事業年度</td><td>4月1日から3月31日まで</td></tr>"
        "<tr><td>公告掲載方法</td>"
        f"<td>電子公告により行う。{url_part}</td></tr></table>"
    )


def test_本文の平文を作る_監査報告書は読まない():
    text = document_text(_zip(_koukoku("https://www.example.co.jp/ir/koukoku/")))
    assert "公告掲載方法" in text
    assert "auditor" not in text


def test_公告掲載方法の欄の_URL_を登録ドメインで返す():
    text = document_text(_zip(_koukoku(
        "公告掲載URL https://www.example.co.jp/ir/koukoku/ "
        "（やむを得ない場合は日本経済新聞に掲載） http://ir.example.co.jp/"
    )))
    assert koukoku_domains(text) == ["example.co.jp"]


def test_目次の見出しには_URL_が無いので次の出現を見る():
    text = "目次 第6 提出会社の株式事務の概要 公告掲載方法 …" + " x" * 300 + (
        " 公告掲載方法 電子公告 https://corp.example.com/notice/"
    )
    assert koukoku_domains(text) == ["example.com"]


def test_新聞公告は_URL_が無く新聞と分かる():
    text = document_text(_zip("<td>公告掲載方法</td><td>日本経済新聞に掲載して行う。</td>"))
    assert koukoku_domains(text) == []
    assert newspaper_notice(text)


# -- 本体 ------------------------------------------------------------------------


class FakeApi:
    """日付ごとの書類一覧と、docID ごとの zip を返す。"""

    def __init__(self, lists: dict[str, list[dict]], docs: dict[str, bytes]) -> None:
        self.lists = lists
        self.docs = docs
        self.calls: list[str] = []

    def __call__(self, path: str, params: dict[str, str]) -> bytes:
        self.calls.append(f"{path}?{params.get('date', params.get('type'))}")
        if path == "/documents.json":
            results = self.lists.get(params["date"], [])
            return json.dumps({"metadata": {"status": "200"}, "results": results}).encode()
        doc_id = path.rsplit("/", 1)[-1]
        if doc_id not in self.docs:
            raise RuntimeError("404")
        return self.docs[doc_id]


def _doc(doc_id: str, code: str, doc_type: str = "120") -> dict:
    return {"docID": doc_id, "edinetCode": code, "docTypeCode": doc_type,
            "withdrawalStatus": "0", "secCode": "12340"}


def _confirm_only(ok: dict[str, str]):
    """ok: {ドメイン: 社名}。社名が合えば found、ドメインが無ければ not_on_page。"""
    seen: list[str] = []

    def confirm(domain, name_ja, name_en):
        seen.append(domain)
        if ok.get(domain) == name_ja:
            return domain, "found"
        return None, "not_on_page"

    confirm.seen = seen  # type: ignore[attr-defined]
    return confirm


def test_最新の有報の公告_URL_をページで確かめて付ける():
    api = FakeApi(
        {
            "2026-11-05": [_doc("S2", "E00001")],  # 新しい方
            "2026-06-26": [_doc("S1", "E00001"), _doc("S9", "E00002", "130")],
        },
        {"S2": _zip(_koukoku("https://www.example.co.jp/ir/")),
         "S1": _zip(_koukoku("https://www.old.co.jp/"))},
    )
    reader = EdinetKoukoku(api, _confirm_only({"example.co.jp": "株式会社例"}), today=TODAY)
    got = reader.websites_for([("jp:1", "E00001", "株式会社例", None)], taken=set())
    assert got == {"jp:1": "example.co.jp"}
    assert "/documents/S1?1" not in api.calls  # 古い有報は読まない
    assert reader.stats.found == 1


def test_公告を外部に任せている会社の_URL_は採らない():
    """2026-11-06 の probe：東京海上 HD の欄に開示書類の印刷会社のドメイン。"""
    api = FakeApi(
        {"2026-06-26": [_doc("S1", "E00001")]},
        {"S1": _zip(_koukoku("https://www.pronexus.co.jp/koukoku/1234/"))},
    )
    reader = EdinetKoukoku(api, _confirm_only({}), today=TODAY)
    assert reader.websites_for([("jp:1", "E00001", "東京海上ホールディングス株式会社", None)],
                               taken=set()) == {}
    assert reader.stats.name_not_on_page == 1


def test_他社に付いているドメインと_2社に出たドメインは採らない():
    api = FakeApi(
        {"2026-06-26": [_doc("S1", "E1"), _doc("S2", "E2"), _doc("S3", "E3")]},
        {"S1": _zip(_koukoku("https://www.parent.co.jp/")),
         "S2": _zip(_koukoku("https://www.group.co.jp/")),
         "S3": _zip(_koukoku("https://www.group.co.jp/"))},
    )
    ok = {"parent.co.jp": "株式会社子", "group.co.jp": "株式会社甲"}

    def confirm(domain, name_ja, name_en):
        return (domain, "found") if domain in ok else (None, "not_on_page")

    reader = EdinetKoukoku(api, confirm, today=TODAY)
    got = reader.websites_for(
        [("jp:1", "E1", "株式会社子", None), ("jp:2", "E2", "株式会社甲", None),
         ("jp:3", "E3", "株式会社乙", None)],
        taken={"parent.co.jp"},
    )
    assert got == {}
    assert reader.stats.taken_by_other == 3


def test_新聞公告と欄が無いものと有報が無いものを分けて数える():
    api = FakeApi(
        {"2026-06-26": [_doc("S1", "E1"), _doc("S2", "E2")]},
        {"S1": _zip("<td>公告掲載方法</td><td>日本経済新聞に掲載</td>"),
         "S2": _zip("<p>本文</p>")},
    )
    reader = EdinetKoukoku(api, _confirm_only({}), today=TODAY)
    reader.websites_for(
        [("jp:1", "E1", "株式会社甲", None), ("jp:2", "E2", "株式会社乙", None),
         ("jp:3", "E3", "株式会社丙", None), ("jp:4", None, "外国会社", None)],
        taken=set(),
    )
    s = reader.stats
    assert (s.newspaper, s.no_section, s.no_yuho, s.no_edinet_code) == (1, 1, 1, 1)


def test_一覧を引けなかった日があれば有報が無いとは言わない():
    def api(path, params):
        raise RuntimeError("down")

    reader = EdinetKoukoku(api, _confirm_only({}), today=TODAY)
    assert reader.websites_for([("jp:1", "E1", "株式会社甲", None)], taken=set()) == {}
    assert (reader.stats.no_yuho, reader.stats.yuho_unknown) == (0, 1)
    assert reader.stats.list_failed_days > 0


def test_土日は一覧を引かず_全社そろったら遡るのをやめる():
    api = FakeApi({"2026-11-05": [_doc("S1", "E1")]},
                  {"S1": _zip(_koukoku("https://www.example.co.jp/"))})
    reader = EdinetKoukoku(api, _confirm_only({}), today=TODAY)
    reader.websites_for([("jp:1", "E1", "株式会社甲", None)], taken=set())
    lists = [c for c in api.calls if c.startswith("/documents.json")]
    assert lists == ["/documents.json?2026-11-06", "/documents.json?2026-11-05"]


def test_過去の一覧と読んだ有報はキャッシュから使う(tmp_path):
    lists = {"2026-06-26": [_doc("S1", "E1")]}
    docs = {"S1": _zip(_koukoku("https://www.example.co.jp/"))}
    ok = _confirm_only({"example.co.jp": "株式会社甲"})
    first = FakeApi(lists, docs)
    EdinetKoukoku(first, ok, today=TODAY, cache_dir=tmp_path).websites_for(
        [("jp:1", "E1", "株式会社甲", None)], taken=set())
    second = FakeApi(lists, docs)
    reader = EdinetKoukoku(second, ok, today=TODAY, cache_dir=tmp_path)
    got = reader.websites_for([("jp:1", "E1", "株式会社甲", None)], taken=set())
    assert got == {"jp:1": "example.co.jp"}
    assert not any(c.startswith("/documents/S1") for c in second.calls)
    # 7日以内の一覧はキャッシュしない（取り下げ・追加がありうる）
    assert any(c == "/documents.json?2026-11-06" for c in second.calls)
    assert not any(c == "/documents.json?2026-06-26" for c in second.calls)


def test_取り下げた有報は使わない():
    withdrawn = dict(_doc("S1", "E1"), withdrawalStatus="1")
    api = FakeApi({"2026-06-26": [withdrawn]}, {})
    reader = EdinetKoukoku(api, _confirm_only({}), today=TODAY)
    reader.websites_for([("jp:1", "E1", "株式会社甲", None)], taken=set())
    assert reader.stats.no_yuho == 1


# -- P1 に組み込む ------------------------------------------------------------------


def test_空欄の会社だけを埋め_既にある値は上書きしない():
    from mailauth.contracts import Entity
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_edinet_koukoku

    def ent(eid, code, name, domain=None):
        return Entity(entity_id=eid, run_id="2026-11", country="JP",
                      population_ids=["jp-all-listed"], name=name, name_normalized=name,
                      edinet_code=code, securities_code="12340", official_domain=domain)

    have = ent("jp:1", "E1", "株式会社既存", "kizon.co.jp")
    empty = ent("jp:2", "E2", "株式会社甲")
    api = FakeApi({"2026-06-26": [_doc("S2", "E2")]},
                  {"S2": _zip(_koukoku("https://www.ko.co.jp/ir/"))})
    reader = EdinetKoukoku(api, _confirm_only({"ko.co.jp": "株式会社甲"}), today=TODAY)
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_edinet_koukoku([have, empty], manifest, reader=reader)
    assert have.official_domain == "kizon.co.jp"
    assert (empty.official_domain, empty.official_url) == ("ko.co.jp", "https://ko.co.jp/")
    b = manifest.breakdown["edinet_koukoku"]
    assert (b["missing_before"], b["found"], b["still_missing"]) == (1, 1, 0)


def test_offline_では引かず_そう書く():
    from mailauth.contracts import Entity
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_edinet_koukoku

    e = Entity(entity_id="jp:2", run_id="2026-11", country="JP",
               population_ids=["jp-all-listed"], name="株式会社甲", name_normalized="甲")
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_edinet_koukoku([e], manifest, offline=True)
    assert e.official_domain is None
    assert "ENRICH_SKIPPED_EDINET_KOUKOKU" in {w.code for w in manifest.warnings}


def test_国内の母集団は推測より先にこの経路を使う():
    data = yaml.safe_load(open("configs/populations/jp-all-listed.yaml", encoding="utf-8"))
    enrich = data["source"]["enrich"]
    assert enrich.index("wikidata_identity") < enrich.index("edinet_koukoku")
    assert enrich.index("edinet_koukoku") < enrich.index("co_jp_homepage")


def test_確かめは推測と同じ方法で_商号の無いページは採らない():
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    pages = {
        "https://www.ko-example.co.jp/": "<title>株式会社甲乙丙 | トップ</title>",
        "https://www.pronexus.co.jp/": "<title>株式会社プロネクサス</title>",
    }
    g = CoJpGuesser(exists=lambda d: True, fetch=lambda url: pages.get(url))
    assert g.confirm("ko-example.co.jp", "株式会社甲乙丙", None) == ("ko-example.co.jp", "found")
    assert g.confirm("pronexus.co.jp", "東京海上ホールディングス株式会社", None)[0] is None
