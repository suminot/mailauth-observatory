"""パークドメイン分類（DESIGN.md P6）。

**本システム固有の差別化指標。** 網羅展開したドメインの大半は送信に使われて
いない。その中で「適切に固められたもの」と「単に放置されたもの」を区別する。

攻撃者から見れば、送信実績がなく監視もされていないドメインはなりすましの
理想的な出発点になる。したがって非送信ドメインをどう扱っているかは、その
組織のメールセキュリティ成熟度を測る良い代理指標になる。

Czybik et al.（IMC 2023）は「MX のないドメインの10.4%が SPF を持ち、うち
53.1%が `-all`/`~all`」と報告しているが、Null MX と DMARC を組み合わせた
段階化はしていない。ここを4段階に分けられることが本システムの固有の数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import ParkClass

#: 強制と見なす DMARC ポリシー
ENFORCING_POLICIES = ("reject", "quarantine")
#: 送信を否定する all の修飾子。`-` は hardfail、`~` は softfail
HARD_DENY = "-"
SOFT_DENY = ("-", "~")


@dataclass
class ParkResult:
    park_class: str | None = None
    has_null_mx: bool | None = None
    has_wildcard_dkim_revoked: bool | None = None
    notes: list[str] = field(default_factory=list)


def classify(fact: dict) -> ParkResult:
    """fact からパーク分類を決める。

    **観測できなかったドメインは分類しない。** SERVFAIL で MX も SPF も
    取れなかったドメインを `neglected`（放置）と呼ぶのは事実の捏造である。
    「取れなかった」と「無かった」を混ぜない（原則5）。
    """
    result = ParkResult(
        has_null_mx=fact.get("mx_null"),
        has_wildcard_dkim_revoked=fact.get("dkim_wildcard_revoked"),
    )

    if not fact.get("observed"):
        result.notes.append(
            "観測できていないためパーク分類をしていない。"
            "レコードが無かったのではなく、取れなかった（原則5）"
        )
        return result

    null_mx = bool(fact.get("mx_null"))
    mx_present = bool(fact.get("mx_present"))
    spf_present = bool(fact.get("spf_present"))
    dmarc_present = bool(fact.get("dmarc_present"))
    qualifier = fact.get("spf_all_qualifier")
    effective = fact.get("effective_7489")

    if mx_present:
        # 実 MX がある。通常の送受信ドメインなのでパーク分類の対象外
        result.park_class = (
            ParkClass.ACTIVE_SENDING if spf_present else ParkClass.INCONSISTENT
        )
        if not spf_present:
            result.notes.append(
                "MX はあるが SPF が無い。送信元の認可を宣言していない状態"
            )
        return result

    hard_deny = qualifier == HARD_DENY
    soft_deny = qualifier in SOFT_DENY
    enforced = effective in ENFORCING_POLICIES

    if null_mx and hard_deny and effective == "reject":
        result.park_class = ParkClass.HARDENED_PARKED
        result.notes.append(
            "Null MX + SPF -all + DMARC p=reject。"
            "送信も受信も明示的に否定している模範的な構成（M3AAWG 推奨）"
        )
    elif soft_deny and enforced:
        result.park_class = ParkClass.DEFENDED_PARKED
    elif soft_deny:
        result.park_class = ParkClass.INTENTIONAL_NO_SEND
        result.notes.append(
            "送信禁止の意図はあるが DMARC が弱い。"
            "SPF の -all だけでは転送やアライメント緩和の隙間が残る"
        )
    elif not spf_present and not dmarc_present:
        result.park_class = ParkClass.NEGLECTED
        result.notes.append(
            "MX も SPF も DMARC も無い。なりすましの出発点になりうる"
        )
    else:
        result.park_class = ParkClass.INCONSISTENT

    if result.has_wildcard_dkim_revoked:
        # 実効的な防御力は小さいが、不作為ではなく明示的な宣言であることに
        # 意味がある。効いているのは p=reject と strict alignment の方
        result.notes.append(
            "*._domainkey に失効鍵がある。明示的な宣言として意図を示せているが、"
            "レコードが無くても DKIM 検証は失敗するため実効的な防御力は小さい"
        )

    return result


def is_parked(park_class: str | None) -> bool:
    """パーク分類の集計対象か（DESIGN.md P6「この分類が生む固有の数字」）。

    `active_sending` は通常の送信ドメインなので分母から外す。
    `inconsistent` は矛盾しており要個別確認なので、防御率の分母には入れない。
    """
    return park_class in (
        ParkClass.HARDENED_PARKED,
        ParkClass.DEFENDED_PARKED,
        ParkClass.INTENTIONAL_NO_SEND,
        ParkClass.NEGLECTED,
    )


def is_defended(park_class: str | None) -> bool:
    """非送信ドメインの防御率の分子。"""
    return park_class in (ParkClass.HARDENED_PARKED, ParkClass.DEFENDED_PARKED)
