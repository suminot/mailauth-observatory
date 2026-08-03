"""SPF / DMARC / MX の最小限の解析。本格的な解釈は P5 の責務。"""

from __future__ import annotations

import pytest

from mailauth.records import (
    dmarc_report_domains,
    dmarc_tags,
    find_dmarc_records,
    find_spf_records,
    is_dmarc_record,
    is_null_mx,
    is_spf_record,
    join_txt_strings,
    mx_hosts,
    spf_all_qualifier,
    spf_includes,
    spf_is_dynamic,
    spf_mechanism_domains,
    spf_redirect,
)

# -- 分割TXTの連結 -----------------------------------------------------------


def test_split_txt_strings_are_joined_in_order():
    """255バイト超の TXT は複数の character-string に分割される。連結しないと取りこぼす。"""
    chunks = ["v=spf1 include:_spf.example.com ", "include:_spf2.example.com -all"]
    joined = join_txt_strings(chunks)
    assert joined == "v=spf1 include:_spf.example.com include:_spf2.example.com -all"
    assert spf_includes(joined) == ["_spf.example.com", "_spf2.example.com"]


# -- SPF の判定 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("txt", "expected"),
    [
        ("v=spf1 -all", True),
        ("v=spf1", True),
        ("V=SPF1 -all", True),  # バージョンのキーワードは case-insensitive に扱う
        ("v=spf10 -all", False),  # バージョンセクションは SP か終端で終わる
        ("v=spf2.0/pra -all", False),  # Sender ID は SPF ではない
        ("v=DMARC1; p=none", False),
        ("", False),
    ],
)
def test_is_spf_record(txt, expected):
    assert is_spf_record(txt) is expected


def test_find_spf_records_picks_only_spf():
    txts = ["v=spf1 -all", "google-site-verification=abc", "MS=ms12345", "v=spf1 ~all"]
    assert find_spf_records(txts) == ["v=spf1 -all", "v=spf1 ~all"]


@pytest.mark.parametrize(
    ("txt", "expected"),
    [
        ("v=spf1 -all", "-"),
        ("v=spf1 ~all", "~"),
        ("v=spf1 ?all", "?"),
        ("v=spf1 +all", "+"),
        ("v=spf1 all", "+"),  # 修飾子の省略は + を意味する
        ("v=spf1 include:x.example", None),
        ("v=spf1 -allx", None),
    ],
)
def test_spf_all_qualifier(txt, expected):
    assert spf_all_qualifier(txt) == expected


def test_spf_includes():
    txt = (
        "v=spf1 ip4:198.51.100.0/24 include:spf.protection.outlook.com "
        "include:_spf.google.com -all"
    )
    assert spf_includes(txt) == ["spf.protection.outlook.com", "_spf.google.com"]


def test_spf_redirect_is_ignored_when_all_is_present():
    """all が存在すると redirect は無視される（RFC 7208 §6.1）。"""
    assert spf_redirect("v=spf1 redirect=_spf.example.com") == "_spf.example.com"
    assert spf_redirect("v=spf1 -all redirect=_spf.example.com") is None
    assert spf_redirect("v=spf1 include:x.example -all") is None


def test_spf_mechanism_domains_catches_sakura_style():
    """さくらは専用 include を持たず a: で表現する。include だけ見ると取りこぼす。"""
    txt = "v=spf1 a:www1234.sakura.ne.jp mx ~all"
    assert spf_mechanism_domains(txt) == ["www1234.sakura.ne.jp"]


def test_spf_is_dynamic_detects_macros():
    """Valimail の動的SPF。静的にルックアップ数を数えても無意味なので識別する。"""
    assert spf_is_dynamic("v=spf1 include:%{i}._ip.%{h}._ehlo.%{d}._spf.vali.email -all")
    assert not spf_is_dynamic("v=spf1 include:spf.protection.outlook.com -all")


# -- DMARC の判定 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("txt", "expected"),
    [
        ("v=DMARC1; p=reject", True),
        ("V=DMARC1; p=reject", True),  # タグ名は case-insensitive
        ("v=dmarc1; p=reject", False),  # 値は case-sensitive で厳密一致
        ("v=spf1 -all", False),
        ("", False),
    ],
)
def test_is_dmarc_record(txt, expected):
    assert is_dmarc_record(txt) is expected


def test_find_dmarc_records():
    assert find_dmarc_records(["v=DMARC1; p=none", "random"]) == ["v=DMARC1; p=none"]


def test_dmarc_tags_takes_first_of_duplicates():
    tags = dmarc_tags("v=DMARC1; p=reject; pct=50; p=none; unknown=keep")
    assert tags["p"] == "reject"  # 重複は最初の出現を採る
    assert tags["pct"] == "50"
    assert tags["unknown"] == "keep"  # 未知タグも捨てない


def test_dmarc_report_domains_keeps_only_the_domain_part():
    """個人情報を集めないという非目的のため、ローカル部は保存しない。"""
    txt = "v=DMARC1; p=reject; rua=mailto:agg@example.co.jp,mailto:x@dmarc25.jp!10m"
    assert dmarc_report_domains(txt, "rua") == ["example.co.jp", "dmarc25.jp"]


def test_dmarc_report_domains_handles_missing_tag():
    assert dmarc_report_domains("v=DMARC1; p=none", "ruf") == []


# -- MX の判定 --------------------------------------------------------------


def test_null_mx_is_detected():
    """RFC 7505 の Null MX。「受け取らない」の明示的な宣言であって設定漏れではない。"""
    assert is_null_mx(["."]) is True
    assert is_null_mx(["mx.example.com."]) is False
    assert is_null_mx([".", "mx.example.com."]) is False  # 単独でなければ Null MX ではない
    assert is_null_mx([]) is False


def test_mx_hosts_normalizes_and_drops_null():
    assert mx_hosts(["MX1.Example.COM.", "mx2.example.com"]) == [
        "mx1.example.com",
        "mx2.example.com",
    ]
    assert mx_hosts(["."]) == []
