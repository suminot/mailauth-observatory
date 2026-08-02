"""社名・証券コード・ドメインの正規化。

名寄せで必ず起きる問題（DR-12）への対処をここに集約する。
一意識別子（日本＝法人番号、米国＝CIK/LEI）を主キーにし、社名は補助に留める
という方針は変えないが、突合の最後の手段として正規化名が要る。
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

#: 法人格語。NFKC 正規化のあとに前後どちらの位置でも落とす。
_LEGAL_FORMS_JA = [
    "株式会社",
    "有限会社",
    "合同会社",
    "合名会社",
    "合資会社",
    "一般社団法人",
    "一般財団法人",
    "公益社団法人",
    "公益財団法人",
    "特定非営利活動法人",
    "独立行政法人",
    "国立大学法人",
    "医療法人",
    "学校法人",
    "社会福祉法人",
    "相互会社",
]

_LEGAL_FORMS_EN = [
    "incorporated",
    "corporation",
    "company limited",
    "company",
    "limited",
    "holdings",
    "group",
    "inc",
    "corp",
    "co",
    "ltd",
    "llc",
    "lp",
    "plc",
    "sa",
    "ag",
    "nv",
    "bv",
    "gmbh",
    "kk",
]

_PUNCT_RE = re.compile(r"[\s,\.\-–—_/\\'\"()（）\[\]【】&＆・･]+")


def normalize_name(name: str | None) -> str:
    """NFKC正規化 -> 法人格語除去 -> 空白正規化（DESIGN.md P1 実装メモ）。

    英字は小文字に畳む。突合専用の値であり、表示には使わない。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).strip()

    for form in _LEGAL_FORMS_JA:
        s = s.replace(form, "")

    s = s.lower()
    # 記号を区切りに落としてからトークン単位で英語法人格語を除く。
    # 「company」を含む正当な社名を壊さないよう、末尾・先頭のトークンだけを見る。
    tokens = [t for t in _PUNCT_RE.split(s) if t]
    while tokens and tokens[-1] in _LEGAL_FORMS_EN:
        tokens.pop()
    while tokens and tokens[0] in _LEGAL_FORMS_EN:
        tokens.pop(0)

    return "".join(tokens)


def normalize_securities_code(code: str | None) -> str | None:
    """EDINET は5桁（末尾0）、JPX/一般は4桁。突合のため先頭4桁に揃える。"""
    if not code:
        return None
    s = unicodedata.normalize("NFKC", code).strip()
    if not s:
        return None
    if len(s) == 5 and s.endswith("0"):
        return s[:4]
    return s[:4] if len(s) > 4 else s


def normalize_houjin_bangou(value: str | None) -> str | None:
    """13桁の数字のみを通す。桁数が違うものは None にして欠損として数える。"""
    if not value:
        return None
    s = re.sub(r"\D", "", unicodedata.normalize("NFKC", value))
    return s if len(s) == 13 else None


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    s = unicodedata.normalize("NFKC", url).strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s


#: eTLD+1 を求めるにあたり、2ラベルで1つの実効TLDになるもの。
#: 本格的な PSL は P3/P5 で導入する。P1 で必要なのは official_url の
#: 正規化だけなので、日本と主要国の一般的な形だけを見る。
_MULTI_LABEL_SUFFIXES = {
    "co.jp", "or.jp", "ne.jp", "ac.jp", "ad.jp", "ed.jp", "go.jp", "gr.jp", "lg.jp",
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "com.cn", "net.cn", "org.cn",
    "co.kr", "or.kr",
    "com.br", "com.mx", "com.sg", "com.hk", "com.tw", "co.in", "co.th", "co.id",
    "com.tr", "co.nz", "co.za",
}


def etld_plus_one(host: str) -> str | None:
    """ホスト名を eTLD+1 に丸める。

    PSL を丸ごと持たない簡易実装。P1 の official_domain 用。
    ドメイン同定の本番（P2/P3）では publicsuffix2 等に差し替えること。
    """
    if not host:
        return None
    h = host.strip().lower().rstrip(".")
    if not h or "." not in h:
        return None
    labels = h.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_from_url(url: str | None) -> str | None:
    """URL から eTLD+1 を取り出す。取り出せなければ None。"""
    normalized = normalize_url(url)
    if not normalized:
        return None
    try:
        host = urlparse(normalized).hostname
    except ValueError:
        return None
    if not host:
        return None
    return etld_plus_one(host)
