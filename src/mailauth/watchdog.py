"""起動漏れの見張り役（2026-11-08）。

GitHub Actions の schedule は遅れたり落ちたりする。月次計測・CT の補充は
「終わり際に次を頼む」連鎖でもつないでいるが、それも切れる：

- 補充の毎時の schedule は、30 時間で4回しか起動しなかったことがある（2026-11-02）
- 同じ concurrency グループで待てる依頼は1本だけで、後から来た依頼が前の依頼を
  取り消す（2026-11-08、補充の依頼が取り消された）
- 補充の連鎖は「積み残しがある間」しか続かない。CertSpotter の枠（1時間ごとに戻る）
  を使い切るには、積み残しが無くても毎時1回は回す必要がある

ここでは**何を頼むべきかだけを決める**（純粋な関数。頼むのはワークフロー）。
見張り役のワークフローも schedule なので、それ自体が抜けることがある ──
補充の毎時の schedule・連鎖・作業セッションの定期チェックと重ねて、
どれか1つが動けば止まらないようにする。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

ACTIVE = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})

#: 補充がこれより長く起動していなければ頼む（CertSpotter の枠は1時間ごとに戻る）
BACKFILL_GAP = dt.timedelta(minutes=50)
#: 月次計測を、その月のいつから「来ているはず」と見るか（月初からの経過）
MONTHLY_DUE = {
    "jp-all-listed": dt.timedelta(hours=14),  # 1日 02:00 の schedule ＋ 余裕
    "us-all-listed": dt.timedelta(days=1, hours=14),  # 2日 03:23 の schedule ＋ 余裕
    "jp-all-listed-group": dt.timedelta(days=1, hours=20),  # 米国の後
}
#: 同じ母集団を頼み直すのは、前に頼んでからこれだけ空けてから（落ち続ける実行を
#: 頼み続けない）
MONTHLY_RETRY_GAP = dt.timedelta(hours=12)


@dataclass
class Run:
    workflow: str
    status: str
    conclusion: str | None
    created_at: dt.datetime
    title: str = ""


@dataclass
class Action:
    workflow: str
    reason: str
    inputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"workflow": self.workflow, "reason": self.reason, "inputs": self.inputs}


def parse_runs(rows: list[dict]) -> list[Run]:
    out = []
    for r in rows:
        created = dt.datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
        out.append(Run(workflow=str(r["workflow"]), status=str(r.get("status") or ""),
                       conclusion=r.get("conclusion"), created_at=created,
                       title=str(r.get("title") or r.get("display_title") or "")))
    return out


def decide(
    now: dt.datetime,
    runs: list[Run],
    *,
    month: str,
    gold_populations: set[str],
    has_group_list: bool,
) -> list[Action]:
    """頼むべき起動。**頼みすぎない**：走っている・待っているものがあれば頼まない。"""
    actions: list[Action] = []

    backfill = [r for r in runs if r.workflow == "ct-backfill.yml"]
    if not any(r.status in ACTIVE for r in backfill):
        last = max((r.created_at for r in backfill), default=None)
        if last is None or now - last > BACKFILL_GAP:
            ago = (
                "一度も無い" if last is None
                else f"{int((now - last).total_seconds() // 60)} 分前"
            )
            actions.append(Action("ct-backfill.yml", f"補充の最後の起動が {ago}",
                                  {"run_id": month}))

    monthly = [r for r in runs if r.workflow == "monthly.yml"]
    if any(r.status in ACTIVE for r in monthly):
        return actions  # 月次計測は1本ずつ。走っている間は頼まない
    start = dt.datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=dt.UTC)
    for pop, due in MONTHLY_DUE.items():
        if pop == "jp-all-listed-group" and not has_group_list:
            continue
        if pop in gold_populations or now < start + due:
            continue
        tried = [r for r in monthly if f"{pop}.yaml" in r.title and month in r.title]
        if tried and now - max(r.created_at for r in tried) < MONTHLY_RETRY_GAP:
            continue
        actions.append(Action(
            "monthly.yml",
            f"{month} の {pop} の gold が無い（月初から {now - start} 経過）",
            {"run_id": month, "population": f"configs/populations/{pop}.yaml",
             "publish": "yes", "start_from": "p1"},
        ))
        break  # 月次計測は1本ずつ（同じ concurrency グループで待てるのは1本）
    return actions
