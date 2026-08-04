"""未知 MX ホストの月次作業リスト（DESIGN.md Sprint 10）。

Sprint 10 は「未知 MX ホストの手動同定を**月次のルーチンに組み込む**」と
定めている。P6 は既に未知ホストを頻度順で manifest に出しているが、
**一覧が出るだけでは routine にならない。** 毎月同じ顔ぶれが並んでいても
気付けないし、手を付けたのかどうかも分からない。

そこでここでは月をまたいで追跡する。

  - 各ホストが**何か月連続で未知のままか**
  - 調査して同定できなかったホストは**未着手と区別する**

## 「同定できない」と「手を付けていない」は別である

自社運用の MX や、再販業者経由で製品名が公開されていないホストは、
何か月見ても同定できない。これを未着手と同じ扱いにすると、作業リストが
永久に減らない見かけになり、**本当に手を付けるべき新顔が埋もれる。**

調査して同定できなかったものは `configs/worklist/unidentified_hosts.yaml`
に理由付きで記録し、作業リストから外す。ただし**件数は残す。**
「調べたが分からなかった」は結論であって、無かったことにはしない。

## 一覧から消えたことは同定できた証拠ではない

P6 の未知ホスト一覧は上位 N 件で切っている。順位が落ちて一覧から消えた
ホストと、辞書に載って消えたホストは区別できない。**前月の一覧が上限に
達していた場合、そこに無いことを「同定済み」と読んではいけない**
（原則5の適用）。連続月数はこの区別を踏まえて数える。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .manifest import read_manifest
from .paths import config_path, list_run_ids, phase_dir

#: 調査済み・同定不能の記録。**未着手と混ぜない**
UNIDENTIFIED_PATH = "configs/worklist/unidentified_hosts.yaml"

#: P6 が未知ホストを何件まで出すか（`p6_infer.runner.UNKNOWN_MX_TOP_N`）。
#: 一覧が上限に達していたら、そこに無いことは同定の証拠にならない
TOP_N = 20

#: 同定できなかった理由。**「分からない」を分類しておく。**
#: 自社運用と情報が無いのは別の話で、前者は今後も同定できない
UNIDENTIFIED_REASONS = (
    "self_hosted",
    "reseller",
    "no_public_info",
    "dead_domain",
    "other",
)

REASON_LABELS = {
    "self_hosted": "自社運用。製品ではない",
    "reseller": "再販業者経由で製品名が公開されていない",
    "no_public_info": "公開情報から同定できない",
    "dead_domain": "ドメインが失効している",
    "other": "その他（備考を参照）",
}

#: 何か月連続で未知なら「新顔ではない」と見なすか。
#: 初月から騒ぐと毎月の一覧がそのまま作業リストになってしまう
STALE_MONTHS = 3


@dataclass
class UnidentifiedEntry:
    registered_domain: str
    reason: str = "other"
    investigated_on: dt.date | None = None
    note: str | None = None


@dataclass
class UnknownHost:
    registered_domain: str
    count: int
    examples: list[str] = field(default_factory=list)
    #: 現在の run を含めて何か月連続で未知一覧に載っているか
    months_unknown: int = 1
    #: 前月の一覧が上限に達していたため、連続月数が過小評価かもしれない
    streak_uncertain: bool = False

    @property
    def is_stale(self) -> bool:
        return self.months_unknown >= STALE_MONTHS

    def to_dict(self) -> dict[str, Any]:
        return {
            "registered_domain": self.registered_domain,
            "count": self.count,
            "examples": list(self.examples),
            "months_unknown": self.months_unknown,
            "streak_uncertain": self.streak_uncertain,
            "is_stale": self.is_stale,
        }


@dataclass
class Worklist:
    run_id: str
    #: 手を付けるべきホスト（調査済みを除いたもの）
    hosts: list[UnknownHost] = field(default_factory=list)
    #: 調査済み・同定不能として作業リストから外した分。**件数は残す**
    set_aside: list[UnknownHost] = field(default_factory=list)
    months_examined: list[str] = field(default_factory=list)
    #: P6 の未知ホスト一覧が上限に達している。**取りこぼしがある**
    truncated: bool = False
    unidentified_available: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def stale(self) -> list[UnknownHost]:
        return [h for h in self.hosts if h.is_stale]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "months_examined": self.months_examined,
            "truncated": self.truncated,
            "unidentified_available": self.unidentified_available,
            "hosts": [h.to_dict() for h in self.hosts],
            "set_aside": [h.to_dict() for h in self.set_aside],
            "stale_count": len(self.stale),
            "stale_months_threshold": STALE_MONTHS,
            "notes": self.notes,
        }


def load_unidentified(path: Path | str | None = None) -> tuple[
    dict[str, UnidentifiedEntry], bool, list[str]
]:
    """調査済み・同定不能の記録を読む。

    戻り値は (registered_domain -> 記録, 読めたか, 注記)。
    **読めなかったことを「調査済みが0件」にしない。** 読めていなければ
    全ホストを未着手として出す（作業が増える側に倒す）。
    """
    target = Path(path) if path else config_path(UNIDENTIFIED_PATH)
    notes: list[str] = []
    if not target.is_file():
        notes.append(
            f"調査済みの記録 {target} が無い。すべて未着手として並べている"
        )
        return {}, False, notes
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        notes.append(f"調査済みの記録を読めなかった: {exc}")
        return {}, False, notes

    items = raw.get("hosts") if isinstance(raw, dict) else raw
    out: dict[str, UnidentifiedEntry] = {}
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            notes.append(f"{target.name} の {index} 件目が辞書ではない")
            continue
        domain = str(item.get("registered_domain") or "").strip().lower()
        if not domain:
            notes.append(f"{target.name} の {index} 件目に registered_domain が無い")
            continue
        reason = str(item.get("reason") or "other").strip()
        if reason not in UNIDENTIFIED_REASONS:
            notes.append(
                f"{target.name} の {index} 件目の reason が不正（{reason}）。"
                "other として扱う"
            )
            reason = "other"
        investigated = item.get("investigated_on")
        if isinstance(investigated, dt.datetime):
            investigated = investigated.date()
        elif isinstance(investigated, str):
            try:
                investigated = dt.date.fromisoformat(investigated.strip())
            except ValueError:
                notes.append(
                    f"{target.name} の {index} 件目の investigated_on を"
                    f"日付として読めない（{investigated}）"
                )
                investigated = None
        elif not isinstance(investigated, dt.date):
            investigated = None
        out[domain] = UnidentifiedEntry(
            registered_domain=domain,
            reason=reason,
            investigated_on=investigated,
            note=(str(item.get("note") or "").strip() or None),
        )
    return out, True, notes


def _unknown_from(run_id: str) -> tuple[list[dict], bool]:
    """1か月ぶんの未知ホスト一覧。戻り値は (一覧, 上限に達していたか)。"""
    manifest = read_manifest(phase_dir(run_id, "p6_infer"))
    if manifest is None:
        return [], False
    hosts = (manifest.get("breakdown") or {}).get("unknown_mx_hosts") or []
    if not isinstance(hosts, list):
        return [], False
    return hosts, len(hosts) >= TOP_N


def build(
    run_id: str,
    *,
    months: list[str] | None = None,
    unidentified_path: Path | str | None = None,
) -> Worklist:
    """作業リストを組む。

    `months` は連続月数の計算に使う過去の run（新しい順でなくてよい）。
    省略すると `data/runs/` にあるものを使う。
    """
    worklist = Worklist(run_id=run_id)
    current, truncated = _unknown_from(run_id)
    worklist.truncated = truncated
    if truncated:
        worklist.notes.append(
            f"P6 の未知ホスト一覧が上限 {TOP_N} 件に達している。"
            "**一覧に無いホストが残っている**（頻度の低いものが切れている）"
        )

    known_runs = sorted(months if months is not None else list_run_ids())
    past = [r for r in known_runs if r < run_id]
    worklist.months_examined = past + [run_id]

    # 過去の月の一覧を新しい順に見て、連続して載っているかを数える
    history: list[tuple[str, set[str], bool]] = []
    for r in reversed(past):
        hosts, was_truncated = _unknown_from(r)
        if not hosts:
            # 実行記録が無い月。**「載っていなかった」とは扱わない**
            continue
        history.append(
            (r, {str(h.get("registered_domain") or "") for h in hosts}, was_truncated)
        )

    entries, available, notes = load_unidentified(unidentified_path)
    worklist.unidentified_available = available
    worklist.notes.extend(notes)

    for item in current:
        domain = str(item.get("registered_domain") or "")
        if not domain:
            continue
        host = UnknownHost(
            registered_domain=domain,
            count=int(item.get("count") or 0),
            examples=[str(e) for e in (item.get("examples") or [])],
        )
        for _month, names, was_truncated in history:
            if domain in names:
                host.months_unknown += 1
                continue
            # **一覧から消えたことは同定できた証拠ではない。**
            # 上限で切られていたなら順位が落ちただけかもしれない
            if was_truncated:
                host.streak_uncertain = True
            break

        if domain in entries:
            worklist.set_aside.append(host)
        else:
            worklist.hosts.append(host)

    worklist.hosts.sort(key=lambda h: (-h.count, h.registered_domain))
    worklist.set_aside.sort(key=lambda h: (-h.count, h.registered_domain))

    if not current:
        worklist.notes.append(
            f"{run_id} の P6 の実行記録に未知ホストが無い。"
            "P6 を実行していないか、すべて辞書に一致した"
        )
    if any(h.streak_uncertain for h in worklist.hosts):
        worklist.notes.append(
            "連続月数が過小評価になっているホストがある。前月の一覧が上限で"
            "切られていたため、**「載っていなかった」を「同定できた」と"
            "読めない**（原則5）"
        )
    return worklist


def to_markdown(
    worklist: Worklist, *, unidentified: dict[str, UnidentifiedEntry] | None = None
) -> str:
    """月次の作業リストを組む。人が読んで手を動かすための1枚。"""
    entries = unidentified if unidentified is not None else load_unidentified()[0]
    lines = [
        f"# 未知 MX ホストの作業リスト {worklist.run_id}",
        "",
        "辞書に一致しなかった MX ホストを、登録ドメイン単位で頻度順に並べています。",
        "**上から順に手で同定して `configs/fingerprints/` に追記すると推定率が上がります。**",
        "",
        f"連続 {STALE_MONTHS} か月以上未知のままのものには印を付けています。",
        "同定できないと判断したものは",
        f"`{UNIDENTIFIED_PATH}` に理由を書いて作業リストから外してください。",
        "**「調べたが分からなかった」は結論であって、無かったことにはしません。**",
        "",
    ]

    if not worklist.unidentified_available:
        lines += [
            "> 調査済みの記録を読めていないため、**すべて未着手として並べています。**",
            "",
        ]

    if worklist.hosts:
        stale = len(worklist.stale)
        lines += [
            f"## 手を付けるもの（{len(worklist.hosts)} 件）",
            "",
        ]
        if stale:
            lines += [
                f"**うち {stale} 件は {STALE_MONTHS} か月以上そのままです。** "
                "同定するか、同定できない理由を記録してください。",
                "",
            ]
        lines += [
            "| 登録ドメイン | 件数 | 連続月 | 例 |",
            "|---|---|---|---|",
        ]
        for host in worklist.hosts:
            mark = " ⚠" if host.is_stale else ""
            uncertain = "以上" if host.streak_uncertain else ""
            lines.append(
                f"| `{host.registered_domain}` | {host.count} "
                f"| {host.months_unknown}{uncertain}{mark} "
                f"| {', '.join(f'`{e}`' for e in host.examples)} |"
            )
        lines.append("")
    else:
        lines += ["## 手を付けるもの", "", "ありません。", ""]

    if worklist.set_aside:
        lines += [
            f"## 調査済み・同定できなかったもの（{len(worklist.set_aside)} 件）",
            "",
            "**件数は残します。** 「調べたが分からなかった」を無かったことに"
            "しないためです。",
            "",
            "| 登録ドメイン | 件数 | 理由 | 調査日 |",
            "|---|---|---|---|",
        ]
        for host in worklist.set_aside:
            entry = entries.get(host.registered_domain)
            reason = REASON_LABELS.get(entry.reason, entry.reason) if entry else "—"
            on = (
                entry.investigated_on.isoformat()
                if entry and entry.investigated_on
                else "—"
            )
            lines.append(
                f"| `{host.registered_domain}` | {host.count} | {reason} | {on} |"
            )
        lines.append("")

    for note in worklist.notes:
        lines.append(f"- {note}")
    if worklist.notes:
        lines.append("")

    lines += [
        "## 追記のしかた",
        "",
        "`configs/fingerprints/*.yaml` に規則を足します。コンソールの",
        "「辞書メンテナンス」画面（画面5）からも追加できます。追加した規則は",
        "**次回の P6 から効きます。過去の月は作り直しません**",
        "（訂正の方針と同じ）。",
        "",
    ]
    return "\n".join(lines)
