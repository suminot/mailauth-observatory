"""DKIM の解釈（三値表現。原則5 の適用）。

**「未設定」と「既知セレクタでは未検出」は厳密に区別する。** セレクタは
DNS 上で列挙できないため、検出できなかったことは「無い」の証明にならない。
総務省が JP ドメイン外形調査から DKIM を明示的に除外しているのが、この
区別の重要性を裏づけている。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from ..contracts import DkimStatus

#: p= が空なら失効を意味する（RFC 6376 §3.6.1
#: "An empty value means that this public key has been revoked"）
REVOKED_EMPTY_P = ""


@dataclass
class DkimKey:
    selector: str
    raw: str
    valid: bool = False
    key_type: str | None = None
    key_bits: int | None = None
    revoked: bool = False
    testing: bool = False
    #: CNAME 先。署名基盤の推定に使う（証拠として最も強い）
    cname_target: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class DkimResult:
    status: str = DkimStatus.NOT_APPLICABLE
    selectors: list[str] = field(default_factory=list)
    key_bits: list[int] = field(default_factory=list)
    keys: list[DkimKey] = field(default_factory=list)
    testing_flag: bool = False
    revoked: bool = False
    #: 対照クエリが応答した。何にでも答える DNS なので検出結果は信用できない
    wildcard_suspect: bool = False
    #: ワイルドカードセレクタに失効鍵が置かれていた（M3AAWG 推奨構成）
    wildcard_revoked_key: bool = False
    selectors_tried: int = 0
    #: セレクタの委譲先（重複除去済み）。署名基盤の推定に使う
    cname_targets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _tags(txt: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in txt.split(";"):
        name, sep, value = part.partition("=")
        if not sep:
            continue
        key = name.strip().lower()
        if key and key not in out:
            out[key] = value.strip()
    return out


def estimate_key_bits(p_value: str) -> int | None:
    """base64 の公開鍵からビット長を推定する。

    DER でラップされた SubjectPublicKeyInfo の長さから概算する。
    厳密なパースはしない。1024/2048/4096 の区別が付けば足りる。
    """
    cleaned = re.sub(r"\s+", "", p_value or "")
    if not cleaned:
        return None
    try:
        raw = base64.b64decode(cleaned + "=" * (-len(cleaned) % 4), validate=False)
    except (ValueError, TypeError):
        return None
    # SPKI ヘッダ（RSA で 22〜24 バイト程度）を引いて 8 倍する
    body = max(len(raw) - 24, 0)
    bits = body * 8
    for standard in (512, 1024, 2048, 3072, 4096):
        if abs(bits - standard) <= standard * 0.15:
            return standard
    return bits or None


def parse_key(selector: str, txt: str, cname_target: str | None = None) -> DkimKey:
    """1つの DKIM 鍵レコードを解釈する。"""
    key = DkimKey(selector=selector, raw=txt, cname_target=cname_target)
    tags = _tags(txt)

    version = tags.get("v")
    if version is not None and version.upper() != "DKIM1":
        key.notes.append(f"v タグが DKIM1 でない: {version!r}")
        return key

    key.key_type = (tags.get("k") or "rsa").lower()
    key.testing = "y" in (tags.get("t") or "").lower().split(":")
    p_value = tags.get("p")

    if p_value is None:
        key.notes.append("p タグが無い。鍵レコードとして不正")
        return key

    if p_value.strip() == REVOKED_EMPTY_P:
        # 失効。ワイルドカードに置いておけば攻撃者がどのセレクタを騙っても
        # 失効鍵に当たる（M3AAWG のパークドメイン推奨構成）
        key.valid = True
        key.revoked = True
        key.notes.append("p= が空。この公開鍵は失効している（RFC 6376 §3.6.1）")
        return key

    key.valid = True
    key.key_bits = estimate_key_bits(p_value)
    if key.key_bits and key.key_bits < 1024:
        key.notes.append(f"鍵長 {key.key_bits} bit は短すぎる（1024bit 未満）")
    if key.testing:
        key.notes.append("t=y。テストモードのため受信側は検証失敗を無視しうる")
    return key


def build_result(
    *,
    found: dict[str, str],
    selectors_tried: int,
    control_responded: bool,
    wildcard_record: str | None = None,
    cnames: dict[str, str] | None = None,
    applicable: bool = True,
) -> DkimResult:
    """検出結果をまとめる。

    `found` は {セレクタ: TXT}。`applicable=False` は階層C など
    そもそもセレクタを投げていない場合で、`not_applicable` になる。
    """
    result = DkimResult(selectors_tried=selectors_tried)
    cname_map = cnames or {}
    # **CNAME は鍵が読めなくても記録する。** SERVFAIL 等で TXT が取れなくても
    # 委譲先が分かっていれば署名基盤の推定はできる（DESIGN.md P6）
    seen: set[str] = set()
    for target in cname_map.values():
        normalized = (target or "").strip().rstrip(".").lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.cname_targets.append(normalized)
    result.cname_targets.sort()

    if control_responded:
        # 実在しないセレクタに応答した。検出結果は偽陽性の可能性がある
        result.wildcard_suspect = True
        result.notes.append(
            "対照クエリ（実在しないセレクタ）が応答した。"
            "何にでも答える DNS のため DKIM の検出は信用できない"
        )

    if wildcard_record:
        wildcard_key = parse_key("*", wildcard_record)
        result.keys.append(wildcard_key)
        if wildcard_key.revoked:
            result.wildcard_revoked_key = True
            result.notes.append(
                "*._domainkey に失効鍵が置かれている（M3AAWG のパークドメイン推奨構成）"
            )

    for selector, txt in sorted(found.items()):
        key = parse_key(selector, txt, cname_map.get(selector))
        result.keys.append(key)
        if key.valid and not key.revoked:
            result.selectors.append(selector)
            if key.key_bits:
                result.key_bits.append(key.key_bits)
        if key.testing:
            result.testing_flag = True
        if key.revoked:
            result.revoked = True

    if not applicable:
        result.status = DkimStatus.NOT_APPLICABLE
        result.notes.append("セレクタを投げていない（階層C）。未設定の証拠にはならない")
    elif result.selectors:
        result.status = DkimStatus.DETECTED
    else:
        # ここが重要。「未設定」ではない
        result.status = DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS
        result.notes.append(
            f"既知セレクタ {selectors_tried} 個では検出できなかった。"
            "セレクタは DNS 上で列挙できないため、未設定の証明にはならない"
        )

    return result
