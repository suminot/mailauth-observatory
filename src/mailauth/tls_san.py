"""公式サイトが実際に出している TLS 証明書の SAN から、兄弟ドメインを拾う。

P2 の CT 経路（crt.sh / CertSpotter）が探しているのは「起点ドメインと同じ
証明書に載っている別のドメイン」である。証明書の発行時に、申請者は SAN の
すべての名前を管理していることを CA に示している ── だから同じ証明書に
載ったドメインは、同じ持ち主のものと読める。

**同じ手がかりは、相手のサイトに1回つなげば取れる。** CT 検索サービスは
2026-09 から毎月のように落ちていて（CT_UPSTREAM_UNAVAILABLE）、こちらの都合で
速くできない（BACKLOG 12）。証明書そのものは、その会社のサーバーが誰にでも
見せているものである。

  - 見るのは `https://<起点>/` と `https://www.<起点>/` の証明書だけ
    （1社あたり最大2回の接続。HTTP の中身は読まない）
  - **検証に通った証明書だけを使う。** 起点ドメインの名前で検証できない
    証明書（共用レンタルサーバーの証明書など）は、その会社のものとは言えない
  - **共用の証明書を捨てる。** 1枚に無関係な会社のドメインを数十載せた証明書
    がある（CDN の共用 SAN 証明書）。登録ドメインが `SHARED_CERT_LIMIT` を
    超える証明書からは何も拾わない
  - CT と違って**いま出している1枚**しか見えない。CT の代わりではなく、CT が
    落ちている月にも最低限の手がかりを残すための経路である
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .normalize import etld_plus_one
from .ordered import in_input_order

#: これを超える数の登録ドメインを載せた証明書は、共用とみなして使わない。
#: 自社のブランドドメインを1枚にまとめる会社でも、10 を超えることはまれ
SHARED_CERT_LIMIT = 10
DEFAULT_TIMEOUT = 5.0
DEFAULT_CONCURRENCY = 16


@dataclass
class SanResult:
    domain: str
    #: 証明書を取れたホスト（起点そのもの か www.起点）
    host: str | None = None
    #: SAN の DNS 名（小文字、末尾の点なし）
    names: list[str] = field(default_factory=list)
    #: SAN から取り出した登録ドメイン（起点を含む）
    registrable: list[str] = field(default_factory=list)
    #: 共用の証明書とみなして捨てた
    shared: bool = False
    error_kind: str | None = None

    @property
    def usable(self) -> list[str]:
        """候補にしてよい登録ドメイン。共用なら空。"""
        return [] if self.shared else self.registrable


def registrable_names(names: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for name in names:
        host = name.strip().lower().rstrip(".").removeprefix("*.")
        if not host or " " in host:
            continue
        apex = etld_plus_one(host)
        if apex:
            seen.setdefault(apex, None)
    return sorted(seen)


def interpret(domain: str, host: str, names: list[str]) -> SanResult:
    registrable = registrable_names(names)
    return SanResult(
        domain=domain,
        host=host,
        names=sorted({n.lower().rstrip(".") for n in names}),
        registrable=registrable,
        shared=len(registrable) > SHARED_CERT_LIMIT,
    )


def fetch_names(host: str, timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """`host:443` の証明書の SAN（DNS 名）を返す。**検証に通らなければ例外。**"""
    context = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert() or {}
    return [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]


class TlsSanSource:
    """本番用。`fetch` を差し替えられる（検査はネットワークに出ない）。"""

    def __init__(
        self,
        fetch: Callable[[str], list[str]] | None = None,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._fetch = fetch or (lambda host: fetch_names(host, timeout=timeout))
        self.concurrency = max(int(concurrency), 1)

    def lookup(self, domain: str) -> SanResult:
        last_error = "connect"
        for host in (domain, f"www.{domain}"):
            try:
                names = self._fetch(host)
            except ssl.SSLCertVerificationError:
                last_error = "verify"
                continue
            except TimeoutError:
                last_error = "timeout"
                continue
            except (OSError, ssl.SSLError, ValueError):
                last_error = "connect"
                continue
            if names:
                return interpret(domain, host, names)
            last_error = "no_san"
        return SanResult(domain=domain, error_kind=last_error)

    def prefetch(self, domains: Iterable[str]) -> dict[str, SanResult]:
        ordered = list(dict.fromkeys(d for d in domains if d))
        return {
            result.domain: result
            for result in in_input_order(
                self.lookup, ordered, concurrency=self.concurrency, window=256
            )
        }


class DisabledTlsSanSource:
    """引かない。offline や discovery.tls_san: false のとき。"""

    concurrency = 1

    def lookup(self, domain: str) -> SanResult:
        return SanResult(domain=domain, error_kind="disabled")

    def prefetch(self, domains: Iterable[str]) -> dict[str, SanResult]:
        return {}
