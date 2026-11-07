"""子会社名 → gBizINFO → 企業サイトのドメイン（BACKLOG 52）。ネットワークに出ない。"""

from __future__ import annotations

import httpx

from mailauth.p1_population.group_domains import GbizNameSearch, read_csv, scan, write_csv


def _search(tmp_path, hits: dict[str, list[dict]], details: dict[str, str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-hojinInfo-api-token"] == "t"
        path = request.url.path
        if path.endswith("/hojin"):
            found = hits.get(request.url.params["name"], [])
            return httpx.Response(200, json={"hojin-infos": found})
        number = path.rsplit("/", 1)[-1]
        url = (details or {}).get(number)
        return httpx.Response(200, json={"hojin-infos": [{"company_url": url}] if url else []})

    return GbizNameSearch("t", qps=0, cache_dir=tmp_path,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_商号が完全に一致する法人が1つならその企業サイトのドメインを採る(tmp_path):
    s = _search(tmp_path, {
        "株式会社甲乙丙ロジ": [
            {"corporate_number": "1111111111111", "name": "株式会社甲乙丙ロジ"},
            # 部分一致で返る別の会社は採らない
            {"corporate_number": "2222222222222", "name": "株式会社甲乙丙ロジスティクス東海"},
        ],
    }, {"1111111111111": "https://www.kouotsu-logi.co.jp/"})
    assert s.lookup("株式会社甲乙丙ロジ") == ("1111111111111", "kouotsu-logi.co.jp", "found")
    # 2回目はキャッシュ（API を呼ばない）
    calls = s.calls
    assert s.lookup("株式会社甲乙丙ロジ")[2] == "found"
    assert s.calls == calls


def test_同じ商号の法人が複数あれば採らない(tmp_path):
    s = _search(tmp_path, {"株式会社サンプル": [
        {"corporate_number": "1", "name": "株式会社サンプル"},
        {"corporate_number": "2", "name": "株式会社 サンプル"},
    ]})
    assert s.lookup("株式会社サンプル") == (None, None, "ambiguous")


def test_一覧を順に引き_引き終えた名前は引き直さない(tmp_path):
    s = _search(tmp_path, {"A株式会社": [{"corporate_number": "9", "name": "A株式会社",
                                         "company_url": "http://a.example.co.jp"}]})
    subs = {"E1": ("S1", ["A株式会社", "B株式会社"], "2026-11-07")}
    data, stats = scan(s, subs, {}, max_lookups=10)
    assert data[("E1", "A株式会社")] == ("9", "example.co.jp", "found")
    assert data[("E1", "B株式会社")][2] == "not_found"
    path = tmp_path / "g.csv"
    write_csv(path, data)
    again, stats2 = scan(s, subs, read_csv(path), max_lookups=10)
    assert stats2["already"] == 2 and stats2["looked_up"] == 0
