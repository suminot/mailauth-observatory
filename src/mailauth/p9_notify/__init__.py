"""P9 事前通知の準備（DESIGN.md Sprint 9）。

**このモジュールはメールを送らない。** 送信の可否は運用上の判断であり、
コードが勝手に始めてよいものではない。ここが作るのは「通知計画」までで、
誰にどの文面を送るかを人が確認できる形にして止まる。

準備するもの
  - 通知先の候補（security.txt / RFC 2142 エイリアス / RDAP の abuse 窓口）
  - 通知文面のテンプレート描画
  - オプトアウトの登録簿
  - **送信元自身のメール認証が完全準拠かの自己検査**

最後の項目が要件として重要である。メール認証の不備を指摘する通知を、
自分の SPF/DKIM/DMARC が不備な状態で送ると、その一通で信頼を失う。
"""

from .contacts import ContactCandidate, ContactSet, discover
from .optout import OptOutRegistry
from .plan import NotifyPlan, NotifyTarget, PlanBlockedError, build_plan
from .runner import PHASE, MissingInputError, build_targets, run
from .selfcheck import SelfComplianceError, SelfComplianceResult, check_self
from .template import Finding, TemplateError, findings_from_fact, render

__all__ = [
    "ContactCandidate",
    "ContactSet",
    "discover",
    "OptOutRegistry",
    "NotifyPlan",
    "NotifyTarget",
    "PlanBlockedError",
    "build_plan",
    "SelfComplianceError",
    "SelfComplianceResult",
    "check_self",
    "Finding",
    "TemplateError",
    "findings_from_fact",
    "render",
    "PHASE",
    "MissingInputError",
    "build_targets",
    "run",
]
