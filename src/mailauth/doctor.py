"""いま何をすればいいかを1つだけ答える（運営者の手数を減らすための診断）。

OWNER-TASKS.md には運営者の作業が9件ある。**9件を同時に見せるのは設計の
失敗である。** 依存関係があるので実際に着手できるのは常に1〜2件で、残りは
「まだやらなくていいもの」なのに、一覧に並んでいると全部が未完了に見える。

このモジュールは持っている認証情報と生成物の状態から、

1. いま実際に動かせる母集団
2. 足りない鍵と、その取得コスト（すぐ / 登録 / 申請して待つ）
3. **次の一手を1つだけ**

を出す。**鍵が1つも無い状態でも「今日できること」が必ず1つ出る**ことが
このモジュールの要件である。米国母集団は SEC EDGAR（パブリックドメイン）と
Wikidata（CC0）だけで組めるので、必要なのは連絡先メールアドレス1つ
（SEC が User-Agent に連絡先を求めるため）であり、登録も申請も要らない。

診断は読むだけで、**何も書き換えない。**
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import credential, list_populations, load_yaml
from .offload import DESTINATION_ENV, DESTINATION_LABELS, REQUIRED_DESTINATIONS
from .paths import gold_root, runs_root

# --------------------------------------------------------------------------
# 認証情報の目録
# --------------------------------------------------------------------------

#: 取得コスト。**「無料」かどうかではなく「今日手に入るか」で分ける。**
#: 申請制のものを待っている間に他が止まると、体感の総所要時間が跳ね上がる。
COST_IMMEDIATE = "immediate"
COST_SIGNUP = "signup"
COST_APPLICATION = "application"

COST_LABELS = {
    COST_IMMEDIATE: "すぐ（登録不要）",
    COST_SIGNUP: "登録すれば即日",
    COST_APPLICATION: "申請して待つ",
}

#: 並べ替え用。小さいほど早く手に入る
COST_ORDER = {COST_IMMEDIATE: 0, COST_SIGNUP: 1, COST_APPLICATION: 2}


@dataclass(frozen=True)
class Credential:
    name: str
    label: str
    where: str
    cost: str
    stops: str

    @property
    def cost_label(self) -> str:
        return COST_LABELS.get(self.cost, self.cost)

    def present(self) -> bool:
        return credential(self.name) is not None


CREDENTIALS: dict[str, Credential] = {
    c.name: c
    for c in (
        Credential(
            name="MAILAUTH_CONTACT_EMAIL",
            label="SEC に名乗る連絡先",
            where="あなたのメールアドレス",
            cost=COST_IMMEDIATE,
            stops="米国母集団が理由を添えて停止する",
        ),
        Credential(
            name="MAILAUTH_EDINET_SUBSCRIPTION_KEY",
            label="EDINET API キー",
            where="https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1",
            cost=COST_SIGNUP,
            stops="国内の母集団が取れない",
        ),
        Credential(
            name="MAILAUTH_GBIZINFO_TOKEN",
            label="gBizINFO トークン",
            where="https://info.gbiz.go.jp/api/index.html",
            cost=COST_APPLICATION,
            stops="国内企業の official_url が全社欠損し、P2 の候補生成が起点を失う",
        ),
        Credential(
            name="MAILAUTH_HOUJIN_BANGOU_APP_ID",
            label="法人番号 API の ID",
            where="https://www.houjin-bangou.nta.go.jp/webapi/",
            cost=COST_SIGNUP,
            stops="商号の裏取りをスキップする（計測そのものは動く）",
        ),
    )
}

#: official_url をどこから取るか。**値が None の経路は認証情報が要らない。**
#: 認証不要の経路が1つでも enrich にあるなら、鍵付きの経路は必須ではない。
OFFICIAL_URL_SOURCES: dict[str, str | None] = {
    "gbizinfo": "MAILAUTH_GBIZINFO_TOKEN",
    "wikidata_identity": None,
}

#: 母集団の一次ソースが要求する鍵
PRIMARY_SOURCE_CREDENTIAL = {
    "edinet_code_list": "MAILAUTH_EDINET_SUBSCRIPTION_KEY",
    "sec_edgar": "MAILAUTH_CONTACT_EMAIL",
}

#: official_url 以外の enrich が使う鍵。欠けても計測は通る
OPTIONAL_ENRICH_CREDENTIAL = {
    "houjin_bangou": "MAILAUTH_HOUJIN_BANGOU_APP_ID",
}


# --------------------------------------------------------------------------
# 母集団ごとの可否
# --------------------------------------------------------------------------


@dataclass
class PopulationReadiness:
    id: str
    label: str
    country: str
    config_path: str
    implemented: bool
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.implemented and not self.missing_required

    @property
    def cheapest_cost(self) -> int:
        """動かせるようにするまでに一番待たされる鍵のコスト。"""
        if not self.missing_required:
            return -1
        return max(COST_ORDER.get(CREDENTIALS[n].cost, 9) for n in self.missing_required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "country": self.country,
            "config": self.config_path,
            "implemented": self.implemented,
            "runnable": self.runnable,
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
        }


def _population_readiness() -> list[PopulationReadiness]:
    out: list[PopulationReadiness] = []
    for cfg in list_populations():
        if not cfg.enabled:
            continue
        src = cfg.source
        required: list[str] = []
        optional: list[str] = []

        primary_key = PRIMARY_SOURCE_CREDENTIAL.get(src.primary)
        if primary_key and not CREDENTIALS[primary_key].present():
            required.append(primary_key)

        # constraints で明示されていれば一次ソースによらず必須
        if cfg.constraints.get("requires_contact_email"):
            name = "MAILAUTH_CONTACT_EMAIL"
            if not CREDENTIALS[name].present() and name not in required:
                required.append(name)

        enrich = list(src.enrich)

        # official_url は複数経路がありうる。認証不要の経路があれば鍵は任意になる
        url_routes = [e for e in enrich if e in OFFICIAL_URL_SOURCES]
        if url_routes:
            keyless = any(OFFICIAL_URL_SOURCES[e] is None for e in url_routes)
            # 閾値が緩い（大半が欠けてよい）なら、そもそも必須にしない
            threshold = cfg.acceptance.max_missing_rate.get("official_url")
            tolerant = threshold is not None and threshold >= 0.5
            for route in url_routes:
                key = OFFICIAL_URL_SOURCES[route]
                if key is None or CREDENTIALS[key].present():
                    continue
                if keyless or tolerant:
                    optional.append(key)
                else:
                    required.append(key)

        for route in enrich:
            key = OPTIONAL_ENRICH_CREDENTIAL.get(route)
            if key and not CREDENTIALS[key].present() and key not in optional:
                optional.append(key)

        out.append(
            PopulationReadiness(
                id=cfg.id,
                label=cfg.label,
                country=cfg.country,
                config_path=str(cfg.source_path.relative_to(_repo())) if cfg.source_path else "",
                implemented=cfg.implemented,
                missing_required=required,
                missing_optional=optional,
            )
        )
    return out


def _repo() -> Path:
    from .paths import repo_root

    return repo_root()


# --------------------------------------------------------------------------
# 生成物の状態
# --------------------------------------------------------------------------


def _measured_months() -> list[str]:
    root = gold_root()
    if not root.is_dir():
        return []
    months = []
    for p in sorted(root.glob("month=*")):
        if p.is_dir() and any(p.iterdir()):
            months.append(p.name.split("=", 1)[1])
    return months


def _run_reports() -> list[str]:
    root = runs_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# --------------------------------------------------------------------------
# あとで効いてくるもの（無くても計測は回る）
# --------------------------------------------------------------------------


@dataclass
class LaterItem:
    key: str
    label: str
    done: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "done": self.done, "detail": self.detail}


def _deploy_state() -> LaterItem:
    names = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_PAGES_PROJECT")
    missing = [n for n in names if not os.environ.get(n)]
    if not missing:
        return LaterItem("deploy", "サイトの公開先（Cloudflare Pages）", True, "設定済み")
    return LaterItem(
        "deploy",
        "サイトの公開先（Cloudflare Pages）",
        False,
        "未設定: " + ", ".join(missing) + "。揃うまで deploy.yml は何もしない",
    )


def _offload_state() -> LaterItem:
    configured = []
    for name, envs in DESTINATION_ENV.items():
        if all(os.environ.get(e) for e in envs):
            configured.append(DESTINATION_LABELS.get(name, name))
    if len(configured) >= REQUIRED_DESTINATIONS:
        return LaterItem("offload", "bronze の退避先", True, " / ".join(configured))
    if configured:
        return LaterItem(
            "offload",
            "bronze の退避先",
            False,
            f"{configured[0]} のみ。第9章は別事業者を含む {REQUIRED_DESTINATIONS} か所を求めている",
        )
    return LaterItem(
        "offload",
        "bronze の退避先",
        False,
        "未設定。bronze を失うと過去の再解釈ができなくなる",
    )


def _tier2_state() -> LaterItem:
    try:
        pub = load_yaml("configs/publish.yaml")
    except FileNotFoundError:
        return LaterItem("tier2", "第2層（個社名付き明細）", False, "configs/publish.yaml が無い")
    t2 = pub.get("tier2") or {}
    if t2.get("enabled"):
        return LaterItem("tier2", "第2層（個社名付き明細）", True, "公開中")
    return LaterItem(
        "tier2",
        "第2層（個社名付き明細）",
        False,
        "無効のまま。事前通知から最低30日とアクセス制御が揃うまで P8 が止める",
    )


def _dkim_l3_state() -> LaterItem:
    try:
        cfg = load_yaml("configs/measure.yaml")
    except FileNotFoundError:
        cfg = {}
    dkim = cfg.get("dkim") or {}
    on = bool(dkim.get("l3_on_miss"))
    return LaterItem(
        "dkim_l3",
        "DKIM 辞書 L3（GPL-3.0 の判断）",
        on,
        "無効のまま。1回計測するまで判断材料が無いので、今は触らなくてよい",
    )


def _later_items() -> list[LaterItem]:
    return [_offload_state(), _deploy_state(), _dkim_l3_state(), _tier2_state()]


# --------------------------------------------------------------------------
# 次の一手
# --------------------------------------------------------------------------


@dataclass
class NextAction:
    headline: str
    why: str
    steps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"headline": self.headline, "why": self.why, "steps": list(self.steps)}


def _next_action(
    pops: list[PopulationReadiness],
    months: list[str],
    later: list[LaterItem],
) -> NextAction:
    runnable = [p for p in pops if p.runnable]

    # まだ一度も測っていない。**ここで9件の作業を見せない。**
    if not months:
        if runnable:
            target = min(runnable, key=lambda p: (0 if p.country == "US" else 1, p.id))
            return NextAction(
                headline=f"{target.id} を limit 50 で1回だけ回す",
                why=(
                    "計測を1回通すと、辞書の判断（GPL-3.0）も未知 MX の作業リストも"
                    "材料が出る。先に他の8件をやっても判断材料が無い"
                ),
                steps=[
                    "Actions → 月次計測 → Run workflow",
                    f"population: {target.config_path}",
                    "limit: 50",
                    "30分ほどで終わる。通ったら limit を空欄にして本番",
                ],
            )

        # 動かせる母集団が無い。**一番早く手に入る鍵を1つだけ出す。**
        candidates = [p for p in pops if p.implemented and p.missing_required]
        if not candidates:
            return NextAction(
                headline="実装済みの母集団が無い",
                why="configs/populations/ の implemented を確認する",
                steps=[],
            )
        target = min(candidates, key=lambda p: (p.cheapest_cost, len(p.missing_required)))
        keys = [CREDENTIALS[n] for n in target.missing_required]
        keys.sort(key=lambda c: COST_ORDER.get(c.cost, 9))
        first = keys[0]
        return NextAction(
            headline=f"{first.name} を入れる ── {first.cost_label}",
            why=f"これが入ると {target.id} が回せるようになる。取得元: {first.where}",
            steps=[
                "GitHub → Settings → Secrets and variables → Actions",
                "New repository secret",
                f"Name: {first.name}",
                *(
                    [f"（同じ画面で {k.name} も。{k.cost_label}）" for k in keys[1:]]
                    if len(keys) > 1
                    else []
                ),
            ],
        )

    # 1か月ぶんある。次は公開
    deploy = next(i for i in later if i.key == "deploy")
    if not deploy.done:
        return NextAction(
            headline="Cloudflare Pages につなぐ",
            why=f"gold が {len(months)} か月ぶんある。数字はあるのに公開先が無い状態",
            steps=[
                "Cloudflare → Workers & Pages → Create → Pages → Direct Upload",
                "GitHub の secrets に CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / "
                "CLOUDFLARE_PAGES_PROJECT",
                "main への push で deploy.yml が動く",
            ],
        )

    offload = next(i for i in later if i.key == "offload")
    if not offload.done:
        return NextAction(
            headline="bronze の退避先を用意する",
            why=offload.detail,
            steps=[
                "Cloudflare → R2 → Manage R2 API Tokens",
                "副は別事業者（Backblaze B2 / Wasabi / AWS S3）",
            ],
        )

    return NextAction(
        headline="月次の確認だけでよい",
        why=f"gold {len(months)} か月ぶん。公開も退避も設定済み",
        steps=[
            "Actions に「月次計測」の実行があるか",
            "runs/YYYY-MM-worklist.md の未知 MX ホスト",
            "site/src/changelog.md の「解釈」欄",
        ],
    )


# --------------------------------------------------------------------------
# まとめ
# --------------------------------------------------------------------------


@dataclass
class Report:
    populations: list[PopulationReadiness]
    months: list[str]
    runs: list[str]
    later: list[LaterItem]
    next_action: NextAction

    @property
    def runnable(self) -> list[PopulationReadiness]:
        return [p for p in self.populations if p.runnable]

    @property
    def blocked(self) -> list[PopulationReadiness]:
        return [p for p in self.populations if p.implemented and not p.runnable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "populations": [p.to_dict() for p in self.populations],
            "measured_months": list(self.months),
            "runs": list(self.runs),
            "later": [i.to_dict() for i in self.later],
            "next_action": self.next_action.to_dict(),
        }


def diagnose() -> Report:
    pops = _population_readiness()
    months = _measured_months()
    later = _later_items()
    return Report(
        populations=pops,
        months=months,
        runs=_run_reports(),
        later=later,
        next_action=_next_action(pops, months, later),
    )


def render(report: Report) -> str:
    """人が読む形。**次の一手を先頭に置く。**"""
    lines: list[str] = []
    na = report.next_action

    lines.append("次の一手")
    lines.append(f"  → {na.headline}")
    if na.why:
        lines.append(f"     {na.why}")
    for i, step in enumerate(na.steps, 1):
        lines.append(f"     {i}. {step}")
    lines.append("")

    lines.append("いま動かせる母集団")
    if report.runnable:
        for p in report.runnable:
            extra = ""
            if p.missing_optional:
                extra = "（任意の鍵が未設定: " + ", ".join(p.missing_optional) + "）"
            lines.append(f"  ○ {p.id:<16} {p.label}{extra}")
    else:
        lines.append("  （まだ無い）")
    lines.append("")

    if report.blocked:
        lines.append("鍵が足りない母集団")
        for p in report.blocked:
            lines.append(f"  × {p.id:<16} {p.label}")
            for name in p.missing_required:
                c = CREDENTIALS[name]
                lines.append(f"      {c.name}  {c.cost_label}  {c.where}")
                lines.append(f"        無いと: {c.stops}")
        lines.append("")

    lines.append("計測の実績")
    if report.months:
        span = f"{report.months[0]} 〜 {report.months[-1]}"
        lines.append(f"  gold: {len(report.months)} か月ぶん（{span}）")
    else:
        lines.append("  gold: まだ無い")
    lines.append("")

    lines.append("あとでよいもの（無くても計測は回る）")
    for item in report.later:
        mark = "○" if item.done else "・"
        lines.append(f"  {mark} {item.label}")
        lines.append(f"      {item.detail}")

    return "\n".join(lines)
