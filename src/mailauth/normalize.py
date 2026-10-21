"""社名・証券コード・ドメインの正規化。

名寄せで必ず起きる問題（DR-12）への対処をここに集約する。
一意識別子（日本＝法人番号、米国＝CIK/LEI）を主キーにし、社名は補助に留める
という方針は変えないが、突合の最後の手段として正規化名が要る。
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _psl():
    """公開接尾辞リスト（PSL）。読み込みが重いのでプロセス内で1回だけ。

    パッケージに同梱された一覧を使う（ネットワークには出ない）。
    """
    from publicsuffixlist import PublicSuffixList

    return PublicSuffixList()


def normalize_hostname(host: str | None) -> str | None:
    """ホスト名を比較できる形に揃える。丸めない。

    `etld_plus_one` と**同じ前処理**（小文字化・末尾ドット除去）をして、
    ラベルは落とさずに返す。両者を突き合わせて「これは apex か、その配下か」
    を判定するので、前処理がずれると判定もずれる。
    """
    if not host:
        return None
    h = host.strip().lower().rstrip(".")
    if not h or "." not in h:
        return None
    return h


def etld_plus_one(host: str) -> str | None:
    """ホスト名を eTLD+1（登録ドメイン）に丸める。

    **公開接尾辞リスト（PSL）で決める。** 以前は「co.jp」「com.au」など
    30ほどを手で並べた簡易版で、一覧に無い国の区分を取りこぼしていた。
    2026-10-21、米国の年次報告書から読んだ `www.irsa.com.ar` が `com.ar` に、
    `www.wf.com.my` が `com.my` に丸められていた ── **国の区分そのものを
    その会社のドメインとして測るところだった。**

    ホストがそれ自体公開接尾辞（`com.ar` や `co.jp`）なら None を返す。
    PSL の PRIVATE 節も使う（`x.github.io` は `github.io` ではなく
    `x.github.io` が登録単位）。
    """
    if not host:
        return None
    h = host.strip().lower().rstrip(".")
    if not h or "." not in h:
        return None
    return _psl().privatesuffix(h) or None


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
