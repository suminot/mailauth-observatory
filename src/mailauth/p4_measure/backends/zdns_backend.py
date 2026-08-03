"""zdns バックエンド（DESIGN.md P4 の主力）。

Apache-2.0。JSON Lines で status / answers / TTL / authorities / protocol を
生のまま出せるのが利点で、大量計測の速度も dnspython より桁が違う。

外部バイナリなので環境に無いことがある。無い場合は理由を添えて止め、
黙って別のバックエンドに落ちない。どの手法で測ったかは
成果物の意味を変えるため、暗黙に差し替えてはいけない。
"""

from __future__ import annotations

import json
import shutil
import subprocess

from ...resolver import DnsAnswer
from ..plan import Query


class ZdnsNotAvailableError(RuntimeError):
    pass


class ZdnsBackend:
    name = "zdns"

    def __init__(
        self,
        binary: str = "zdns",
        *,
        nameservers: list[str] | None = None,
        timeout: float = 5.0,
        threads: int = 80,
    ) -> None:
        self.binary = shutil.which(binary) or ""
        if not self.binary:
            raise ZdnsNotAvailableError(
                f"{binary} が見つかりません。"
                "github.com/zmap/zdns から入れるか --method dnspython を使ってください"
            )
        self.nameservers = nameservers or []
        self.timeout = timeout
        self.threads = threads
        self.version = self._detect_version()
        self.stats: dict[str, int] = {"queries": 0, "failures": 0}

    def _detect_version(self) -> str:
        try:
            out = subprocess.run(
                [self.binary, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
            return (out.stdout or out.stderr).strip().splitlines()[0][:40] or "unknown"
        except (OSError, subprocess.SubprocessError, IndexError):
            return "unknown"

    @property
    def resolver_label(self) -> str:
        return f"zdns->{','.join(self.nameservers[:3]) or 'default'}"

    def query(self, query: Query) -> DnsAnswer:
        """1本ずつ引く実装。

        zdns の真価は名前をまとめて流し込む一括処理にあるので、
        大量計測時は `query_many` を使うこと。ここは互換のための単発版。
        """
        results = self.query_many([query])
        return results[0]

    def query_many(self, queries: list[Query]) -> list[DnsAnswer]:
        if not queries:
            return []
        rtypes = {q.rtype.upper() for q in queries}
        if len(rtypes) != 1:
            # zdns はモジュール単位で1レコード型を扱う
            out: list[DnsAnswer] = []
            for rtype in sorted(rtypes):
                subset = [q for q in queries if q.rtype.upper() == rtype]
                out.extend(self.query_many(subset))
            return out

        rtype = rtypes.pop()
        argv = [self.binary, rtype.lower(), "--threads", str(self.threads)]
        if self.nameservers:
            argv += ["--name-servers", ",".join(self.nameservers)]

        names = "\n".join(q.name for q in queries)
        self.stats["queries"] += len(queries)
        try:
            proc = subprocess.run(
                argv,
                input=names,
                capture_output=True,
                text=True,
                timeout=max(60, self.timeout * len(queries)),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.stats["failures"] += len(queries)
            return [
                DnsAnswer(
                    name=q.name,
                    rtype=rtype,
                    observed=False,
                    record_present=None,
                    rcode="ERROR",
                    error=f"zdns の起動に失敗: {exc}"[:200],
                )
                for q in queries
            ]

        by_name: dict[str, DnsAnswer] = {}
        for line in (proc.stdout or "").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            answer = self._parse_row(row, rtype)
            by_name[answer.name] = answer

        results = []
        for q in queries:
            key = q.name.rstrip(".").lower()
            hit = by_name.get(key)
            if hit is None:
                self.stats["failures"] += 1
                hit = DnsAnswer(
                    name=key,
                    rtype=rtype,
                    observed=False,
                    record_present=None,
                    rcode="NO_OUTPUT",
                    error="zdns が該当名の行を出力しなかった",
                )
            results.append(hit)
        return results

    @staticmethod
    def _parse_row(row: dict, rtype: str) -> DnsAnswer:
        name = str(row.get("name", "")).rstrip(".").lower()
        status = str(row.get("status", "")).upper()
        data = row.get("data") or {}
        answers = data.get("answers") or []

        values: list[str] = []
        txt_strings: list[list[str]] = []
        cname_chain: list[str] = []
        for a in answers:
            atype = str(a.get("type", "")).upper()
            value = a.get("answer")
            if value is None:
                continue
            if atype == "CNAME" and rtype != "CNAME":
                # 辿った途中の CNAME。DKIM の委譲先はここにしか現れない
                cname_chain.append(str(value).rstrip(".").lower())
                continue
            if atype != rtype:
                continue
            if rtype == "TXT":
                txt_strings.append([str(value)])
            values.append(str(value))

        # zdns の status を rcode に写す。NOERROR/NXDOMAIN 以外は「取れなかった」
        observed = status in ("NOERROR", "NXDOMAIN", "NODATA")
        return DnsAnswer(
            name=name,
            rtype=rtype,
            observed=observed,
            record_present=(len(values) > 0) if observed else None,
            rcode=status or "UNKNOWN",
            values=values,
            txt_strings=txt_strings,
            cname_chain=cname_chain,
            error=None if observed else str(row.get("error") or status)[:200],
            duration_ms=int(float(row.get("duration", 0)) * 1000) or None,
        )
