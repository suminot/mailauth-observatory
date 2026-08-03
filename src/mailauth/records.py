"""SPF / DMARC / MX レコードの最小限の解析。

**本格的な解釈は P5 の責務**である（10ルックアップ制限、void lookup、
Tree Walk、二重計算など）。ここにあるのは P2 と P3 が候補展開と
一次実証のために必要とする最小限だけ。

ここに仕様の解釈を足していくと P5 と二重管理になる。迷ったら P5 に置くこと。
"""

from __future__ import annotations

import re

SPF_VERSION = "v=spf1"
DMARC_VERSION = "v=DMARC1"


def join_txt_strings(strings: list[str]) -> str:
    """分割された TXT の character-string を順序どおり連結する。

    1つの TXT レコードは複数の character-string（各最大255オクテット、
    RFC 1035 §3.3）に分割されうる。連結せずに解析すると、255バイトを
    超える SPF や 2048bit の DKIM 公開鍵を取りこぼす。
    """
    return "".join(strings)


def is_spf_record(txt: str) -> bool:
    """SPF レコードか。

    バージョンセクションは正確に "v=spf1" で、SP または終端で終わる
    （RFC 7208 §4.5）。"v=spf10" は SPF ではない。
    """
    if not txt:
        return False
    lowered = txt.strip().lower()
    if not lowered.startswith(SPF_VERSION):
        return False
    rest = lowered[len(SPF_VERSION) :]
    return rest == "" or rest[0] in " \t"


def find_spf_records(txts: list[str]) -> list[str]:
    """TXT の集合から SPF レコードだけを取り出す。

    2件以上あれば PermError（RFC 7208 §4.5）だが、その判定は P5 の責務。
    ここでは見つかったものをそのまま返す。
    """
    return [t for t in txts if is_spf_record(t)]


def spf_terms(txt: str) -> list[str]:
    """SPF のメカニズム・修飾子を空白で分割して返す（バージョンを除く）。"""
    if not is_spf_record(txt):
        return []
    parts = txt.strip().split()
    return parts[1:]


def spf_all_qualifier(txt: str) -> str | None:
    """`all` の修飾子（- ~ ? +）を返す。`all` が無ければ None。

    修飾子の省略は `+` を意味する（RFC 7208 §4.6.2）。
    """
    for term in spf_terms(txt):
        lowered = term.lower()
        if lowered == "all":
            return "+"
        if len(lowered) > 3 and lowered[1:] == "all" and lowered[0] in "+-~?":
            return lowered[0]
    return None


def spf_has_all(txt: str) -> bool:
    return spf_all_qualifier(txt) is not None


def spf_includes(txt: str) -> list[str]:
    """include: の対象ドメインを返す。"""
    out: list[str] = []
    for term in spf_terms(txt):
        if term.lower().startswith("include:"):
            value = term.split(":", 1)[1].strip()
            if value:
                out.append(value.rstrip(".").lower())
    return out


def spf_redirect(txt: str) -> str | None:
    """redirect= の対象ドメインを返す。

    **`all` がレコードに存在する場合、redirect は無視される**
    （RFC 7208 §6.1）。"... -all redirect=..." では -all が優先され
    redirect は死ぬので、その場合は None を返す。
    """
    if spf_has_all(txt):
        return None
    for term in spf_terms(txt):
        if term.lower().startswith("redirect="):
            value = term.split("=", 1)[1].strip()
            if value:
                return value.rstrip(".").lower()
    return None


def spf_mechanism_domains(txt: str) -> list[str]:
    """a: / mx: / exists: で明示されたドメインを返す。

    さくらインターネットのように専用 include を持たず
    `a:wwwNNNN.sakura.ne.jp` 形式で表現する事業者があるため、
    include だけを見ると取りこぼす（DESIGN.md P6 実装メモ）。
    """
    out: list[str] = []
    for term in spf_terms(txt):
        lowered = term.lower().lstrip("+-~?")
        for prefix in ("a:", "mx:", "exists:"):
            if lowered.startswith(prefix):
                value = lowered.split(":", 1)[1].split("/")[0].strip()
                if value:
                    out.append(value.rstrip("."))
    return out


#: Valimail の動的SPF。マクロを含むため静的にルックアップ数を数えても無意味
_DYNAMIC_SPF_RE = re.compile(r"%\{[a-z]\}", re.IGNORECASE)


def spf_is_dynamic(txt: str) -> bool:
    return bool(_DYNAMIC_SPF_RE.search(txt or ""))


# --------------------------------------------------------------------------
# DMARC
# --------------------------------------------------------------------------


def is_dmarc_record(txt: str) -> bool:
    """DMARC レコードか。

    タグ名は case-insensitive だが `v=DMARC1` の値のみ
    case-sensitive で厳密一致（RFC 9989 §4.7）。
    """
    if not txt:
        return False
    stripped = txt.strip()
    if not stripped.lower().startswith("v="):
        return False
    first = stripped.split(";", 1)[0].strip()
    name, _, value = first.partition("=")
    return name.strip().lower() == "v" and value.strip() == "DMARC1"


def find_dmarc_records(txts: list[str]) -> list[str]:
    return [t for t in txts if is_dmarc_record(t)]


def dmarc_tags(txt: str) -> dict[str, str]:
    """DMARC のタグを辞書にする。

    重複タグは最初の出現を採る（P5 が `has_duplicate_tag` を立てる）。
    未知タグも捨てずに保持する。round-trip のため。
    """
    out: dict[str, str] = {}
    for part in (txt or "").split(";"):
        name, sep, value = part.partition("=")
        if not sep:
            continue
        key = name.strip().lower()
        if key and key not in out:
            out[key] = value.strip()
    return out


def _addr_domain(uri: str) -> str | None:
    """`mailto:x@example.com!10m` からドメイン部を取り出す。"""
    value = uri.strip()
    if not value:
        return None
    if value.lower().startswith("mailto:"):
        value = value[len("mailto:") :]
    value = value.split("!", 1)[0]  # サイズ指定を落とす
    if "@" not in value:
        return None
    domain = value.rsplit("@", 1)[1].strip().rstrip(".").lower()
    return domain or None


def dmarc_report_domains(txt: str, tag: str = "rua") -> list[str]:
    """rua / ruf の宛先ドメインを返す。

    メールアドレスのローカル部は保存しない。個人情報を集めないという
    非目的（DESIGN.md 1.2）に触れるため、ドメインだけを取り出す。
    """
    raw = dmarc_tags(txt).get(tag, "")
    out: list[str] = []
    for uri in raw.split(","):
        domain = _addr_domain(uri)
        if domain and domain not in out:
            out.append(domain)
    return out


# --------------------------------------------------------------------------
# MX
# --------------------------------------------------------------------------


def is_null_mx(mx_exchanges: list[str]) -> bool:
    """RFC 7505 の Null MX（優先度0、交換ホストがルート `.`）か。

    「メールを受け取らない」の明示的な宣言であり、設定漏れとは違う。
    """
    if len(mx_exchanges) != 1:
        return False
    return mx_exchanges[0].strip() in (".", "")


def mx_hosts(mx_exchanges: list[str]) -> list[str]:
    """Null MX を除いた実際の MX ホスト名。"""
    out = []
    for host in mx_exchanges:
        normalized = host.strip().rstrip(".").lower()
        if normalized:
            out.append(normalized)
    return out
