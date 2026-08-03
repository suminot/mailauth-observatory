"""Organizational Domain の二重解決（DESIGN.md P5）。

PSL 解決と Tree Walk 解決の両方を計算し、`org_domain_divergence` を保存する。
日本の `.co.jp` 系や多階層サブドメインで差分が出る候補を事前に洗い出すため。

Tree Walk（RFC 9989）の手順
  Author Domain から `_dmarc` ラベルを付けて上位へ辿り、`psd=y` または
  `psd=n` を含む有効なレコードを見つけたら停止。DoS 対策で1ドメインあたり
  最大8クエリ。8ラベル超のドメインは Author Domain を最初に照会し、
  続行時は最右7ラベルから再開する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..records import find_dmarc_records
from ..resolver import Resolver
from .dmarc import dmarc_tags

#: DoS 対策の上限（RFC 9989）
MAX_TREE_WALK_QUERIES = 8
#: 8ラベルを超える場合に再開する位置
TREE_WALK_RESUME_LABELS = 7


@dataclass
class OrgDomainResult:
    psl: str | None = None
    treewalk: str | None = None
    divergence: bool = False
    treewalk_queries: int = 0
    #: Tree Walk が psd タグを見つけて停止したか。
    #: 見つからず打ち切った場合は判定の確度が落ちる
    treewalk_terminated_on_psd: bool = False
    notes: list[str] = field(default_factory=list)


def _psl():
    from publicsuffixlist import PublicSuffixList

    return PublicSuffixList()


def resolve_psl(domain: str) -> str | None:
    """PSL による Organizational Domain。

    PSL の PRIVATE セクション（s3.amazonaws.com 等）を含むため、
    Tree Walk とは結果が異なることがある。それが検出したい差分。
    """
    if not domain:
        return None
    return _psl().privatesuffix(domain.strip().lower().rstrip(".")) or None


def tree_walk_names(domain: str) -> list[str]:
    """Tree Walk で照会する名前の列（RFC 9989 の手順）。"""
    clean = domain.strip().lower().rstrip(".")
    if not clean:
        return []
    labels = clean.split(".")

    if len(labels) > MAX_TREE_WALK_QUERIES:
        # 8ラベル超は Author Domain を最初に照会し、最右7ラベルから再開する
        names = [clean]
        start = len(labels) - TREE_WALK_RESUME_LABELS
        for i in range(start, len(labels) - 1):
            names.append(".".join(labels[i:]))
        return names[:MAX_TREE_WALK_QUERIES]

    # 自分自身から順に上位へ。TLD 単独は照会しない
    return [".".join(labels[i:]) for i in range(len(labels) - 1)][:MAX_TREE_WALK_QUERIES]


def resolve_tree_walk(domain: str, resolver: Resolver) -> tuple[str | None, int, bool]:
    """Tree Walk による Organizational Domain。

    戻り値は (org_domain, 使ったクエリ数, psd で停止したか)。
    """
    queries = 0
    for name in tree_walk_names(domain):
        answer = resolver.query(f"_dmarc.{name}", "TXT")
        queries += 1
        if not answer.observed or not answer.record_present:
            continue
        records = find_dmarc_records(
            ["".join(chunks) for chunks in answer.txt_strings] or answer.values
        )
        if not records:
            continue
        tags = dmarc_tags(records[0])
        psd = (tags.get("psd") or "").lower()
        if psd in ("y", "n"):
            # psd タグを含む有効なレコードを見つけたら停止（RFC 9989）。
            # **psd を持たないレコードでは停止しない。** ここで停止させると
            # Author Domain 自身が常に Organizational Domain になり、
            # PSL とほぼ全件で食い違って差分の指標が意味を失う
            return name, queries, True
    return None, queries, False


def resolve(domain: str, resolver: Resolver | None = None) -> OrgDomainResult:
    """PSL と Tree Walk の両方を計算する。

    resolver が無い場合は PSL だけを返す（Tree Walk は DNS を要する）。
    """
    result = OrgDomainResult()
    result.psl = resolve_psl(domain)

    if resolver is None:
        result.notes.append("resolver が無いため Tree Walk を計算していない")
        return result

    treewalk, queries, on_psd = resolve_tree_walk(domain, resolver)
    result.treewalk = treewalk
    result.treewalk_queries = queries
    result.treewalk_terminated_on_psd = on_psd

    if treewalk is None:
        result.notes.append(
            "Tree Walk で有効な DMARC レコードが見つからなかった。"
            "PSL 側の判定のみが利用できる"
        )
        return result

    result.divergence = (result.psl or "") != (treewalk or "")
    if result.divergence:
        result.notes.append(
            f"PSL と Tree Walk で Organizational Domain が異なる: "
            f"{result.psl} / {treewalk}"
        )
    if not on_psd:
        result.notes.append(
            "psd タグを持つレコードで停止していない。RFC 9989 の想定どおりではない"
        )
    return result
