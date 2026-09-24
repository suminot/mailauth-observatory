"""Wikidata から企業リストを再構築する（DESIGN.md P1 / Sprint 1.5）。

**fortune.com はスクレイピングも二次利用も規約で禁止**されている。会社名の
リストは CC0 のソースから作り直す。Fortune の編集著作物である順位と売上高は
取得もしないし成果物にも含めない（`constraints.exclude_fields`）。

Wikidata Query Service の作法
  - User-Agent は必須。連絡先を含める（未設定だと 403 で弾かれる）
  - POST で投げる。SPARQL は長くなり GET の URL 長制限に当たる
  - タイムアウトは60秒。重いクエリは 500 で返る
  - 失敗したら黙って空を返さない。**「取れなかった」を上に伝える**
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..paths import config_path

ENDPOINT = "https://query.wikidata.org/sparql"
#: 連絡先を含めるのが WDQS の作法。無いと弾かれる
USER_AGENT = "mailauth-observatory/0.1 (https://github.com/suminot/mailauth-observatory)"
TIMEOUT_SEC = 60.0


class WikidataError(RuntimeError):
    """取得に失敗した。**空リストを返して「0社だった」と誤解させない。**"""


@dataclass
class ForeignRow:
    """Wikidata から得た1社分。EDINET の行と同じ役割。

    国内側と違い、identity の主キーは LEI である。SEC EDGAR は米国提出者
    しかカバーしないので、Global 500 のように多国籍の母集団では CIK を
    主キーにできない（DESIGN.md configs/populations/global500.yaml）。
    """

    qid: str
    name: str
    name_en: str | None = None
    lei: str | None = None
    cik: str | None = None
    website: str | None = None
    ticker: str | None = None
    country: str | None = None
    #: SIC は SEC EDGAR から後で付ける
    sic: str | None = None
    sic_label: str | None = None
    notes: list[str] = field(default_factory=list)


def load_query(path: str | Path) -> str:
    """SPARQL ファイルを読む。`#+` で始まる行は運用メモなので落とす。"""
    p = config_path(str(path))
    if not p.is_file():
        raise WikidataError(f"SPARQL が見つかりません: {p}")
    lines = [
        ln
        for ln in p.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#+")
    ]
    query = "\n".join(lines).strip()
    if not query:
        raise WikidataError(f"SPARQL が空です: {p}")
    return query


def run_query(query: str, *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """SPARQL を実行して bindings をそのまま返す。"""
    owned = client is None
    c = client or httpx.Client(timeout=TIMEOUT_SEC, follow_redirects=True)
    try:
        resp = c.post(
            ENDPOINT,
            data={"query": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
        )
        if resp.status_code != 200:
            raise WikidataError(
                f"Wikidata が {resp.status_code} を返した: {resp.text[:200]}"
            )
        payload = resp.json()
    except httpx.HTTPError as exc:
        raise WikidataError(f"Wikidata に接続できない: {exc}") from exc
    finally:
        if owned:
            c.close()

    return list((payload.get("results") or {}).get("bindings") or [])


def _value(binding: dict, key: str) -> str | None:
    cell = binding.get(key)
    if not cell:
        return None
    value = str(cell.get("value") or "").strip()
    return value or None


def _qid(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.rstrip("/").rsplit("/", 1)[-1]


def parse_bindings(bindings: list[dict[str, Any]]) -> tuple[list[ForeignRow], dict]:
    """bindings を1社1行にまとめる。

    SPARQL の結果は OPTIONAL の組み合わせで同じ企業が複数行に散る。
    qid でまとめ、値が競合したら**最初のものを採って注記を残す**。
    黙って上書きすると、どちらが採用されたか分からなくなる。
    """
    by_qid: dict[str, ForeignRow] = {}
    stats = {"bindings": len(bindings), "conflicts": 0, "no_qid": 0, "no_name": 0}

    for binding in bindings:
        qid = _qid(_value(binding, "company"))
        if not qid:
            stats["no_qid"] += 1
            continue
        name = _value(binding, "companyLabel")
        if not name:
            stats["no_name"] += 1
            continue

        row = by_qid.get(qid)
        if row is None:
            row = ForeignRow(qid=qid, name=name)
            by_qid[qid] = row

        for field_name, key in (
            ("name_en", "companyLabelEn"),
            ("lei", "lei"),
            ("cik", "cik"),
            ("website", "website"),
            ("ticker", "tickerLabel"),
        ):
            value = _value(binding, key)
            if value is None:
                continue
            current = getattr(row, field_name)
            if current is None:
                setattr(row, field_name, value)
            elif current != value:
                stats["conflicts"] += 1
                if len(row.notes) < 5:
                    row.notes.append(f"{field_name} が競合: {current} / {value}")

        country = _qid(_value(binding, "country"))
        if country and row.country is None:
            row.country = country

    return sorted(by_qid.values(), key=lambda r: r.qid), stats


def fetch(query_path: str | Path, *, client: httpx.Client | None = None):
    """SPARQL を読んで実行し、行にして返す。"""
    query = load_query(query_path)
    return parse_bindings(run_query(query, client=client))


def _cik_key(raw: str) -> str | None:
    """Wikidata の CIK は桁揃えがまちまち。SEC に合わせて10桁に揃える。"""
    try:
        return f"{int(raw):010d}"
    except ValueError:
        return None


def _houjin_bangou_key(raw: str) -> str | None:
    """法人番号は13桁。**桁数が違うものは捨てる。**

    Wikidata の値は利用者が入れたもので、ハイフン入りや桁落ちが混じる。
    正規化して通すと、別の会社の番号に化ける危険があるので**捨てる方を選ぶ**
    （突合できないのは「取れなかった」であって、誤った突合より安全）。
    """
    digits = "".join(c for c in raw if c.isdigit())
    return digits if len(digits) == 13 else None


#: 一次名簿ごとの突合鍵。**どの列で突き合わせるかは名簿によって違う。**
IDENTITY_KEYS: dict[str, Callable[[str], str | None]] = {
    "cik": _cik_key,
    "houjin_bangou": _houjin_bangou_key,
}


def fetch_identity(
    query_path: str | Path,
    *,
    key: str = "cik",
    fields: tuple[str, ...] = ("website", "lei"),
    client: httpx.Client | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """突合鍵 -> {website, lei, …} を**1クエリで**引く。

    **1社ずつ引かない。** 数千社を個別に照会すると WDQS に不当な負荷を
    かける。全件を1回で取り、手元で突合する。

    同じ鍵に複数の値があったら**最初のものを採って競合を数える。**
    黙って上書きすると、どちらが採用されたか分からなくなる。

    鍵は名簿によって違う（米国は CIK、国内は法人番号）。正規化のしかたも
    違うので `IDENTITY_KEYS` に分けてある。
    """
    normalize = IDENTITY_KEYS.get(key)
    if normalize is None:
        raise WikidataError(f"突合鍵 {key!r} の正規化が定義されていません")

    bindings = run_query(load_query(query_path), client=client)
    out: dict[str, dict[str, str]] = {}
    stats: dict[str, Any] = {
        "bindings": len(bindings),
        "key": key,
        "key_count": 0,
        "unusable_keys": 0,
        "conflicts": 0,
    }
    for field_name in fields:
        stats[field_name] = 0

    for binding in bindings:
        raw = _value(binding, key)
        if not raw:
            continue
        normalized = normalize(raw)
        if normalized is None:
            # **黙って飛ばさない。** 突合できなかった数が見えないと、
            # 被覆率が低い原因が「無い」のか「読めない」のか分からない
            stats["unusable_keys"] += 1
            continue
        entry = out.setdefault(normalized, {})
        for field_name in fields:
            value = _value(binding, field_name)
            if not value:
                continue
            if field_name not in entry:
                entry[field_name] = value
                stats[field_name] += 1
            elif entry[field_name] != value:
                stats["conflicts"] += 1

    stats["key_count"] = len(out)
    return out, stats


def fetch_cik_identity(
    query_path: str | Path, *, client: httpx.Client | None = None
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """CIK -> {website, lei}。`fetch_identity` の米国向けの呼び出し。"""
    identity, stats = fetch_identity(query_path, key="cik", client=client)
    # 既存の呼び出しと実行記録が読む名前を残す
    stats["cik_count"] = stats["key_count"]
    return identity, stats
