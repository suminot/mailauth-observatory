"""dnspython バックエンド。

DESIGN.md は zdns を主力としているが、zdns は外部バイナリで環境に無いことも
ある。dnspython は依存として常に入っているので、これを既定にしてある。
例外分岐が細かく制御でき、原則5（observed と record_present の区別）を
最も忠実に実装できるのも利点。
"""

from __future__ import annotations

import dns.version

from ...resolver import DnsAnswer, DnsResolver
from ..plan import Query


class DnspythonBackend:
    name = "dnspython"

    def __init__(self, resolver: DnsResolver | None = None, **kwargs) -> None:
        self.resolver = resolver or DnsResolver(**kwargs)
        self.version = dns.version.version

    @property
    def resolver_label(self) -> str:
        servers = ",".join(self.resolver.nameservers[:3])
        return f"dnspython->{servers}"

    def query(self, query: Query) -> DnsAnswer:
        return self.resolver.query(query.name, query.rtype)

    @property
    def stats(self) -> dict[str, int]:
        return self.resolver.stats
