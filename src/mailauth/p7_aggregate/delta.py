"""前月差分（DESIGN.md P7）。

受け入れ基準は「前月差分が『消えた』と『取れなかった』を混同していないこと」。
これが差分計算で最も間違えやすい点である。

  - SERVFAIL で今月取れなかったドメインは**消えていない**。観測できていない
    だけなので、`domains_disappeared` に数えない
  - ポリシーの後退も同じ。今月の観測が無いのに「p=reject を外した」と
    記録してはならない
  - 実際に不在と言えるのは、**2連続の観測で不在だった**場合のみ
    （一時的な DNS 障害や委任ミスを恒久的な消滅と誤認しないため）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

#: ポリシーの強さ。上下の判定に使う
#: `quarantine_partial`（pct<100）は quarantine より弱く none より強い。
#: 以前は表に無く、DMARC が無いのと同じ 0 に落ちていた
POLICY_RANK = {None: 0, "none": 1, "quarantine_partial": 1.5, "quarantine": 2, "reject": 3}

#: 移り変わりの行列の区分。quarantine_partial は quarantine に寄せる
_STATE = {None: "no_dmarc", "none": "none", "quarantine_partial": "quarantine",
          "quarantine": "quarantine", "reject": "reject"}


@dataclass
class Delta:
    entities_new: int = 0
    entities_removed: int = 0
    domains_new: int = 0
    #: 2連続観測で不在だったものだけ
    domains_disappeared: int = 0
    policy_upgraded: int = 0
    policy_downgraded: int = 0
    #: **両方の月に観測できた同じドメインだけ**の移り変わり。キーは
    #: `"none>reject"` の形（no_dmarc / none / quarantine / reject）。
    #: 変わらなかったものも数える（行列の対角）
    policy_transitions: dict[str, int] = field(default_factory=dict)
    #: DMARC の rua を足した・外した（両方の月に DMARC があったドメインのうち）
    rua_added: int = 0
    rua_removed: int = 0
    #: 今月観測できず判定を保留したドメイン。**消滅ではない**
    domains_unobserved_this_month: int = 0
    #: 前月の出力が無くて差分を計算していない
    skipped: bool = False
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def _observed(fact: dict) -> bool:
    value = fact.get("observed")
    if isinstance(value, float):
        return value == value and bool(value)
    return bool(value)


def _policy(fact: dict) -> str | None:
    value = fact.get("effective_7489")
    if value is None or (isinstance(value, float) and value != value):
        return None
    return str(value)


def _has_rua(fact: dict) -> bool:
    value = fact.get("dmarc_rua")
    if value is None or isinstance(value, (str, bytes, float)):
        return False
    try:
        return len(value) > 0
    except TypeError:
        return False


def _by_domain(facts: list[dict]) -> dict[str, dict]:
    return {str(f.get("domain_id") or ""): f for f in facts}


def compute(
    current: list[dict],
    previous: list[dict] | None,
    *,
    before_previous: list[dict] | None = None,
    current_entities: set[str] | None = None,
    previous_entities: set[str] | None = None,
) -> Delta:
    """今月と前月の fact から差分を出す。

    `before_previous` は前々月の fact。2連続観測での不在を判定するために使う。
    無ければ `domains_disappeared` は 0 のままにして、その理由を注記する。
    """
    result = Delta()
    if previous is None:
        result.skipped = True
        result.notes.append("前月の facts が無いため差分を計算していない")
        return result

    now = _by_domain(current)
    before = _by_domain(previous)

    result.domains_new = sum(1 for d in now if d not in before)

    for domain_id, prev_fact in before.items():
        cur_fact = now.get(domain_id)

        if cur_fact is None or not _observed(cur_fact):
            # 今月の観測が無い。**ここで「消えた」と決めてはいけない**
            result.domains_unobserved_this_month += 1
            continue
        if not _observed(prev_fact):
            # 前月が観測できていないので、ポリシーの上下は判定できない
            continue

        old_p, new_p = _policy(prev_fact), _policy(cur_fact)
        key = f"{_STATE.get(old_p, 'none')}>{_STATE.get(new_p, 'none')}"
        result.policy_transitions[key] = result.policy_transitions.get(key, 0) + 1
        if old_p is not None and new_p is not None:
            had, has = _has_rua(prev_fact), _has_rua(cur_fact)
            result.rua_added += int(has and not had)
            result.rua_removed += int(had and not has)
        old = POLICY_RANK.get(old_p, 0)
        new = POLICY_RANK.get(new_p, 0)
        if new > old:
            result.policy_upgraded += 1
        elif new < old:
            result.policy_downgraded += 1

    if before_previous is None:
        result.notes.append(
            "前々月の facts が無いため domains_disappeared を数えていない。"
            "1回の不在は一時的な DNS 障害と区別できない"
        )
    else:
        older = _by_domain(before_previous)
        for domain_id in older:
            if not _observed(older[domain_id]):
                continue
            # 前月と今月の2連続で「観測できたが不在」なら消滅と見なす
            gone_last = _gone(before.get(domain_id))
            gone_now = _gone(now.get(domain_id))
            if gone_last and gone_now:
                result.domains_disappeared += 1

    if current_entities is not None and previous_entities is not None:
        result.entities_new = len(current_entities - previous_entities)
        result.entities_removed = len(previous_entities - current_entities)

    return result


def _gone(fact: dict | None) -> bool:
    """そのドメインが「観測できた上で、無かった」か。

    fact 自体が無いのは計測対象から外れたということで、
    レコードが無かったことの証明ではない。よって False を返す。
    """
    if fact is None:
        return False
    if not _observed(fact):
        return False
    present = fact.get("record_present")
    if isinstance(present, float):
        return present == present and not bool(present)
    return present is not None and not bool(present)


def diff_stats(current: Any, previous: Any) -> dict[str, int]:
    """全社統計の主要指標の増減。サイトの「前月比」に使う。"""
    keys = (
        "total_entities",
        "total_domains",
        "observed_domains",
        "entities_with_domains",
        "spf_adopted_domains",
        "dmarc_adopted_domains",
        "dmarc_enforced_domains",
        "enforced_reject_domains",
        "parked_hardened",
        "parked_neglected",
    )
    out: dict[str, int] = {}
    for key in keys:
        now = int(getattr(current, key, 0) or 0)
        before = int(getattr(previous, key, 0) or 0) if previous is not None else 0
        out[key] = now - before
    return out
