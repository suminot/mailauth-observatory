"""名寄せの正規化。DR-12「名寄せで必ず起きる問題」への対処を検証する。"""

from __future__ import annotations

import pytest

from mailauth.normalize import (
    domain_from_url,
    etld_plus_one,
    normalize_houjin_bangou,
    normalize_name,
    normalize_securities_code,
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # 「株式会社」の前後で表記が割れても同じ値に落ちること
        ("株式会社サンプル情報システム", "サンプル情報システム株式会社"),
        # 全角・半角のゆれ（NFKC）
        ("ＡＢＣ商事株式会社", "ABC商事"),
        # 英語の法人格語
        ("Sample Motor Corporation", "Sample Motor"),
        ("Sample Foods Co., Ltd.", "sample foods"),
        ("SAMPLE HOLDINGS, INC.", "Sample"),
    ],
)
def test_normalize_name_collapses_variants(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_normalize_name_keeps_distinct_companies_distinct():
    assert normalize_name("株式会社サンプル銀行") != normalize_name("株式会社サンプル証券")


def test_normalize_name_handles_empty():
    assert normalize_name(None) == ""
    assert normalize_name("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12340", "1234"),  # EDINET は5桁・末尾0
        ("1234", "1234"),  # 一般は4桁
        ("１２３４０", "1234"),  # 全角
        ("", None),
        (None, None),
    ],
)
def test_normalize_securities_code(raw, expected):
    assert normalize_securities_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234567890123", "1234567890123"),
        ("1234-5678-90123", "1234567890123"),
        ("123", None),  # 桁数不正は欠損として扱う
        ("", None),
        (None, None),
    ],
)
def test_normalize_houjin_bangou(raw, expected):
    assert normalize_houjin_bangou(raw) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.example.co.jp", "example.co.jp"),  # 2ラベルの実効TLD
        ("mail.corp.example.co.jp", "example.co.jp"),
        ("www.example.com", "example.com"),
        ("example.com", "example.com"),
        ("a.b.c.example.org", "example.org"),
        ("localhost", None),
        ("", None),
    ],
)
def test_etld_plus_one(host, expected):
    assert etld_plus_one(host) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.co.jp/ir/", "example.co.jp"),
        ("http://example.com", "example.com"),
        ("www.example.co.jp", "example.co.jp"),  # スキームなしも通す
        ("", None),
        (None, None),
    ],
)
def test_domain_from_url(url, expected):
    assert domain_from_url(url) == expected
