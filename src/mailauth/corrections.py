"""訂正申告の登録簿と訂正履歴ページ（DESIGN.md Sprint 9・10）。

**訂正窓口の常設は、名誉毀損の抗弁における「公益目的」の立証材料である**
（DESIGN.md 1.4）。窓口があることと、申告が処理されていることは別なので、
処理の記録を機械的に公開する。

`p9_notify` の通知文面は「訂正の履歴は公開サイトに残します」と書いている。
**書いた約束を果たす実装がここである。**

この登録簿の設計で守っていること

  1. **受け付けた事実は、結論が出る前から公開する。** 審査中のものを隠すと
     「都合の悪い申告を握り潰した」と読まれる余地が残る
  2. **却下した申告も載せる。** 載せないと履歴が「訂正した分だけ」になり、
     申告が何件あったのかが読み手に分からない
  3. **申告者の個人情報を持たない。** 氏名もメールアドレスも列を作らない。
     **列が無ければ入らない**（DESIGN.md の個人情報方針）
  4. **過去の集計を遡って作り直さない。** 訂正は次回以降の計測に反映し、
     過去は履歴として残す。数字を書き換えると、公開済みの値を引用した
     第三者の記述と食い違う
  5. **SLA を機械で測る。** 「48〜72時間で審査」は書いただけでは守られない
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import config_path

#: 既定の登録簿。無くてもよいが、**「読めなかった」と「0件」は区別する**
DEFAULT_PATH = "configs/corrections/corrections.yaml"

#: 審査の目標と上限（DESIGN.md Sprint 10「48〜72時間で審査」）
SLA_TARGET_HOURS = 48
SLA_LIMIT_HOURS = 72

#: 申告の状態。**received と under_review も公開する**（未処理を隠さない）
OPEN_STATUSES = ("received", "under_review")
CLOSED_STATUSES = ("accepted", "partially_accepted", "rejected", "withdrawn")
STATUSES = OPEN_STATUSES + CLOSED_STATUSES

STATUS_LABELS = {
    "received": "受付",
    "under_review": "審査中",
    "accepted": "訂正した",
    "partially_accepted": "一部訂正した",
    "rejected": "訂正しなかった",
    "withdrawn": "申告が取り下げられた",
}

#: 登録簿に置いてよい列。**申告者を特定する列をここに足さない。**
#: 氏名・メールアドレス・電話番号の列は作らない（列が無ければ入らない）
ALLOWED_FIELDS = frozenset(
    {
        "id",
        "received_on",
        "domain",
        "channel",
        "claim",
        "our_observation",
        "status",
        "reviewed_on",
        "resolution",
        "applied_from",
        "months_affected",
        "note",
    }
)

#: 申告経路。**個人を特定しない粒度でしか記録しない**
CHANNELS = ("issue", "email", "postal", "other")


class CorrectionsError(RuntimeError):
    """登録簿の内容が要件を満たしていない。"""


@dataclass
class Correction:
    id: str
    received_on: dt.date
    status: str
    #: 対象ドメイン。企業名ではなくドメインで識別する
    domain: str | None = None
    channel: str = "other"
    claim: str = ""
    #: 我々が観測していたこと。**申告と観測を並べて出す**
    our_observation: str = ""
    reviewed_on: dt.date | None = None
    resolution: str = ""
    #: 反映した月（YYYY-MM）。**過去の月は書き換えない**
    applied_from: str | None = None
    months_affected: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def review_hours(self, *, now: dt.datetime | None = None) -> float | None:
        """受付から審査までの時間。未審査なら現在までの経過時間。

        日付しか持っていないので日単位の粗い値になる。**粗いことを承知で
        出す。** 精度を上げるために受付時刻を持つと、申告者の行動時刻という
        個人情報に近づく
        """
        end = self.reviewed_on or (now or dt.datetime.now(dt.UTC)).date()
        return max((end - self.received_on).days, 0) * 24.0

    def is_overdue(self, *, now: dt.datetime | None = None) -> bool:
        """SLA の上限を超えて未審査のまま残っているか。"""
        if not self.is_open:
            return False
        hours = self.review_hours(now=now)
        return hours is not None and hours > SLA_LIMIT_HOURS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "received_on": self.received_on.isoformat(),
            "domain": self.domain,
            "channel": self.channel,
            "claim": self.claim,
            "our_observation": self.our_observation,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "reviewed_on": self.reviewed_on.isoformat() if self.reviewed_on else None,
            "resolution": self.resolution,
            "applied_from": self.applied_from,
            "months_affected": list(self.months_affected),
            "note": self.note,
        }


@dataclass
class CorrectionsRegistry:
    path: Path | None = None
    #: 読めているか。**「読めなかった」を「申告0件」として公開しない**
    available: bool = False
    entries: list[Correction] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.status] = out.get(entry.status, 0) + 1
        return out

    def open_entries(self) -> list[Correction]:
        return [e for e in self.entries if e.is_open]

    def overdue(self, *, now: dt.datetime | None = None) -> list[Correction]:
        return [e for e in self.entries if e.is_overdue(now=now)]

    def for_domain(self, domain: str) -> list[Correction]:
        target = (domain or "").strip().lower().rstrip(".")
        return [e for e in self.entries if (e.domain or "").lower() == target]

    def to_dict(self, *, now: dt.datetime | None = None) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "available": self.available,
            "count": len(self.entries),
            "by_status": self.by_status(),
            "open": [e.id for e in self.open_entries()],
            "overdue": [e.id for e in self.overdue(now=now)],
            "sla_target_hours": SLA_TARGET_HOURS,
            "sla_limit_hours": SLA_LIMIT_HOURS,
            "problems": self.problems,
            "notes": self.notes,
            "entries": [e.to_dict() for e in self.entries],
        }


def _inline(text: str) -> str:
    """箇条書きの1項目に収まる形に畳む。

    YAML の `|` で書かれた複数行をそのまま出すと、2行目以降がリストの外に
    こぼれて別の段落として表示される。**申告の内容が構造の外に出てしまうと
    誰の発言なのかが読み手に分からなくなる。** 表のセルでも同じ問題が出る
    ので、改行とパイプを潰す。
    """
    collapsed = " ".join(str(text or "").split())
    return collapsed.replace("|", "／")


def _date(value: Any, *, where: str, required: bool = False) -> dt.date | None:
    if value in (None, ""):
        if required:
            raise CorrectionsError(f"{where}: 日付が空になっている")
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CorrectionsError(f"{where}: 日付として読めない（{value}）") from exc


def load(path: Path | None = None) -> CorrectionsRegistry:
    """登録簿を読む。

    **失敗を「申告0件」に見せない。** 無い / 壊れている場合は
    `available=False` を返し、理由を残す。訂正履歴ページはその状態を
    そのまま書く。「申告はありません」と書いてしまうと事実に反しうる。
    """
    target = Path(path) if path else config_path(DEFAULT_PATH)
    registry = CorrectionsRegistry(path=target)

    if not target.is_file():
        registry.notes.append(
            f"訂正申告の登録簿 {target} が無い。**「申告が0件」とは書かない。** "
            "0件でよいなら空リストのファイルを置くこと"
        )
        return registry

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        registry.notes.append(f"登録簿を読めなかった: {exc}")
        return registry

    if raw is None:
        raw = []
    items = raw.get("corrections") if isinstance(raw, dict) else raw
    if items is None:
        items = []
    if not isinstance(items, list):
        registry.notes.append("登録簿の形式が list ではない")
        return registry

    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        where = f"{target.name} の {index} 件目"
        if not isinstance(item, dict):
            registry.problems.append(f"{where}: 辞書ではない")
            continue

        # **列そのものを制限する。** 申告者を特定する列が紛れ込んだら弾く
        extra = sorted(set(item) - ALLOWED_FIELDS)
        if extra:
            registry.problems.append(
                f"{where}: 想定外の列がある（{', '.join(extra)}）。"
                "**申告者を特定する情報は登録簿に置かない**"
            )
            continue

        try:
            received_on = _date(item.get("received_on"), where=where, required=True)
            reviewed_on = _date(item.get("reviewed_on"), where=where)
        except CorrectionsError as exc:
            registry.problems.append(str(exc))
            continue

        entry_id = str(item.get("id") or "").strip()
        if not entry_id:
            registry.problems.append(f"{where}: id が無い")
            continue
        if entry_id in seen:
            registry.problems.append(f"{where}: id が重複している（{entry_id}）")
            continue
        seen.add(entry_id)

        status = str(item.get("status") or "received").strip()
        if status not in STATUSES:
            registry.problems.append(
                f"{where}: status が不正（{status}）。"
                f"使えるのは {', '.join(STATUSES)}"
            )
            continue
        if status in CLOSED_STATUSES and reviewed_on is None:
            registry.problems.append(
                f"{where}: {status} なのに reviewed_on が無い。"
                "**いつ審査したかを残さないと SLA が測れない**"
            )
            continue
        if reviewed_on is not None and received_on is not None and reviewed_on < received_on:
            registry.problems.append(
                f"{where}: reviewed_on が received_on より前になっている"
            )
            continue

        channel = str(item.get("channel") or "other").strip()
        if channel not in CHANNELS:
            registry.problems.append(
                f"{where}: channel が不正（{channel}）。other として扱う"
            )
            channel = "other"

        assert received_on is not None  # 上で required=True にしてある
        registry.entries.append(
            Correction(
                id=entry_id,
                received_on=received_on,
                status=status,
                domain=(str(item.get("domain") or "").strip().lower() or None),
                channel=channel,
                claim=str(item.get("claim") or "").strip(),
                our_observation=str(item.get("our_observation") or "").strip(),
                reviewed_on=reviewed_on,
                resolution=str(item.get("resolution") or "").strip(),
                applied_from=(str(item.get("applied_from") or "").strip() or None),
                months_affected=[str(m) for m in (item.get("months_affected") or [])],
                note=(str(item.get("note") or "").strip() or None),
            )
        )

    registry.entries.sort(key=lambda e: (e.received_on, e.id), reverse=True)
    registry.available = not registry.problems
    if registry.problems:
        registry.notes.append(
            "登録簿に不備がある。**不備がある状態を「申告0件」として公開しない**"
        )
    elif not registry.entries:
        registry.notes.append("登録簿は空である（読めている。申告が1件も無い）")
    return registry


def to_markdown(
    registry: CorrectionsRegistry, *, now: dt.datetime | None = None
) -> str:
    """訂正履歴のページを組む。

    **却下も審査中も載せる。** 訂正した分だけを載せると、申告が何件あって
    どう扱われたのかが読み手に分からない。
    """
    stamp = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    lines = [
        "# 訂正履歴",
        "",
        "受け付けた訂正申告と、その処理の記録です。",
        "**審査中のものも、訂正しなかったものも載せます。** 訂正した分だけを",
        "載せると、申告が何件あってどう扱われたのかが分からなくなります。",
        "",
        f"この一覧は `mailauth corrections` が生成しています（{stamp}）。",
        "",
        f"審査の目標は受付から {SLA_TARGET_HOURS} 時間以内、"
        f"上限は {SLA_LIMIT_HOURS} 時間です。",
        "超過しているものは下の表に印を付けています。",
        "",
        "申告の方法は[訂正申告](./corrections)のページにあります。",
        "**申告者の氏名やメールアドレスはこの登録簿に保存していません。**",
        "",
    ]

    if not registry.available:
        lines += [
            "## 登録簿を読めていません",
            "",
            "**「申告が0件」ではありません。** 登録簿の読み込みに失敗しています。",
            "",
        ]
        for note in registry.notes + registry.problems:
            lines.append(f"- {note}")
        lines.append("")
        return "\n".join(lines)

    if not registry.entries:
        lines += [
            "## 受け付けた申告",
            "",
            "現時点で受け付けた訂正申告はありません（登録簿は読めています）。",
            "",
        ]
        return "\n".join(lines)

    counts = registry.by_status()
    lines += ["## 内訳", "", "| 状態 | 件数 |", "|---|---|"]
    for status in STATUSES:
        if status in counts:
            lines.append(f"| {STATUS_LABELS[status]} | {counts[status]} |")
    lines += ["", f"合計 {len(registry.entries)} 件。", ""]

    overdue = {e.id for e in registry.overdue(now=now)}
    if overdue:
        lines += [
            f"**審査の上限（{SLA_LIMIT_HOURS} 時間）を超えている申告が "
            f"{len(overdue)} 件あります。**",
            "",
        ]

    lines += [
        "## 一覧",
        "",
        "| ID | 受付 | 対象 | 状態 | 審査 | 反映 |",
        "|---|---|---|---|---|---|",
    ]
    for entry in registry.entries:
        mark = " ⚠" if entry.id in overdue else ""
        reviewed = entry.reviewed_on.isoformat() if entry.reviewed_on else "—"
        applied = entry.applied_from or "—"
        lines.append(
            f"| {entry.id} | {entry.received_on.isoformat()} "
            f"| {entry.domain or '—'} | {STATUS_LABELS[entry.status]}{mark} "
            f"| {reviewed} | {applied} |"
        )
    lines.append("")

    lines += ["## 個別の記録", ""]
    for entry in registry.entries:
        lines += [f"### {entry.id}（{STATUS_LABELS[entry.status]}）", ""]
        lines.append(f"- 受付: {entry.received_on.isoformat()}")
        if entry.domain:
            lines.append(f"- 対象: {entry.domain}")
        if entry.months_affected:
            lines.append(f"- 対象の月: {', '.join(entry.months_affected)}")
        if entry.claim:
            lines.append(f"- 申告の内容: {_inline(entry.claim)}")
        if entry.our_observation:
            lines.append(f"- 当方の観測: {_inline(entry.our_observation)}")
        if entry.reviewed_on:
            lines.append(f"- 審査: {entry.reviewed_on.isoformat()}")
        if entry.resolution:
            lines.append(f"- 結論: {_inline(entry.resolution)}")
        else:
            # **空欄を空欄として出す。** 埋まっていないことが分かる形にする
            lines.append("- 結論: （未記入）")
        if entry.applied_from:
            lines.append(
                f"- 反映: {entry.applied_from} の計測から。"
                "**過去の月の数字は書き換えていません**"
            )
        if entry.note:
            lines.append(f"- 備考: {_inline(entry.note)}")
        lines.append("")

    lines += [
        "## 訂正の反映について",
        "",
        "誤りが確認できた場合、**次回以降の計測に反映します。過去の月の数字は",
        "書き換えません。** 公開済みの値を引用した記述と食い違わないようにする",
        "ためです。どの月から反映したかは上の記録に書いています。",
        "",
        "計測方法や集計定義の変更は[変更履歴](./changelog)に記録しています。",
        "",
    ]
    return "\n".join(lines)


def to_json(registry: CorrectionsRegistry, *, now: dt.datetime | None = None) -> str:
    return json.dumps(
        registry.to_dict(now=now), ensure_ascii=False, indent=1, sort_keys=True
    )
