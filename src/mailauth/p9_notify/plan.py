"""通知計画の組み立て（DESIGN.md Sprint 9）。

**この関数は送らない。** 誰にどの文面をどの順で送るかを決めて、人が読める
形で止まる。送信は運用上の判断であり、コードが勝手に始めてよいものではない。

計画を作る前に通る門が4つある。どれも警告ではなく停止である。

  1. **送信元自身のメール認証が完全準拠であること**（`selfcheck`）。
     不備を指摘する通知を、自分が不備な状態で送ると信頼を失う
  2. **オプトアウト登録簿が読めていること**（`optout`）。読めないことを
     「誰も断っていない」として扱うと、断った相手に送ってしまう
  3. **訂正申告の窓口があること。** 指摘だけして訂正を受け付けないのは
     一方的である
  4. **文面が語彙規約・営業要素・URL 真正性の検査を通ること**（`template`）

期待値についても計画に書き出す。DR-18 の実測では SPF 不備通知の2週間後
是正率は **3.3%**（Czybik et al., IMC 2023、111,951通）である。到達率も
RFC 2142 経由で 24.16%（バウンスしない割合）にとどまる。**送れば直る
という前提で運用を組むと、結果を見て「失敗した」と誤読する。**
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .contacts import ContactSet
from .optout import OptOutRegistry
from .selfcheck import SelfComplianceResult, require_compliant
from .template import (
    CORRECTION_DAYS_RECOMMENDED,
    Finding,
    RenderedMessage,
    TemplateError,
    findings_from_fact,
    render,
)

#: 毎秒1通（DESIGN.md Sprint 9）。相手の受信側に負荷をかけない
RATE_PER_SEC = 1.0

#: DR-18 の実測値。計画に併記して期待値を較正する
EXPECTED_REACH = 0.2416
EXPECTED_REMEDIATION_2W = 0.033


class PlanBlockedError(RuntimeError):
    """前提が揃っていないため計画を作らない。"""


@dataclass
class NotifyTarget:
    """通知の候補1件。"""

    domain: str
    #: P5 の Fact 行（dict）。**観測できていない行は対象にしない**
    fact: dict
    contacts: ContactSet
    entity_name: str | None = None
    entity_id: str | None = None


@dataclass
class PlannedMessage:
    domain: str
    address: str
    contact_source: str
    contact_confidence: str
    subject: str
    body: str
    findings: list[Finding] = field(default_factory=list)
    #: 計画開始からの秒数。毎秒1通のレート制限を満たす順序
    send_after_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "address": self.address,
            "contact_source": self.contact_source,
            "contact_confidence": self.contact_confidence,
            "subject": self.subject,
            "body": self.body,
            "send_after_sec": self.send_after_sec,
            "findings": [f.code for f in self.findings],
        }


@dataclass
class SkippedTarget:
    domain: str
    reason: str
    detail: str | None = None


@dataclass
class ManualChannel:
    """自動送信の対象外だが、人が手で出す先がある。

    **「送れない」と「窓口が無い」は別である。** security.txt にフォームだけを
    公示している組織は窓口を持っている。自動送信の対象から外した結果として
    連絡そのものが落ちないよう、ここに残す。
    """

    domain: str
    urls: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


@dataclass
class NotifyPlan:
    """人が確認するための計画。**これ自体は何も送らない。**"""

    sender_domain: str
    measured_month: str
    notified_on: dt.date
    correction_days: int
    messages: list[PlannedMessage] = field(default_factory=list)
    skipped: list[SkippedTarget] = field(default_factory=list)
    #: 自動送信の対象外だが人が手で出す先がある分
    manual: list[ManualChannel] = field(default_factory=list)
    rate_per_sec: float = RATE_PER_SEC
    self_check: dict | None = None
    optout: dict | None = None
    notes: list[str] = field(default_factory=list)
    #: **常に False。** 送信はこのモジュールの外の判断である
    sendable: bool = False

    @property
    def count(self) -> int:
        return len(self.messages)

    @property
    def duration_sec(self) -> float:
        return self.messages[-1].send_after_sec if self.messages else 0.0

    @property
    def earliest_publish(self) -> dt.date:
        """第2層を出せる最短の日。訂正期間の経過後。"""
        return self.notified_on + dt.timedelta(days=self.correction_days)

    def skipped_by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.skipped:
            out[item.reason] = out.get(item.reason, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "sender_domain": self.sender_domain,
            "measured_month": self.measured_month,
            "notified_on": self.notified_on.isoformat(),
            "correction_days": self.correction_days,
            "earliest_publish": self.earliest_publish.isoformat(),
            "count": self.count,
            "rate_per_sec": self.rate_per_sec,
            "duration_sec": self.duration_sec,
            "sendable": self.sendable,
            "expected": self.expectations(),
            "skipped": [
                {"domain": s.domain, "reason": s.reason, "detail": s.detail}
                for s in self.skipped
            ],
            "skipped_by_reason": self.skipped_by_reason(),
            "manual": [
                {"domain": m.domain, "urls": m.urls, "findings": m.findings}
                for m in self.manual
            ],
            "self_check": self.self_check,
            "optout": self.optout,
            "messages": [m.to_dict() for m in self.messages],
            "notes": self.notes,
        }

    def expectations(self) -> dict:
        """DR-18 の実測値から見た見込み。**楽観しないために出す。**"""
        return {
            "reach_rate": EXPECTED_REACH,
            "remediation_rate_2w": EXPECTED_REMEDIATION_2W,
            "expected_delivered": round(self.count * EXPECTED_REACH, 1),
            "expected_remediated_2w": round(self.count * EXPECTED_REMEDIATION_2W, 1),
            "source": (
                "Soussi/Korczyński (IEEE 2020) / Czybik et al. (IMC 2023, 111,951通)"
            ),
        }

    def to_markdown(self) -> str:
        exp = self.expectations()
        lines = [
            f"# 通知計画 {self.measured_month}",
            "",
            "**この計画は送信を行っていない。** 送信の可否は運用上の判断である。",
            "",
            f"- 差出人: {self.sender_domain}",
            f"- 通知日（予定）: {self.notified_on.isoformat()}",
            f"- 訂正期間: {self.correction_days}日 "
            f"→ 第2層を出せる最短日 {self.earliest_publish.isoformat()}",
            f"- 対象: {self.count}件（毎秒{self.rate_per_sec:g}通で "
            f"約{self.duration_sec / 60:.0f}分）",
            f"- 見込み: 到達 約{exp['expected_delivered']}件"
            f"（{EXPECTED_REACH:.2%}）、2週間後の是正 約"
            f"{exp['expected_remediated_2w']}件（{EXPECTED_REMEDIATION_2W:.1%}）",
            f"  出典: {exp['source']}",
            "",
        ]
        if self.skipped:
            lines += ["## 対象外", "", "| 理由 | 件数 |", "|---|---|"]
            for reason, count in sorted(self.skipped_by_reason().items()):
                lines.append(f"| {reason} | {count} |")
            lines.append("")
        if self.manual:
            lines += [
                "## 人が手で出す先",
                "",
                "**自動送信の対象外だが窓口はある。** security.txt が mailto ではなく",
                "フォームを公示している組織である。`security@` に送るのは公示の否定に",
                "なるため、ここは人が手で出す。",
                "",
                "| ドメイン | 公示された窓口 | 事実 |",
                "|---|---|---|",
            ]
            for item in self.manual:
                lines.append(
                    f"| {item.domain} | {' / '.join(item.urls)} "
                    f"| {', '.join(item.findings)} |"
                )
            lines.append("")
        lines += [
            "## 宛先",
            "",
            "| ドメイン | 宛先 | 経路 | 信頼度 | 事実 |",
            "|---|---|---|---|---|",
        ]
        for message in self.messages:
            codes = ", ".join(f.code for f in message.findings)
            lines.append(
                f"| {message.domain} | {message.address} | {message.contact_source} "
                f"| {message.contact_confidence} | {codes} |"
            )
        lines.append("")
        for note in self.notes:
            lines.append(f"- {note}")
        return "\n".join(lines) + "\n"


def build_plan(
    targets: list[NotifyTarget],
    *,
    sender_domain: str,
    self_check: SelfComplianceResult,
    registry: OptOutRegistry,
    detail_url: str,
    method_url: str,
    correction_contact: str,
    measured_month: str,
    notified_on: dt.date | None = None,
    correction_days: int = CORRECTION_DAYS_RECOMMENDED,
    rate_per_sec: float = RATE_PER_SEC,
    optout_contact: str | None = None,
) -> NotifyPlan:
    """通知計画を作る。**送信はしない。**

    `detail_url` は `{domain}` を含んでいれば各社分に差し替える。含まなければ
    全件が同じ URL を指す（第2層を出す前の段では index を指すことになる）。
    """
    if not sender_domain:
        raise PlanBlockedError("差出人ドメインが指定されていない")

    # 門1: 送信元自身の準拠。**通らなければ計画を作らない**
    require_compliant(self_check)

    # 門2: オプトアウト登録簿。「読めなかった」を「誰も断っていない」にしない
    if not registry.available:
        raise PlanBlockedError(
            "オプトアウト登録簿を読めていない。**「誰も断っていない」とは"
            "解釈しない。** 空でよいならヘッダだけのファイルを置くこと。\n  "
            + "\n  ".join(registry.notes)
        )

    # 門3: 訂正窓口。指摘だけして訂正を受け付けないのは一方的である
    if not correction_contact or "@" not in correction_contact:
        raise PlanBlockedError(
            "訂正申告の窓口が無い。**指摘する側が訂正を受け付けないのは"
            "一方的である。** configs/publish.yaml の "
            "tier2.correction_contact を設定すること"
        )

    plan = NotifyPlan(
        sender_domain=sender_domain,
        measured_month=measured_month,
        notified_on=notified_on or dt.date.today(),
        correction_days=correction_days,
        rate_per_sec=rate_per_sec if rate_per_sec > 0 else RATE_PER_SEC,
        self_check=self_check.to_dict(),
        optout=registry.to_dict(),
    )

    # 企業単位の辞退。1ドメインで断られたら同一企業の他のドメインも外す。
    # **断りの範囲を狭く解釈しない**
    entity_optouts = {
        t.entity_id
        for t in targets
        if t.entity_id and (m := registry.matched(t.domain)) and m.scope == "entity"
    }

    for target in sorted(targets, key=lambda t: t.domain):
        domain = target.domain

        if registry.contains(domain):
            plan.skipped.append(
                SkippedTarget(domain, "オプトアウト", registry.reason(domain))
            )
            continue
        if target.entity_id and target.entity_id in entity_optouts:
            plan.skipped.append(
                SkippedTarget(
                    domain, "オプトアウト", "同一企業の別ドメインが企業単位で辞退"
                )
            )
            continue

        if not target.fact.get("observed"):
            # **観測できなかったものを指摘しない。** 原則5の実害が出る箇所
            plan.skipped.append(
                SkippedTarget(
                    domain,
                    "未観測",
                    "DNS を観測できていない。「設定が無い」ではないため通知しない",
                )
            )
            continue

        findings = findings_from_fact(target.fact)
        if not findings:
            plan.skipped.append(
                SkippedTarget(domain, "指摘事項なし", "観測した範囲では不備が無い")
            )
            continue

        if target.contacts.prefers_web:
            # **公示された窓口を無視して別の口を叩かない。** security.txt に
            # mailto が無くフォームだけが並んでいる組織は「メールではなく
            # ここへ出せ」と公示している。`security@` に送るのはその否定である
            plan.manual.append(
                ManualChannel(
                    domain=domain,
                    urls=list(target.contacts.web_contacts),
                    findings=[f.code for f in findings_from_fact(target.fact)],
                )
            )
            plan.skipped.append(
                SkippedTarget(
                    domain,
                    "公示窓口がメール以外",
                    "security.txt が指定するのはフォーム: "
                    + ", ".join(target.contacts.web_contacts),
                )
            )
            continue

        candidate = target.contacts.best
        if candidate is None:
            plan.skipped.append(
                SkippedTarget(domain, "連絡先なし", "候補が1件も見つからなかった")
            )
            continue

        url = detail_url.format(domain=domain) if "{domain}" in detail_url else detail_url
        try:
            message: RenderedMessage = render(
                domain,
                findings,
                sender_domain=sender_domain,
                detail_url=url,
                method_url=method_url,
                correction_contact=correction_contact,
                measured_month=measured_month,
                entity_name=target.entity_name,
                optout_contact=optout_contact,
                notified_on=plan.notified_on,
                correction_days=correction_days,
            )
        except TemplateError as exc:
            # **文面が作れないものを送信対象に残さない**
            plan.skipped.append(SkippedTarget(domain, "文面の検査に落ちた", str(exc)))
            continue

        plan.messages.append(
            PlannedMessage(
                domain=domain,
                address=candidate.address,
                contact_source=candidate.source,
                contact_confidence=candidate.confidence,
                subject=message.subject,
                body=message.body,
                findings=findings,
                send_after_sec=round(len(plan.messages) / plan.rate_per_sec, 3),
            )
        )

    low = sum(1 for m in plan.messages if m.contact_confidence == "low")
    if low:
        plan.notes.append(
            f"{low}件は RFC 2142 のエイリアス宛である。**存在を確認していない。** "
            f"Top 1M でもバウンスしない割合は {EXPECTED_REACH:.2%} にとどまる"
        )
    plan.notes.append(
        f"2週間後の是正率の実測は {EXPECTED_REMEDIATION_2W:.1%} である"
        "（Czybik et al., IMC 2023）。**送れば直るという前提で運用を組まない。** "
        "法的フレーミングと郵送を併用した事例では 76.3%（Maass et al., USENIX 2021）"
    )
    plan.notes.append(
        "日本市場では「不審メール扱い」「セキュリティ窓口の不在」"
        "「法務部門の関与」が障壁になる。文面の URL は差出人と同一ドメイン上に"
        "あることを機械検査済みだが、それでも読まれない前提で数える"
    )
    plan.notes.append(
        f"第2層を出せる最短日は {plan.earliest_publish.isoformat()} である"
        f"（通知日 + {correction_days}日）。**この日より前に "
        "tier2.enabled を true にしない**"
    )
    return plan
