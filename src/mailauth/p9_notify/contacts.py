"""通知先の候補を集める（DESIGN.md Sprint 9）。

**到達率に過度な期待をしないこと。** リサーチ（DR-18）の実測値。

| 経路 | 実測 |
|---|---|
| security.txt の普及率（Fortune 500） | 約4%（Cybernews / M. Repa, 2024年11月） |
| RFC 2142 エイリアスの到達率（Top 1M） | 24.16%（Soussi/Korczyński, IEEE 2020）|

到達率はバウンスしないだけの数字であって、読まれた保証ではない。

つまり**大半のドメインには届かない。** 届かないことを前提に、
届いた分だけを数える設計にする。「送った」と「届いた」と「読まれた」は
それぞれ別の事実である（原則5 の延長）。

RFC 2142 のエイリアスは**存在を確認できない。** SMTP で VRFY を投げるのは
迷惑行為であり、実際に送ってみるまで分からない。よって候補としては挙げるが、
**確認済みとは呼ばない。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

#: RFC 2142 が定めるセキュリティ・運用の窓口。存在は確認できない
RFC2142_ALIASES = ("security", "abuse", "postmaster")

#: security.txt の置き場所（RFC 9116）。well-known が正で、直下は移行措置
SECURITY_TXT_PATHS = (
    "/.well-known/security.txt",
    "/security.txt",
)

#: 経路の信頼度。**security.txt だけが「その組織が公示した窓口」である。**
#: 残りは規約上あるはずというだけで、実在の保証がない
SOURCE_CONFIDENCE = {
    "security_txt": "high",
    "rdap_abuse": "medium",
    "rfc2142": "low",
}

TIMEOUT_SEC = 10.0
#: security.txt の取得は 1 ドメイン 1 リクエストに抑える。
#: 相手のサーバに繰り返し当てない
MAX_REDIRECTS = 3

_CONTACT_RE = re.compile(r"^Contact:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_EXPIRES_RE = re.compile(r"^Expires:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_MAILTO_RE = re.compile(r"^mailto:(.+)$", re.IGNORECASE)


@dataclass
class ContactCandidate:
    """通知先の候補1件。

    **`verified` は常に False である。** SMTP で存在確認するのは迷惑行為で、
    実際に送ってみるまで届くかは分からない。列を持っているのは、将来
    バウンス結果を書き戻せるようにするため。
    """

    address: str
    source: str
    confidence: str
    verified: bool = False
    note: str | None = None


@dataclass
class ContactSet:
    domain: str
    candidates: list[ContactCandidate] = field(default_factory=list)
    security_txt_found: bool = False
    security_txt_expired: bool | None = None
    #: security.txt が公示している非メールの窓口（フォーム等）。
    #: **自動送信の対象にはしないが、捨てない。** 組織が「ここへ出せ」と
    #: 公示している先であり、代わりに `security@` へ送るのは公示に反する
    web_contacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> ContactCandidate | None:
        """最も信頼できる候補。security.txt があればそれを使う。"""
        order = {"high": 0, "medium": 1, "low": 2}
        if not self.candidates:
            return None
        return min(self.candidates, key=lambda c: (order.get(c.confidence, 9), c.address))

    @property
    def prefers_web(self) -> bool:
        """公示された窓口がメール以外だけである。

        実例として cloudflare.com の security.txt は `Contact:` が
        HackerOne と自社フォームの2件で、mailto が無い。この状態で
        `security@cloudflare.com` に送るのは、**公示された窓口を無視して
        別の口を叩く**ことになる。自動送信の対象から外す判断に使う。
        """
        if not self.web_contacts:
            return False
        return not any(c.source == "security_txt" for c in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "security_txt_found": self.security_txt_found,
            "security_txt_expired": self.security_txt_expired,
            "web_contacts": list(self.web_contacts),
            "prefers_web": self.prefers_web,
            "candidates": [
                {
                    "address": c.address,
                    "source": c.source,
                    "confidence": c.confidence,
                    "verified": c.verified,
                    "note": c.note,
                }
                for c in self.candidates
            ],
            "notes": self.notes,
        }


def parse_security_txt(text: str) -> tuple[list[str], list[str], str | None]:
    """security.txt から Contact と Expires を読む（RFC 9116）。

    戻り値は (mailto の宛先, 非メールの窓口 URL, Expires)。

    自動送信の対象にするのは `mailto:` だけである。ただし**非メールの窓口を
    捨てない。** RFC 9116 は Contact の優先順位を記載順で表すと定めており、
    フォームしか公示していない組織は「メールではなくここへ出せ」と言っている。
    捨てると、その意思を無視して `security@` に送ることになる。
    """
    addresses: list[str] = []
    web: list[str] = []
    for value in _CONTACT_RE.findall(text):
        value = value.strip()
        match = _MAILTO_RE.match(value)
        if match:
            address = match.group(1).strip()
            if address and address not in addresses:
                addresses.append(address)
        elif value.lower().startswith(("https:", "http:")) and value not in web:
            web.append(value)
    expires = _EXPIRES_RE.search(text)
    return addresses, web, (expires.group(1).strip() if expires else None)


def fetch_security_txt(
    domain: str, *, client: httpx.Client | None = None
) -> tuple[str | None, str | None]:
    """security.txt を取る。戻り値は (本文, 使ったパス)。

    **取れなくても例外にしない。** 4% しか置いていないので、無いのが通常である。
    """
    owned = client is None
    c = client or httpx.Client(
        timeout=TIMEOUT_SEC,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": "mailauth-observatory/0.1 (security notification prep)"},
    )
    try:
        for path in SECURITY_TXT_PATHS:
            try:
                resp = c.get(f"https://{domain}{path}")
            except httpx.HTTPError:
                continue
            if resp.status_code == 200 and "Contact:" in resp.text:
                return resp.text, path
        return None, None
    finally:
        if owned:
            c.close()


def discover(
    domain: str,
    *,
    client: httpx.Client | None = None,
    fetch_https: bool = True,
    rdap_abuse: str | None = None,
) -> ContactSet:
    """1ドメインの通知先候補を集める。

    `fetch_https=False` にすると HTTPS を叩かない。その場合 security.txt は
    「無い」ではなく「見ていない」として記録する（原則5）。
    """
    result = ContactSet(domain=domain)

    if fetch_https:
        text, path = fetch_security_txt(domain, client=client)
        if text:
            result.security_txt_found = True
            addresses, web, expires = parse_security_txt(text)
            result.web_contacts = web
            for address in addresses:
                result.candidates.append(
                    ContactCandidate(
                        address=address,
                        source="security_txt",
                        confidence=SOURCE_CONFIDENCE["security_txt"],
                        note=f"{path} に公示されている窓口",
                    )
                )
            if expires:
                result.notes.append(f"security.txt の Expires: {expires}")
            if not addresses:
                result.notes.append(
                    "security.txt はあるが mailto: の Contact が無い。"
                    f"公示されている窓口は {', '.join(web) or '（URL も読めない）'}。"
                    "**この組織はメール以外の窓口を指定している。**"
                    "自動送信の対象にはせず、人が手でこの窓口に出すこと"
                )
        else:
            result.notes.append(
                "security.txt が無い。Fortune 500 でも普及率は約4%なので"
                "これが通常の状態である"
            )
    else:
        result.notes.append(
            "HTTPS を叩いていないため security.txt を見ていない。"
            "「無い」のではなく「確認していない」"
        )

    if rdap_abuse:
        result.candidates.append(
            ContactCandidate(
                address=rdap_abuse,
                source="rdap_abuse",
                confidence=SOURCE_CONFIDENCE["rdap_abuse"],
                note="レジストラの abuse 窓口。組織の窓口ではない",
            )
        )

    for alias in RFC2142_ALIASES:
        result.candidates.append(
            ContactCandidate(
                address=f"{alias}@{domain}",
                source="rfc2142",
                confidence=SOURCE_CONFIDENCE["rfc2142"],
                note=(
                    "RFC 2142 が定める窓口。**存在は確認していない。** "
                    "Top 1M でもバウンスしない割合は 24.16% にとどまる"
                ),
            )
        )

    return result
