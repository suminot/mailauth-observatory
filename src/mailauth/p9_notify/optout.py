"""オプトアウトの登録簿（DESIGN.md Sprint 9）。

**「読めなかった」を「誰も断っていない」として扱わない。** ここが原則5の
最も実害の出る適用箇所である。登録簿の読み込みに失敗したときに空集合を
返すと、断った相手に送ってしまう。取り返しがつかない。だから
`available` が False の登録簿では通知計画を作らせない（`plan.build_plan`）。

登録簿はリポジトリに置いた CSV である（原則7 ── 設定はコード外）。
DB を持たないのは、**誰がいつ断ったかの履歴が git に残る**ほうが運用として
確実だからである。取り消しはコミットとして見える。

**期限の列を持たない。** 断られた相手は恒久的に対象外にする。「1年経ったから
また送る」を可能にする列を作ると、いつか誰かが使う。
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import config_path

#: 既定の登録簿。存在しなくてもよいが、**無い状態では通知計画を作れない**
DEFAULT_PATH = "configs/notify/optout.csv"

#: CSV の列。domain 以外は記録のためで、判定には使わない
COLUMNS = ("domain", "requested_on", "scope", "note")

#: `scope` の値。domain は当該ドメインのみ、entity は同一企業の全ドメイン
SCOPES = ("domain", "entity")


@dataclass
class OptOutEntry:
    domain: str
    requested_on: dt.date | None = None
    scope: str = "domain"
    note: str | None = None


@dataclass
class OptOutRegistry:
    """オプトアウトの登録簿。

    `available` が False は「登録簿を読めていない」ことを意味する。
    **空の登録簿（available=True, entries=[]）とは別の状態である。**
    """

    path: Path | None = None
    available: bool = False
    entries: list[OptOutEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def domains(self) -> set[str]:
        return {e.domain for e in self.entries}

    def entity_scoped(self) -> set[str]:
        """企業単位で断られたドメイン。同一企業の他のドメインも外す判断に使う。"""
        return {e.domain for e in self.entries if e.scope == "entity"}

    def matched(self, domain: str) -> OptOutEntry | None:
        """このドメインに当たる登録を返す。

        サブドメインも外す。`example.jp` が断ったなら `mail.example.jp` にも
        送らない。**断りの範囲を狭く解釈しない。**
        """
        target = _normalize(domain)
        if not target:
            return None
        for entry in self.entries:
            if target == entry.domain or target.endswith("." + entry.domain):
                return entry
        return None

    def contains(self, domain: str) -> bool:
        return self.matched(domain) is not None

    def reason(self, domain: str) -> str | None:
        entry = self.matched(domain)
        if entry is None:
            return None
        on = entry.requested_on.isoformat() if entry.requested_on else "日付不明"
        return f"{entry.domain} が {on} に受信を辞退（scope={entry.scope}）"

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "available": self.available,
            "count": len(self.entries),
            "entity_scoped": sorted(self.entity_scoped()),
            "notes": self.notes,
        }


def _normalize(domain: str) -> str:
    return str(domain or "").strip().lower().rstrip(".")


def load(path: Path | None = None) -> OptOutRegistry:
    """登録簿を読む。

    **失敗を成功に見せない。** 無い / 壊れている場合は `available=False` を
    返し、理由を notes に残す。呼び出し側（`plan.build_plan`）はこの状態では
    計画を作らない。
    """
    target = Path(path) if path else config_path(DEFAULT_PATH)
    registry = OptOutRegistry(path=target)

    if not target.is_file():
        registry.notes.append(
            f"オプトアウト登録簿 {target} が無い。**「誰も断っていない」とは"
            "解釈しない。** 空でよいならヘッダだけのファイルを置くこと"
        )
        return registry

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        registry.notes.append(f"登録簿を読めなかった: {exc}")
        return registry

    reader = csv.DictReader(text.splitlines())
    missing = [c for c in ("domain",) if c not in (reader.fieldnames or [])]
    if missing:
        registry.notes.append(
            f"登録簿に必須列が無い（{', '.join(missing)}）。"
            f"想定する列: {', '.join(COLUMNS)}"
        )
        return registry

    for lineno, row in enumerate(reader, start=2):
        domain = _normalize(row.get("domain", ""))
        if not domain or domain.startswith("#"):
            continue
        scope = (row.get("scope") or "domain").strip() or "domain"
        if scope not in SCOPES:
            registry.notes.append(
                f"{target.name}:{lineno} の scope が不正（{scope}）。"
                "domain として扱う"
            )
            scope = "domain"
        requested_on: dt.date | None = None
        raw_date = (row.get("requested_on") or "").strip()
        if raw_date:
            try:
                requested_on = dt.date.fromisoformat(raw_date)
            except ValueError:
                registry.notes.append(
                    f"{target.name}:{lineno} の requested_on を日付として読めない"
                    f"（{raw_date}）。判定には影響しない"
                )
        registry.entries.append(
            OptOutEntry(
                domain=domain,
                requested_on=requested_on,
                scope=scope,
                note=(row.get("note") or "").strip() or None,
            )
        )

    registry.available = True
    if not registry.entries:
        registry.notes.append("登録簿は空である（読めている。断りが1件も無い）")
    return registry


def append(
    domain: str,
    *,
    requested_on: dt.date | None = None,
    scope: str = "domain",
    note: str | None = None,
    path: Path | None = None,
) -> Path:
    """断りを追記する。ファイルが無ければヘッダごと作る。

    **削除する関数は用意しない。** 取り消しは人が git 上で行い、
    履歴に残るようにする。
    """
    target = Path(path) if path else config_path(DEFAULT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    new_file = not target.is_file()
    with target.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "domain": _normalize(domain),
                "requested_on": (requested_on or dt.date.today()).isoformat(),
                "scope": scope if scope in SCOPES else "domain",
                "note": note or "",
            }
        )
    return target
