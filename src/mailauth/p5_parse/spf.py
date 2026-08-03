"""SPF の解釈（DESIGN.md P5「SPF のエッジケース」）。

records.py が「読み取り」なのに対し、ここは「仕様に照らした解釈」を行う。
このフェーズは何度でも作り直せる。バグが見つかったら bronze から再実行する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..records import (
    find_spf_records,
    spf_all_qualifier,
    spf_includes,
    spf_is_dynamic,
    spf_terms,
)

#: ルックアップを行うメカニズム（RFC 7208 §4.6.4）。
#: 「ルックアップを行うメカニズムの数」であり、生成されるクエリ総数ではない。
LOOKUP_MECHANISMS = ("include", "a", "mx", "ptr", "exists")
#: 修飾子側でルックアップを伴うもの
LOOKUP_MODIFIERS = ("redirect",)
#: カウント対象外
NON_LOOKUP_MECHANISMS = ("all", "ip4", "ip6")

#: 10ルックアップ制限（RFC 7208 §4.6.4）。予算は評価パス全体で共有される。
#: include 内で別途10もらえるわけではない。
MAX_LOOKUPS = 10
#: void lookup は2回まで（SHOULD だが主要受信者は実質強制）
MAX_VOID_LOOKUPS = 2

#: Valimail の動的SPF。静的にルックアップ数を数えても無意味
VALIMAIL_PATTERN = re.compile(r"_spf\.vali\.email", re.IGNORECASE)


class SpfError:
    NONE = None
    PERMERROR = "permerror"
    TEMPERROR = "temperror"
    MULTIPLE_RECORDS = "multiple_records"


@dataclass
class SpfResult:
    present: bool = False
    valid: bool = False
    error: str | None = None
    raw: str | None = None
    all_qualifier: str | None = None
    #: ルックアップを伴うメカニズムの数。include 先の展開は含まない
    #: （展開には include 先の SPF が必要で、それは別ドメインの観測）
    lookup_count: int = 0
    void_count: int = 0
    exceeds_limit: bool = False
    includes: list[str] = field(default_factory=list)
    is_flattened: bool = False
    is_dynamic: bool = False
    #: 評価パス全体で共有される予算のうち、このレコード単体で消費する分
    terms: list[str] = field(default_factory=list)
    ip4_count: int = 0
    ip6_count: int = 0
    has_ptr: bool = False
    notes: list[str] = field(default_factory=list)


def count_lookups(record: str) -> int:
    """ルックアップを行うメカニズムの数を数える。

    カウント対象: include, a, mx, ptr, exists, redirect
    非対象:       all, ip4, ip6
    """
    count = 0
    for term in spf_terms(record):
        bare = term.lstrip("+-~?")
        name = bare.split(":", 1)[0].split("=", 1)[0].split("/", 1)[0].lower()
        if name in LOOKUP_MECHANISMS or name in LOOKUP_MODIFIERS:
            count += 1
    return count


def detect_flattening(ip4_count: int, ip6_count: int, include_count: int) -> bool:
    """フラット化の検出（DESIGN.md P5）。

    ip4/ip6 が数十〜数百 かつ include がほぼ無い → フラット化疑い。
    10ルックアップ制限を回避するために include を IP に展開する運用がある。
    """
    return (ip4_count + ip6_count) >= 20 and include_count <= 1


def parse(txt_records: list[str]) -> SpfResult:
    """apex の TXT 群から SPF を解釈する。

    複数の v=spf1 があれば PermError（RFC 7208 §4.5）。
    0件なら none（レコードが無い、はエラーではない）。
    """
    records = find_spf_records(txt_records)

    if not records:
        return SpfResult(present=False, valid=False, error=SpfError.NONE)

    if len(records) > 1:
        # ポリシー全体が無効になる。どちらを採るかの問題ではない
        return SpfResult(
            present=True,
            valid=False,
            error=SpfError.MULTIPLE_RECORDS,
            raw=records[0],
            notes=[f"v=spf1 が {len(records)} 件あり PermError（RFC 7208 §4.5）"],
        )

    record = records[0]
    terms = spf_terms(record)
    includes = spf_includes(record)
    lookups = count_lookups(record)
    ip4 = sum(1 for t in terms if t.lstrip("+-~?").lower().startswith("ip4:"))
    ip6 = sum(1 for t in terms if t.lstrip("+-~?").lower().startswith("ip6:"))
    has_ptr = any(t.lstrip("+-~?").lower().split(":", 1)[0] == "ptr" for t in terms)

    result = SpfResult(
        present=True,
        valid=True,
        error=SpfError.NONE,
        raw=record,
        all_qualifier=spf_all_qualifier(record),
        lookup_count=lookups,
        exceeds_limit=lookups > MAX_LOOKUPS,
        includes=includes,
        is_flattened=detect_flattening(ip4, ip6, len(includes)),
        is_dynamic=spf_is_dynamic(record) or bool(VALIMAIL_PATTERN.search(record)),
        terms=terms,
        ip4_count=ip4,
        ip6_count=ip6,
        has_ptr=has_ptr,
    )

    if result.exceeds_limit:
        result.valid = False
        result.error = SpfError.PERMERROR
        result.notes.append(
            f"ルックアップ数 {lookups} が上限 {MAX_LOOKUPS} を超えている（RFC 7208 §4.6.4）"
        )
    if result.is_dynamic:
        result.notes.append(
            "動的SPF（マクロを含む）。静的にルックアップ数を数えても実効を表さない"
        )
    if result.is_flattened:
        result.notes.append(
            f"フラット化疑い（ip4/ip6 が {ip4 + ip6} 件、include が {len(includes)} 件）"
        )
    if has_ptr:
        result.notes.append("ptr は非推奨（RFC 7208 §5.5）")
    if result.all_qualifier is None:
        result.notes.append("all が無い。評価は neutral で終わる")

    return result


def count_void_lookups(include_results: dict[str, bool]) -> int:
    """void lookup の数を数える。

    NXDOMAIN または NODATA を返すクエリが void lookup。
    `include_results` は {ドメイン: レコードが存在したか}。
    2回を超えると PermError（SHOULD だが主要受信者は実質強制）。
    """
    return sum(1 for present in include_results.values() if not present)
