"""計測バックエンドの抽象（DESIGN.md P4「バックエンドの抽象化」）。

**複数手法を比較できることが要件**である。`--method` で切り替え、
`--compare zdns,dnspython` で同一対象を複数手法で引いて差分を出せるようにする。

| バックエンド | 用途 |
|---|---|
| zdns | 主力。Apache-2.0。JSON Lines で生保存 |
| dnsx | 補助・クロスチェック |
| dnspython | 検証・少量。例外分岐が細かく制御しやすい |
| securitytrails | 過去データのみ。継続計測には使わない |
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

from ...contracts import SCHEMA_VERSION, DnssecFlags, RawResponse
from ...contracts import DnsAnswer as ContractAnswer
from ...resolver import DnsAnswer
from ..plan import Query


class MeasureBackend(Protocol):
    name: str
    version: str

    def query(self, query: Query) -> DnsAnswer: ...


def to_raw_response(
    query: Query,
    answer: DnsAnswer,
    *,
    run_id: str,
    domain: str,
    method: str,
    tool_version: str,
    resolver_label: str,
    ts: dt.datetime | None = None,
) -> RawResponse:
    """バックエンドの応答を bronze の1行に変換する。

    分割された TXT は **連結せずに character-string の配列のまま保存する**。
    連結は P5 の責務。生データの忠実性を優先する（原則1）。
    """
    answers: list[ContractAnswer] = []
    if query.rtype.upper() == "TXT":
        for chunks in answer.txt_strings:
            answers.append(ContractAnswer(type="TXT", data=chunks))
        if not answer.txt_strings:
            for value in answer.values:
                answers.append(ContractAnswer(type="TXT", data=[value]))
    else:
        for value in answer.values:
            answers.append(ContractAnswer(type=query.rtype.upper(), data=value))

    return RawResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        ts=ts or dt.datetime.now(dt.UTC).replace(microsecond=0),
        domain=domain,
        query_name=query.name,
        query_type=query.rtype.upper(),
        purpose=query.purpose,
        method=method,
        resolver=resolver_label,
        protocol="tcp" if answer.used_tcp else "udp",
        rcode=answer.rcode,
        observed=answer.observed,
        record_present=answer.record_present,
        answers=answers,
        dnssec=DnssecFlags(
            do=True if answer.authenticated_data is not None else None,
            ad=answer.authenticated_data,
            rrsig_present=None,
        ),
        retries=0,
        duration_ms=answer.duration_ms,
        tool=method,
        tool_version=tool_version,
        error=answer.error,
    )
