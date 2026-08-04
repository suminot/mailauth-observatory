"""計測対象からの除外（DESIGN.md P8「訂正申告」/ Sprint 9）。

公開サイトの訂正申告ページは「計測対象から外す依頼には応じます。ドメインの
除外リストに追加します」と書いている。**その約束を果たす実装がここである。**

`p9_notify.optout` とは別物である。あちらは**通知を送らない**登録簿で、
こちらは**計測そのものをしない**登録簿である。「連絡は不要だが計測は構わない」
という相手もいるので、同じ表に混ぜない。

## 除外は第三の状態である

**「除外した」は「観測できなかった」でも「レコードが無かった」でもない。**
「測らないと決めた」である（原則5の延長）。したがって

  - 除外したドメインは silver にも gold にも出さない
  - **除外した件数は必ず記録して公開する。** 黙って分母から抜くと、
    「観測できたドメインの割合」が理由の説明できない形で動く。
    読み手には計測失敗と区別が付かない

## 読めなければ止める

登録簿を読めないまま計測を続けると、外してほしいと言った相手を測ることに
なる。**これは取り返しがつかない。** `available=False` の登録簿では
P2 / P3 / P4 が止まる（`require_available`）。
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .paths import config_path

#: 既定の登録簿。**無い状態では計測しない。** 空でよいならヘッダだけ置く
DEFAULT_PATH = "configs/domains/excluded.csv"

COLUMNS = ("domain", "entity_id", "scope", "requested_on", "correction_id", "note")

#: `scope` の値。domain は当該ドメインのみ、entity は同一企業のすべて
SCOPES = ("domain", "entity")


class ExclusionsUnavailableError(RuntimeError):
    """除外リストを読めていない。**この状態で計測を進めない。**"""


@dataclass
class ExclusionEntry:
    domain: str | None = None
    entity_id: str | None = None
    scope: str = "domain"
    requested_on: dt.date | None = None
    #: 対応する訂正申告の id（`configs/corrections/corrections.yaml`）
    correction_id: str | None = None
    note: str | None = None

    def describe(self) -> str:
        on = self.requested_on.isoformat() if self.requested_on else "日付不明"
        target = self.domain or self.entity_id or "(対象不明)"
        ref = f" / {self.correction_id}" if self.correction_id else ""
        return f"{target} が {on} に計測対象からの除外を依頼（scope={self.scope}{ref}）"


@dataclass
class ExclusionRegistry:
    """計測対象から外すドメイン・企業の登録簿。

    `available=False` は「読めていない」。**空の登録簿とは別の状態である。**
    """

    path: Path | None = None
    available: bool = False
    entries: list[ExclusionEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 実際に効いた件数。**除外は黙って行わない**（原則4）
    hits: dict[str, int] = field(default_factory=dict)

    @property
    def domains(self) -> set[str]:
        return {e.domain for e in self.entries if e.domain}

    @property
    def entity_ids(self) -> set[str]:
        return {e.entity_id for e in self.entries if e.entity_id and e.scope == "entity"}

    def matched(self, domain: str | None = None, *, entity_id: str | None = None):
        """当たる登録を返す。

        ドメインはサブドメインも外す。`example.jp` が外してほしいなら
        `mail.example.jp` も外す。**依頼の範囲を狭く解釈しない。**
        """
        target = _normalize(domain) if domain else None
        for entry in self.entries:
            if entity_id and entry.entity_id and entry.scope == "entity":
                if entry.entity_id == entity_id:
                    return entry
            if target and entry.domain:
                if target == entry.domain or target.endswith("." + entry.domain):
                    return entry
        return None

    def excludes(self, domain: str | None = None, *, entity_id: str | None = None) -> bool:
        return self.matched(domain, entity_id=entity_id) is not None

    def record(self, key: str, count: int = 1) -> None:
        """効いた件数を数える。manifest に出すため。"""
        self.hits[key] = self.hits.get(key, 0) + count

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "available": self.available,
            "count": len(self.entries),
            "domains": sorted(d for d in self.domains if d),
            "entity_ids": sorted(self.entity_ids),
            "hits": dict(self.hits),
            "notes": self.notes,
        }


def _normalize(domain: str) -> str:
    return str(domain or "").strip().lower().rstrip(".")


def load(path: Path | str | None = None) -> ExclusionRegistry:
    """登録簿を読む。

    **失敗を「除外0件」に見せない。** 無い / 壊れている場合は
    `available=False` を返す。呼び出し側は `require_available()` で止める。
    """
    target = Path(path) if path else config_path(DEFAULT_PATH)
    registry = ExclusionRegistry(path=target)

    if not target.is_file():
        registry.notes.append(
            f"除外リスト {target} が無い。**「除外の依頼が無い」とは解釈しない。** "
            "空でよいならヘッダだけのファイルを置くこと"
        )
        return registry

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        registry.notes.append(f"除外リストを読めなかった: {exc}")
        return registry

    reader = csv.DictReader(text.splitlines())
    fields = reader.fieldnames or []
    if "domain" not in fields and "entity_id" not in fields:
        registry.notes.append(
            f"除外リストに domain も entity_id も無い。想定する列: {', '.join(COLUMNS)}"
        )
        return registry

    for lineno, row in enumerate(reader, start=2):
        domain = _normalize(row.get("domain", "")) or None
        entity_id = (row.get("entity_id") or "").strip() or None
        if not domain and not entity_id:
            continue
        if domain and domain.startswith("#"):
            continue

        scope = (row.get("scope") or ("entity" if entity_id and not domain else "domain")).strip()
        if scope not in SCOPES:
            registry.notes.append(
                f"{target.name}:{lineno} の scope が不正（{scope}）。domain として扱う"
            )
            scope = "domain"

        requested_on: dt.date | None = None
        raw = (row.get("requested_on") or "").strip()
        if raw:
            try:
                requested_on = dt.date.fromisoformat(raw)
            except ValueError:
                registry.notes.append(
                    f"{target.name}:{lineno} の requested_on を日付として読めない（{raw}）。"
                    "判定には影響しない"
                )

        registry.entries.append(
            ExclusionEntry(
                domain=domain,
                entity_id=entity_id,
                scope=scope,
                requested_on=requested_on,
                correction_id=(row.get("correction_id") or "").strip() or None,
                note=(row.get("note") or "").strip() or None,
            )
        )

    registry.available = True
    if not registry.entries:
        registry.notes.append("除外リストは空である（読めている。依頼が1件も無い）")
    return registry


def require_available(registry: ExclusionRegistry) -> None:
    """読めていなければ止める。**警告では済ませない。**

    外してほしいと言った相手を測ってしまうのは取り返しがつかない。
    """
    if registry.available:
        return
    raise ExclusionsUnavailableError(
        "計測対象の除外リストを読めていない。**「除外の依頼が無い」とは"
        "解釈しない。** この状態で計測を進めると、外してほしいと言った相手を"
        "測ることになる。空でよいならヘッダだけのファイルを置くこと。\n  "
        + "\n  ".join(registry.notes)
    )


def append(
    *,
    domain: str | None = None,
    entity_id: str | None = None,
    scope: str = "domain",
    requested_on: dt.date | None = None,
    correction_id: str | None = None,
    note: str | None = None,
    path: Path | None = None,
) -> Path:
    """除外の依頼を追記する。ファイルが無ければヘッダごと作る。

    **削除する関数は用意しない。** 取り消しは人が git 上で行い、履歴に残す。
    """
    if not domain and not entity_id:
        raise ValueError("domain か entity_id のどちらかは必要")
    target = Path(path) if path else config_path(DEFAULT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    new_file = not target.is_file()
    with target.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "domain": _normalize(domain) if domain else "",
                "entity_id": entity_id or "",
                "scope": scope if scope in SCOPES else "domain",
                "requested_on": (requested_on or dt.date.today()).isoformat(),
                "correction_id": correction_id or "",
                "note": note or "",
            }
        )
    return target
