"""グループ会社の母集団（jp-all-listed-group、BACKLOG 52 の三歩目）。

有報の「関係会社の状況」から読んだ子会社名（subsidiaries.csv）を gBizINFO で
引いて企業サイトのドメインが分かったもの（subsidiary_domains.csv）を、
**上場会社とは別の母集団**として組み立てる。上場会社の率には混ぜない
（子会社のメールは親会社が管理していないことがある）。

外すもの（数えて manifest に出す）：

- **上場会社そのもの**（法人番号が上場会社と同じ）。jp-all-listed で測っている
- **上場会社の自社ドメイン**（有報の公告掲載方法に会社が書いたドメイン）を
  サイトにしている子会社。親会社のサイトの一部をそのまま会社のページに
  していることが多く、測ると親会社をもう一度測ることになる
- 同じ法人番号が複数の親会社の下に出たもの（合弁など）は1社にし、
  EDINET コードの若い親会社に付ける
- **3社以上の親会社の下に出る法人は外す。** 子会社ではなく、注記に出てくる
  借入先・信託口の銀行（日本カストディ銀行が 27 社、三菱UFJ の銀行が 29 社の
  下に出ていた。2026-11-07）。合弁でも親は2社まで
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..contracts import Entity
from ..normalize import (
    domain_from_url,
    normalize_houjin_bangou,
    normalize_name,
    normalize_securities_code,
)

#: これ以上の親会社の下に出る法人は子会社として扱わない
MANY_PARENTS = 3


def build_group_entities(
    group_rows: dict[tuple[str, str], tuple[str, str, str]],
    parents: Iterable[Any],
    listed_domains: set[str],
    *,
    population_id: str,
    run_id: str,
    country: str = "JP",
    sites: dict[str, str] | None = None,
) -> tuple[list[Entity], dict[str, int]]:
    """子会社の Entity を組み立てる。

    `group_rows` は `group_domains.read_csv` の形（(親の EDINET コード, 子会社名)
    → (法人番号, ドメイン, 状態)）。`parents` は EDINET コードリストの行
    （上場会社だけ）。`listed_domains` は上場会社の自社ドメイン。
    """
    by_code = {r.edinet_code: r for r in parents if getattr(r, "edinet_code", None)}
    listed_numbers = {
        n for n in (normalize_houjin_bangou(r.houjin_bangou) for r in by_code.values()) if n
    }
    stats = {
        "rows": 0, "found": 0, "from_wikidata": 0, "parent_not_listed": 0, "listed_company": 0,
        "listed_domain": 0, "duplicate_number": 0, "many_parents": 0, "entities": 0,
    }
    #: 法人番号ごとの親の数。3社以上なら子会社ではない（借入先・信託口の銀行）
    parents_of: dict[str, set[str]] = {}
    for (code, _name), (number, _domain, _status) in group_rows.items():
        hb_ = normalize_houjin_bangou(number) if number else None
        if hb_:
            parents_of.setdefault(hb_, set()).add(code)
    sites = sites or {}
    entities: dict[str, Entity] = {}
    for (code, name), (number, domain, status) in sorted(group_rows.items()):
        stats["rows"] += 1
        # **gBizINFO にサイトの登録が無い法人は、法人番号で Wikidata の公式サイトを
        # 使う**（P3225 → P856、CC0）。商号の一致は gBizINFO で確かめ済み
        if status == "no_url" and number:
            hb0 = normalize_houjin_bangou(number)
            site = sites.get(hb0 or "")
            wd = domain_from_url(site) if site else None
            if wd:
                domain, status = wd, "found"
                stats["from_wikidata"] += 1
        if status != "found" or not domain or not number:
            continue
        stats["found"] += 1
        parent = by_code.get(code)
        if parent is None:
            # 親が上場をやめた・コードリストに無い。親の無い子会社は作らない
            stats["parent_not_listed"] += 1
            continue
        hb = normalize_houjin_bangou(number)
        if hb is None:
            continue
        if hb in listed_numbers:
            stats["listed_company"] += 1
            continue
        if len(parents_of.get(hb, ())) >= MANY_PARENTS:
            stats["many_parents"] += 1
            continue
        domain = domain.lower()
        if domain in listed_domains:
            stats["listed_domain"] += 1
            continue
        eid = f"jp:{hb}"
        if eid in entities:
            stats["duplicate_number"] += 1
            continue
        parent_hb = normalize_houjin_bangou(parent.houjin_bangou)
        entities[eid] = Entity(
            entity_id=eid,
            run_id=run_id,
            country=country,
            population_ids=[population_id],
            name=name,
            name_normalized=normalize_name(name),
            houjin_bangou=hb,
            official_url=f"https://{domain}/",
            official_domain=domain,
            parent_entity_id=f"jp:{parent_hb}" if parent_hb else f"edinet:{code}",
            parent_securities_code=normalize_securities_code(parent.securities_code),
        )
    stats["entities"] = len(entities)
    return [entities[k] for k in sorted(entities)], stats
