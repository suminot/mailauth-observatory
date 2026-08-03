"""P2/P3 のための軽量な DNS 問い合わせ。

**本格計測は P4** である。ここは候補展開と一次実証のための最小限で、
バックエンドのプラガブル化やクロスチェックは P4 の責務。

守っていること
  - 原則5: `observed`（クエリ自体が成功したか）と
    `record_present`（レコードが存在したか）を必ず分ける
  - NXDOMAIN と NODATA はリトライしない。一時的失敗ではなく確定結果だから
  - 同じ名前を二度引かない（キャッシュ）。大手プロバイダの権威DNSに
    不当な負荷をかけないための倫理的義務でもある
  - レート制御。既定は控えめにしてある
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Protocol

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset


@dataclass
class DnsAnswer:
    """1回の問い合わせの結果。原則5をそのまま型にしたもの。"""

    name: str
    rtype: str
    #: クエリ自体が成功したか。False は「取れなかった」
    observed: bool
    #: レコードが存在したか。observed=False のときは None（不明）
    record_present: bool | None
    rcode: str
    values: list[str] = field(default_factory=list)
    #: TXT は character-string の配列のまま持つ。連結は呼び出し側の判断
    txt_strings: list[list[str]] = field(default_factory=list)
    error: str | None = None
    duration_ms: int | None = None
    #: DNSSEC の AD フラグ。上流リゾルバが検証済みと示したか。
    #: DO を立てて引いた場合のみ意味を持つ。取れなければ None
    authenticated_data: bool | None = None
    #: 応答が truncated だったため TCP に切り替えたか
    used_tcp: bool = False
    #: 辿った CNAME の先。DKIM セレクタの委譲先を知るために保存する。
    #: `selector._domainkey.example.com CNAME selector.example.dkim.amazonses.com`
    #: のような委譲は **最も強い推定証拠** になる（DESIGN.md P6 二段推定）。
    #: 値だけ見ていると委譲先が分からず、P6 の DKIM_CNAME 照合ができない
    cname_chain: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return not self.observed


class Resolver(Protocol):
    def query(self, name: str, rtype: str) -> DnsAnswer: ...


#: リトライしない rcode。確定結果であって一時的失敗ではない。
#: DKIMセレクタ探索では NODATA が正常系の大半を占めるため、
#: ここを間違えるとクエリ量が数倍に膨らむ。
TERMINAL_RCODES = {"NOERROR", "NXDOMAIN", "NODATA"}


class DnsResolver:
    """dnspython による実装。キャッシュとレート制御つき。"""

    def __init__(
        self,
        nameservers: list[str] | None = None,
        *,
        timeout: float = 5.0,
        tcp_timeout: float | None = None,
        retries: int = 2,
        backoff: tuple[float, ...] = (1.0, 3.0),
        qps: float = 20.0,
        cache: bool = True,
        want_dnssec: bool = True,
    ) -> None:
        system = dns.resolver.Resolver(configure=True)
        self.nameservers = [str(n) for n in (nameservers or system.nameservers)]
        self.timeout = timeout
        #: TCP は UDP が truncated だったときだけ使う。詰まりやすいので別に持つ
        self.tcp_timeout = tcp_timeout if tcp_timeout is not None else timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        #: DO を立てて引く。DNSSEC の観測は他のクエリに相乗りできる
        self.want_dnssec = want_dnssec
        self._cache: dict[tuple[str, str], DnsAnswer] | None = {} if cache else None
        self._last_call = 0.0
        self.stats: dict[str, int] = {
            "queries": 0,
            "cache_hits": 0,
            "retries": 0,
            "failures": 0,
            # DESIGN.md P4 が要求するメトリクス
            "tcp_fallback": 0,
            "tcp_failed": 0,
        }

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def query(self, name: str, rtype: str) -> DnsAnswer:
        key = (name.rstrip(".").lower(), rtype.upper())
        if self._cache is not None and key in self._cache:
            self.stats["cache_hits"] += 1
            return self._cache[key]

        answer = self._query_uncached(key[0], key[1])
        if self._cache is not None:
            self._cache[key] = answer
        return answer

    def _query_uncached(self, name: str, rtype: str) -> DnsAnswer:
        """UDP で引き、truncated なら TCP に切り替える。

        DESIGN.md P4 のステートマシンに沿っている。
          NOERROR(answers>0)        -> observed=true,  record_present=true
          NOERROR(answers=0/NODATA) -> observed=true,  record_present=false  リトライ不要
          NXDOMAIN                  -> observed=true,  record_present=false
          TC=1                      -> TCP で再問い合わせ
          SERVFAIL/REFUSED/TIMEOUT  -> backoff してリトライ、尽きたら次のリゾルバ
          全リゾルバ枯渇             -> observed=false, record_present=null
        """
        started = time.monotonic()
        rdtype = dns.rdatatype.from_text(rtype)
        last_error: str | None = None
        last_rcode = "TIMEOUT"

        for nameserver in self.nameservers or ["127.0.0.1"]:
            attempt = 0
            while True:
                self._throttle()
                self.stats["queries"] += 1
                query = dns.message.make_query(name, rdtype, want_dnssec=self.want_dnssec)
                try:
                    resp = dns.query.udp(query, nameserver, timeout=self.timeout)
                except dns.exception.Timeout as exc:
                    last_rcode, last_error = "TIMEOUT", str(exc)[:200]
                except OSError as exc:
                    last_rcode, last_error = "NETWORK", str(exc)[:200]
                except dns.exception.DNSException as exc:
                    last_rcode, last_error = "ERROR", f"{type(exc).__name__}: {exc}"[:200]
                else:
                    used_tcp = False
                    if resp.flags & dns.flags.TC:
                        # 512バイトを超える応答。TXT が多いドメインで普通に起きる
                        self.stats["tcp_fallback"] += 1
                        try:
                            resp = dns.query.tcp(query, nameserver, timeout=self.tcp_timeout)
                            used_tcp = True
                        except (dns.exception.DNSException, OSError) as exc:
                            self.stats["tcp_failed"] += 1
                            self.stats["failures"] += 1
                            return DnsAnswer(
                                name=name,
                                rtype=rtype,
                                observed=False,
                                record_present=None,
                                rcode="TRUNCATED_TCP_UNAVAILABLE",
                                error=(
                                    "UDP応答が truncated で TCP/53 に切り替えたが失敗した。"
                                    f"経路が TCP/53 を通していない可能性がある: {exc}"
                                )[:300],
                                duration_ms=_ms(started),
                            )

                    return self._from_response(
                        name, rtype, rdtype, resp, started, used_tcp=used_tcp
                    )

                # ここに来たのは一時的失敗。リトライするか次のリゾルバへ
                if attempt < self.retries:
                    delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                    attempt += 1
                    self.stats["retries"] += 1
                    time.sleep(delay)
                    continue
                break

        self.stats["failures"] += 1
        return DnsAnswer(
            name=name,
            rtype=rtype,
            observed=False,
            record_present=None,
            rcode=last_rcode,
            error=last_error,
            duration_ms=_ms(started),
        )

    def _from_response(
        self, name: str, rtype: str, rdtype: int, resp, started: float, used_tcp: bool = False
    ) -> DnsAnswer:
        rcode = dns.rcode.to_text(resp.rcode())
        ad = bool(resp.flags & dns.flags.AD) if self.want_dnssec else None
        if rcode == "NXDOMAIN":
            return DnsAnswer(
                name=name,
                rtype=rtype,
                observed=True,
                record_present=False,
                rcode="NXDOMAIN",
                duration_ms=_ms(started),
                authenticated_data=ad,
                used_tcp=used_tcp,
            )
        if rcode not in ("NOERROR",):
            # SERVFAIL / REFUSED は「取れなかった」。無かったのではない
            return DnsAnswer(
                name=name,
                rtype=rtype,
                observed=False,
                record_present=None,
                rcode=rcode,
                duration_ms=_ms(started),
            )

        values: list[str] = []
        txt_strings: list[list[str]] = []
        cname_chain: list[str] = []
        for rrset in resp.answer:
            if rrset.rdtype == dns.rdatatype.CNAME and rdtype != dns.rdatatype.CNAME:
                # 辿った途中の CNAME。record_present には数えないが捨てない。
                # DKIM の委譲先はここにしか現れない
                for rdata in rrset:
                    cname_chain.append(str(rdata.target).rstrip(".").lower())
                continue
            if rrset.rdtype != rdtype:
                continue  # 求めた型でも CNAME でもない rrset は数えない
            for rdata in rrset:
                if rtype == "TXT":
                    chunks = [b.decode("utf-8", errors="replace") for b in rdata.strings]
                    txt_strings.append(chunks)
                    values.append("".join(chunks))
                elif rtype == "MX":
                    values.append(str(rdata.exchange))
                else:
                    values.append(rdata.to_text())

        return DnsAnswer(
            name=name,
            rtype=rtype,
            observed=True,
            record_present=len(values) > 0,
            rcode="NOERROR" if values else "NODATA",
            values=values,
            txt_strings=txt_strings,
            cname_chain=cname_chain,
            duration_ms=_ms(started),
            authenticated_data=ad,
            used_tcp=used_tcp,
        )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class StaticResolver:
    """テストと再現のための固定応答リゾルバ。

    `{(name, rtype): DnsAnswer}` を渡す。未登録の名前は NODATA を返す
    （「レコードが無い」であって「取れなかった」ではない）。
    テストが実際の DNS に出ないための仕組みで、本番では使わない。
    """

    def __init__(self, answers: dict[tuple[str, str], DnsAnswer] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, str]] = []

    def query(self, name: str, rtype: str) -> DnsAnswer:
        key = (name.rstrip(".").lower(), rtype.upper())
        self.calls.append(key)
        hit = self.answers.get(key)
        if hit is not None:
            return hit
        return DnsAnswer(
            name=key[0], rtype=key[1], observed=True, record_present=False, rcode="NODATA"
        )


def make_answer(
    name: str,
    rtype: str,
    values: list[str] | None = None,
    *,
    observed: bool = True,
    rcode: str | None = None,
    cname_chain: list[str] | None = None,
) -> DnsAnswer:
    """テスト用のヘルパ。"""
    vals = values or []
    return DnsAnswer(
        cname_chain=cname_chain or [],
        name=name.rstrip(".").lower(),
        rtype=rtype.upper(),
        observed=observed,
        record_present=(len(vals) > 0) if observed else None,
        rcode=rcode or ("NOERROR" if vals else "NODATA"),
        values=vals,
        txt_strings=[[v] for v in vals] if rtype.upper() == "TXT" else [],
    )


def shuffled(items: list[str], seed: int | None = None) -> list[str]:
    """ドメイン順をシャッフルする。

    同一権威DNSへの連続クエリを避けるための作法（DESIGN.md P4 実装メモ）。
    seed を渡せば再現可能。
    """
    out = list(items)
    random.Random(seed).shuffle(out)
    return out
