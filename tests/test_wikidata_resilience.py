"""Wikidata を引けなかった1回で、補完を丸ごと失わない。

2026-11-02 の国内の流し直し（run 41540374374）は、WDQS の HTTP/2 で
StreamReset が1回起きただけで ENRICH_FAILED_WIKIDATA になり、Wikidata の
1,207 社分の公式サイトを失った（欠けが 405 → 1,021 社）。P7 はそれを
劣化と見ておらず、既にある 2026-11 の gold を小さく上書きしかけた。
"""

from __future__ import annotations

import json

import httpx
import pytest

from mailauth.p1_population import wikidata
from mailauth.p1_population.wikidata import WikidataError, fetch_identity, run_query

BINDINGS = [
    {
        "houjin_bangou": {"value": "1234567890123"},
        "website": {"value": "https://www.example.co.jp/"},
    }
]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(wikidata, "_sleep", waits.append)
    return waits


@pytest.fixture
def query_file(tmp_path, monkeypatch):
    path = tmp_path / "q.rq"
    path.write_text("SELECT ?houjin_bangou ?website WHERE {}", encoding="utf-8")
    return str(path)


def _client(responses):
    """順に返す。例外なら投げる。"""
    calls = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok():
    return httpx.Response(200, json={"results": {"bindings": BINDINGS}})


def test_接続を切られても引き直す(_no_sleep):
    client = _client([httpx.RemoteProtocolError("<StreamReset stream_id:1>"), _ok()])
    assert run_query("q", client=client) == BINDINGS
    assert _no_sleep == [wikidata.RETRY_WAITS[0]]


def test_混んでいれば待って引き直す(_no_sleep):
    client = _client([httpx.Response(503), httpx.Response(429), _ok()])
    assert run_query("q", client=client) == BINDINGS
    assert _no_sleep == list(wikidata.RETRY_WAITS)


def test_引き直しても切られるなら失敗にする():
    err = httpx.RemoteProtocolError("<StreamReset stream_id:1>")
    client = _client([err] * (len(wikidata.RETRY_WAITS) + 1))
    with pytest.raises(WikidataError, match="StreamReset"):
        run_query("q", client=client)


def test_引けなければ前に引いた結果を使う(query_file):
    """公式サイトの URL は月単位ではほとんど変わらない。**使ったことは残す。**"""
    first, _ = fetch_identity(query_file, key="houjin_bangou", client=_client([_ok()]))
    err = httpx.RemoteProtocolError("reset")
    again, stats = fetch_identity(
        query_file,
        key="houjin_bangou",
        client=_client([err] * (len(wikidata.RETRY_WAITS) + 1)),
    )
    assert again == first
    assert stats["from_cache"]


def test_前に引いた結果が無ければ今までどおり失敗(query_file):
    err = httpx.RemoteProtocolError("reset")
    with pytest.raises(WikidataError):
        fetch_identity(
            query_file,
            key="houjin_bangou",
            client=_client([err] * (len(wikidata.RETRY_WAITS) + 1)),
        )


def test_古すぎる結果は使わない(query_file):
    fetch_identity(query_file, key="houjin_bangou", client=_client([_ok()]))
    path = wikidata._cache_path(wikidata.load_query(query_file))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fetched_at"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(data), encoding="utf-8")
    err = httpx.RemoteProtocolError("reset")
    with pytest.raises(WikidataError):
        fetch_identity(
            query_file,
            key="houjin_bangou",
            client=_client([err] * (len(wikidata.RETRY_WAITS) + 1)),
        )


def test_P1_が補完を引けなかった_run_も劣化として扱う():
    from mailauth.manifest import MANIFEST_FILENAME
    from mailauth.p7_aggregate.runner import degraded_by
    from mailauth.paths import phase_dir

    run = "2026-11"
    assert degraded_by(run) is None
    path = phase_dir(run, "p1_population") / MANIFEST_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"warnings": [{"code": "ENRICH_FAILED_WIKIDATA", "message": "x"}]}),
        encoding="utf-8",
    )
    assert degraded_by(run) == "ENRICH_FAILED_WIKIDATA"
