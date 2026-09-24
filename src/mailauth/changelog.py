"""changelog の自動生成（DESIGN.md Sprint 10 / P8「必須ページ」）。

**数字が前月と動いたとき、実態が変わったのか計測が変わったのかを読み手が
区別できないと、時系列が意味を持たない。** 手で書く前提にすると、忙しい月に
書き落とされて、あとから「この段差は何だったのか」が誰にも分からなくなる。

機械的に検出できるもの
  - パーサ・推定器・集計器の版（`tool_versions`）
  - フィンガープリント辞書の版
  - 業種写像の版（`industry_map_version`）
  - 設定ファイルのハッシュ（`config_hash`）
  - 母集団の件数と主要指標の増減

**「検出できたこと」と「意味の説明」は別物である。** 版が上がったことは
機械が言えるが、それが数字にどう影響したかは人が書く。生成する changelog は
人が書く欄を空けて出す。埋まっていない欄があることが、書くべきことが
残っていることを示す。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from . import PHASES
from .io import read_parquet
from .manifest import read_manifest
from .p7_aggregate import BY_SECTOR_FILENAME, OVERALL_FILENAME
from .paths import gold_dir, gold_root, phase_dir, previous_run_id

#: 版の変化を追う対象。ここに無いものは changelog に出ない
TRACKED_VERSIONS = (
    "mailauth",
    "parser",
    "inference",
    "aggregator",
    "publisher",
    "fingerprints",
)

#: 指標の増減を出す列
TRACKED_METRICS = (
    "total_entities",
    "total_domains",
    "observed_domains",
    "spf_adopted_domains",
    "dmarc_adopted_domains",
    "dmarc_enforced_domains",
    "enforced_reject_domains",
    "dkim_detected_domains",
    "parked_hardened",
    "parked_neglected",
)


@dataclass
class VersionChange:
    phase: str
    key: str
    before: str | None
    after: str | None


@dataclass
class MetricChange:
    population_id: str
    metric: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


class Note(str):
    """注記。**文字列としてそのまま使えるが、鍵も持つ。**

    既存の呼び出しは `" / ".join(entry.notes)` のように文字列として扱って
    いるので、型を変えずに言語の切り替えだけを足す。英語版は `key` から
    引き直す（日本語を機械的に訳すのではなく、同じ意味の英文を用意する）。
    """

    key: str

    def __new__(cls, key: str, text: str) -> Note:
        obj = super().__new__(cls, text)
        obj.key = key
        return obj


#: 注記の英文。**日本語と同じことを言う。** 片方にしか無い注意書きが
#: できると、その言語で読んだ人だけが前提を知らないまま数字を受け取る。
NOTES_EN: dict[str, str] = {
    "no_previous_manifest": (
        "No manifest for the previous month ({prev}), so no change on the "
        "measurement side has been detected. Either this is the first month, "
        "or the run record has been lost"
    ),
    "industry_map_changed": (
        "The set of sector classes has changed. **When the mapping version "
        "changes, a comparison with the previous month does not hold.** "
        "Re-aggregate at one version, or state plainly that the months are "
        "not comparable"
    ),
    "measurement_and_metrics_moved": (
        "A change on the measurement side and a change in the figures fall in "
        "the same month. **The movement this month must not be read as a "
        "change in the world.** Which of the two it came from has to be "
        "written by a person"
    ),
}


@dataclass
class MonthEntry:
    month: str
    previous_month: str | None = None
    version_changes: list[VersionChange] = field(default_factory=list)
    config_changes: list[VersionChange] = field(default_factory=list)
    metric_changes: list[MetricChange] = field(default_factory=list)
    #: 業種写像の版が変わった。**変わると時系列比較が成立しない**
    industry_map_changed: bool = False
    industry_map: tuple[str | None, str | None] = (None, None)
    notes: list[str] = field(default_factory=list)

    @property
    def has_measurement_change(self) -> bool:
        """計測側が変わったか。数字の段差の説明が要るかどうかの判断。"""
        return bool(
            self.version_changes or self.config_changes or self.industry_map_changed
        )


def available_months() -> list[str]:
    import re

    root = gold_root()
    if not root.is_dir():
        return []
    pattern = re.compile(r"^month=(\d{4}-\d{2})$")
    return sorted(
        m.group(1) for d in root.iterdir() if d.is_dir() and (m := pattern.match(d.name))
    )


def _versions(run_id: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for phase in PHASES:
        manifest = read_manifest(phase_dir(run_id, phase))
        if manifest is None:
            continue
        out[phase] = {
            "config_hash": manifest.get("config_hash") or "",
            **{
                k: str(v)
                for k, v in (manifest.get("tool_versions") or {}).items()
                if k in TRACKED_VERSIONS
            },
        }
    return out


def _overall(month: str) -> dict[str, dict[str, Any]]:
    frame = read_parquet(gold_dir(month) / OVERALL_FILENAME)
    if frame is None:
        return {}
    return {
        str(row["population_id"]): row
        for row in frame.to_dict(orient="records")
    }


def _industry_map_version(month: str) -> str | None:
    """業種写像の版。gold の metadata ではなく sector の在無から拾う。

    写像の版が変わると分類そのものが変わるので、前月との比較が成立しない。
    """
    frame = read_parquet(gold_dir(month) / BY_SECTOR_FILENAME)
    if frame is None or frame.empty:
        return None
    # gold には版を持っていないので、業種コードの集合を代理にする。
    # 集合が変わったなら分類が変わったということ
    return ",".join(sorted({str(c) for c in frame["common12_code"]}))


def build_entry(month: str, *, previous: str | None = None) -> MonthEntry:
    """1か月分の changelog エントリを作る。"""
    prev = previous if previous is not None else previous_run_id(month)
    entry = MonthEntry(month=month, previous_month=prev)

    now_versions = _versions(month)
    before_versions = _versions(prev)
    if not before_versions:
        # **初月を「全部が変更」として出さない。** 前月が無いだけであって、
        # 版が上がったわけではない。ここで差分を取ると初回の changelog が
        # 意味の無い羅列になり、本当の変更が埋もれる
        entry.notes.append(
            Note(
                "no_previous_manifest",
                f"前月 {prev} の manifest が無いため、計測側の変更を検出していない。"
                "初回の月か、実行記録が失われている",
            )
        )
        return _finish(entry, month, prev)

    for phase in sorted(set(now_versions) | set(before_versions)):
        after = now_versions.get(phase, {})
        before = before_versions.get(phase, {})
        for key in sorted(set(after) | set(before)):
            a, b = after.get(key), before.get(key)
            if a == b:
                continue
            change = VersionChange(phase=phase, key=key, before=b, after=a)
            if key == "config_hash":
                entry.config_changes.append(change)
            else:
                entry.version_changes.append(change)

    return _finish(entry, month, prev)


def _finish(entry: MonthEntry, month: str, prev: str) -> MonthEntry:
    """業種写像と主要指標の比較。版の差分の有無に関わらず必要。"""
    now_map = _industry_map_version(month)
    before_map = _industry_map_version(prev)
    entry.industry_map = (before_map, now_map)
    if before_map is not None and now_map is not None and before_map != now_map:
        entry.industry_map_changed = True
        entry.notes.append(
            Note(
                "industry_map_changed",
                "業種の分類集合が変わっている。**写像の版が変わると前月との比較が"
                "成立しない。** 同一版で再集計するか、比較しない旨を明記すること",
            )
        )

    now_stats = _overall(month)
    before_stats = _overall(prev)
    for population_id in sorted(set(now_stats) & set(before_stats)):
        a, b = now_stats[population_id], before_stats[population_id]
        for metric in TRACKED_METRICS:
            after = a.get(metric)
            before = b.get(metric)
            if after is None or before is None:
                continue
            if int(after) == int(before):
                continue
            entry.metric_changes.append(
                MetricChange(
                    population_id=population_id,
                    metric=metric,
                    before=int(before),
                    after=int(after),
                )
            )

    if entry.has_measurement_change and entry.metric_changes:
        entry.notes.append(
            Note(
                "measurement_and_metrics_moved",
                "計測側の変更と数字の変化が同じ月に起きている。**この月の増減を"
                "実態の変化として読んではいけない。** どちらの寄与かを人が書くこと",
            )
        )
    return entry


#: ページの文言。**言語ごとに持つ。** 英語版だけ変更履歴が無いと、
#: 英語で読んだ人は「数字が動いた理由」を確かめる場所を持たないことになる。
PAGE_TEXT: dict[str, dict[str, str]] = {
    "ja": {
        "title": "# 変更履歴",
        "intro": (
            "計測方法や集計定義の変更を記録する。数字が前月と比べて動いたとき、\n"
            "**実態が変わったのか計測が変わったのか**を区別できるようにするためである。"
        ),
        "generated": "この一覧は `mailauth changelog` が生成している（{stamp}）。",
        "human": (
            "版の変化は機械が検出するが、**それが数字にどう影響したかは人が書く。**\n"
            "「解釈」の欄が空いている月は、まだ書かれていないということである。"
        ),
        "measurement_changes": "### 計測側の変更",
        "no_measurement_change": "計測側の変更はない。",
        "config_changed": "`{phase}` の設定が変わった （{before} → {after}）",
        "version_changed": "`{phase}` の {key}: `{before}` → `{after}`",
        "none_value": "(なし)",
        "industry_changed": "**業種の分類集合が変わった**（前月との比較が成立しない）",
        "interpretation": "> 解釈: ",
        "metric_changes": "### 主要指標の増減",
        "metric_header": "| 母集団 | 指標 | 前月 | 当月 | 差 |",
        "policy_title": "## 記録の方針",
        "policy_body": (
            "集計定義を変えた場合、その月の数字と前月の数字は直接比較できない。\n"
            "過去の集計は遡って作り直さず、履歴として残す。"
        ),
    },
    "en": {
        "title": "# Change log",
        "intro": (
            "Changes to how the measurement is made, or to how it is aggregated,\n"
            "are recorded here. When a figure moves against the previous month,\n"
            "**this is what tells you whether the world changed or the measurement did.**"
        ),
        "generated": "This list is generated by `mailauth changelog` ({stamp}).",
        "human": (
            "Version changes are detected by machine, but **what they did to the\n"
            "figures has to be written by a person.** A month whose "
            "\u201cinterpretation\u201d line is blank is one nobody has written up yet."
        ),
        "measurement_changes": "### Changes on the measurement side",
        "no_measurement_change": "No change on the measurement side.",
        "config_changed": "`{phase}` configuration changed ({before} → {after})",
        "version_changed": "`{phase}` {key}: `{before}` → `{after}`",
        "none_value": "(none)",
        "industry_changed": (
            "**The set of sector classes changed** "
            "(a comparison with the previous month does not hold)"
        ),
        "interpretation": "> Interpretation: ",
        "metric_changes": "### Movement in the main indicators",
        "metric_header": "| Population | Indicator | Previous | Current | Delta |",
        "policy_title": "## How this is recorded",
        "policy_body": (
            "When an aggregation definition changes, that month's figures cannot be\n"
            "compared directly with the previous month's. Past aggregations are not\n"
            "rebuilt retroactively; they are kept as history."
        ),
    },
}


def to_markdown(
    entries: list[MonthEntry], *, now: dt.datetime | None = None, lang: str = "ja"
) -> str:
    """changelog のページを組む。

    **人が書く欄を空けて出す。** 版が上がったことは機械が言えるが、
    それが数字にどう影響したかは人が書く。空欄が残っていること自体が、
    書くべきことが残っている印になる。
    """
    stamp = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    T = PAGE_TEXT.get(lang, PAGE_TEXT["ja"])
    lines = [
        T["title"],
        "",
        *T["intro"].split("\n"),
        "",
        T["generated"].format(stamp=stamp),
        *T["human"].split("\n"),
        "",
    ]

    for entry in sorted(entries, key=lambda e: e.month, reverse=True):
        lines += [f"## {entry.month}", ""]

        if entry.has_measurement_change:
            lines += [T["measurement_changes"], ""]
            for change in entry.version_changes:
                lines.append(
                    "- "
                    + T["version_changed"].format(
                        phase=change.phase,
                        key=change.key,
                        before=change.before or T["none_value"],
                        after=change.after or T["none_value"],
                    )
                )
            for change in entry.config_changes:
                lines.append(
                    "- "
                    + T["config_changed"].format(
                        phase=change.phase,
                        before=_short(change.before),
                        after=_short(change.after),
                    )
                )
            if entry.industry_map_changed:
                lines.append("- " + T["industry_changed"])
            lines += ["", T["interpretation"], ""]
        else:
            lines += [T["no_measurement_change"], ""]

        if entry.metric_changes:
            lines += [
                T["metric_changes"],
                "",
                T["metric_header"],
                "|---|---|---|---|---|",
            ]
            for change in entry.metric_changes:
                sign = "+" if change.delta > 0 else ""
                lines.append(
                    f"| {change.population_id} | {change.metric} | {change.before} "
                    f"| {change.after} | {sign}{change.delta} |"
                )
            lines.append("")

        for note in entry.notes:
            # 英語版は鍵から引き直す。**日本語をそのまま出さない**
            text = str(note)
            key = getattr(note, "key", None)
            if lang == "en" and key in NOTES_EN:
                text = NOTES_EN[key].format(prev=entry.previous_month)
            lines.append(f"- {text}")
        if entry.notes:
            lines.append("")

    lines += [T["policy_title"], "", *T["policy_body"].split("\n"), ""]
    return "\n".join(lines)


def _short(value: str | None) -> str:
    if not value:
        return "(なし)"
    return f"`{value[:19]}…`" if len(value) > 20 else f"`{value}`"


def build(months: list[str] | None = None) -> list[MonthEntry]:
    targets = months if months is not None else available_months()
    return [build_entry(month) for month in targets]


def to_json(entries: list[MonthEntry]) -> str:
    payload = [
        {
            "month": e.month,
            "previous_month": e.previous_month,
            "has_measurement_change": e.has_measurement_change,
            "version_changes": [
                {"phase": c.phase, "key": c.key, "before": c.before, "after": c.after}
                for c in e.version_changes
            ],
            "config_changes": [
                {"phase": c.phase, "before": c.before, "after": c.after}
                for c in e.config_changes
            ],
            "industry_map_changed": e.industry_map_changed,
            "metric_changes": [
                {
                    "population_id": c.population_id,
                    "metric": c.metric,
                    "before": c.before,
                    "after": c.after,
                    "delta": c.delta,
                }
                for c in e.metric_changes
            ],
            "notes": e.notes,
        }
        for e in entries
    ]
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
