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

import json
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
            stops="コードリストは鍵なしでも取れる。書類取得 API を使う段で要る",
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

#: 母集団の一次ソースが**無いと取れない**鍵。
#:
#: **EDINET コードリストはここに入らない。** 配布物が認証の要らない静的な zip
#: なので、`MAILAUTH_EDINET_SUBSCRIPTION_KEY` が無くても取得できる（2026-09 の
#: 実行で鍵なしに 11,386 件を取得したことを確認済み。設定にある
#: `send_subscription_key` は、鍵があれば添えるという意味でしかない）。
#: **必須でないものを必須として出すと、着手の障壁を実際より高く見せる。**
PRIMARY_SOURCE_CREDENTIAL = {
    "sec_edgar": "MAILAUTH_CONTACT_EMAIL",
}

#: 一次ソースが「あれば添える」鍵。欠けても取得そのものは通る
OPTIONAL_SOURCE_CREDENTIAL = {
    "edinet_code_list": "MAILAUTH_EDINET_SUBSCRIPTION_KEY",
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

        soft_key = OPTIONAL_SOURCE_CREDENTIAL.get(src.primary)
        if soft_key and not CREDENTIALS[soft_key].present():
            optional.append(soft_key)

        # constraints で明示されていれば一次ソースによらず必須
        if cfg.constraints.get("requires_contact_email"):
            name = "MAILAUTH_CONTACT_EMAIL"
            if not CREDENTIALS[name].present() and name not in required:
                required.append(name)

        enrich = list(src.enrich)

        # official_url は複数経路がありうる。
        #
        # **認証不要の経路があることだけを理由に、鍵付きの経路を任意にしない。**
        # 国内の母集団に wikidata_identity（鍵不要）を足したとき、この判定は
        # 「gBizINFO のトークンは要らない」と言い始めた。**それは支持できない** ──
        # Wikidata の国内被覆率はまだ測れておらず（開発環境から WDQS が 403）、
        # 米国では Wikidata だけだと公式サイトが 25.6% しか埋まらない。
        # 鍵なしで回せば分母が大きく欠けるのに、doctor が「任意」と言うと
        # 運営者はトークン無しで回してしまう。
        #
        # 判断の基準は**その母集団がどれだけの欠損を許しているか**にする。
        # 大半が欠けてよい設定（閾値 50% 以上）なら、その経路はもともと
        # 補助でしかないので任意でよい。ほぼ揃うことを期待している設定なら、
        # 実際に揃えている経路が必須である。
        url_routes = [e for e in enrich if e in OFFICIAL_URL_SOURCES]
        if url_routes:
            threshold = cfg.acceptance.max_missing_rate.get("official_url")
            tolerant = threshold is not None and threshold >= 0.5
            for route in url_routes:
                key = OFFICIAL_URL_SOURCES[route]
                if key is None or CREDENTIALS[key].present():
                    continue
                if tolerant:
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


def _observed_domains(month_dir: Path) -> int | None:
    """その月に実際に観測できたドメイン数。読めなければ None。

    **ファイルがあることと測れたことは別である**（原則5）。2026-09 の実行は
    gold を書き、実行レポートも `status=success` と言ったが、official_url が
    1件も取れなかったため P2 以降の入力が 0 で、`observed_domains` は 0
    だった。**ディレクトリの有無で「計測済み」と判定すると、空の結果を
    根拠に次の作業を勧めることになる。**
    """
    f = month_dir / "stats_overall.parquet"
    if not f.is_file():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(f, columns=["observed_domains"])
    except Exception:
        return None
    if df.empty or "observed_domains" not in df.columns:
        return None
    return int(df["observed_domains"].fillna(0).sum())


#: これ以上欠けたら、公開している数字は母集団の一部の話になる。
#: 国内の受け入れ基準（official_url の欠損 10%）より緩くしてあるのは、
#: **doctor は基準違反ではなく「次に効く一手」を出す道具**だからである
ENTITY_GAP_THRESHOLD = 0.25


def _entity_coverage(month_dir: Path) -> tuple[int, int] | None:
    """(母集団の企業数, 実際に計測に現れた企業数)。読めなければ None。

    **起点ドメインが取れなかった企業は、採用率の分母に入っていない。**
    gold は `total_entities` 社と書くが、そのうち何社が計測に現れたかは
    `entities_with_domains` を見ないと分からない。
    """
    f = month_dir / "stats_overall.parquet"
    if not f.is_file():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(f, columns=["total_entities", "entities_with_domains"])
    except Exception:
        # 列が無い月（この指標より前に回した gold）は「分からない」。
        # **0 と読んで「全社欠けている」と言わない**
        return None
    if df.empty:
        return None
    total = int(df["total_entities"].fillna(0).sum())
    with_domains = int(df["entities_with_domains"].fillna(0).sum())
    return (total, with_domains) if total else None


def _worst_entity_gap() -> tuple[str, int, int] | None:
    """起点が取れていない企業の割合が一番大きい月。無ければ None。

    直近の月だけを見ない。**一度公開した月は残り続ける**ので、欠けの
    大きい月があるなら、それが読み手の目に入る数字である。
    """
    root = gold_root()
    if not root.is_dir():
        return None
    worst: tuple[str, int, int] | None = None
    for p in sorted(root.glob("month=*")):
        if not p.is_dir():
            continue
        coverage = _entity_coverage(p)
        if coverage is None:
            continue
        total, with_domains = coverage
        if (total - with_domains) / total < ENTITY_GAP_THRESHOLD:
            continue
        month = p.name.split("=", 1)[1]
        if worst is None or (total - with_domains) / total > (worst[1] - worst[2]) / worst[1]:
            worst = (month, total, with_domains)
    return worst


def _ct_outage_months() -> list[str]:
    """CT ログの取得先が落ちていた月。新しい順。

    **「候補が少ない月」と「CT が使えなかった月」は別である**（原則5）。
    前者は数字として読めるが、後者は計測が成立していない。放っておくと、
    その月が翌月以降の比較の基準になる。

    2026-09-24 に crt.sh はトップページごと 502 を返していた。相手が
    落ちているときは、待ち方も頼み方も効かない。**出直すしかない。**
    """
    root = runs_root()
    if not root.is_dir():
        return []
    months: list[str] = []
    for d in sorted(root.iterdir(), reverse=True):
        m = d / "p2_candidates" / "_manifest.json"
        if not m.is_file():
            continue
        try:
            payload = json.loads(m.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        codes = {w.get("code") for w in payload.get("warnings", [])}
        if "CT_UPSTREAM_UNAVAILABLE" in codes:
            months.append(d.name)
    return months


def _gold_population(month_dir: Path) -> str | None:
    """その月がどの母集団で回ったか。**空振りの原因は母集団ごとに違う。**"""
    f = month_dir / "stats_overall.parquet"
    if not f.is_file():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(f, columns=["population_id"])
    except Exception:
        return None
    if df.empty:
        return None
    return str(df["population_id"].iloc[0])


def _measured_months() -> list[str]:
    """観測が1件でもある月だけを返す。"""
    return [m for m, n, _ in _gold_months() if n]


def _gold_months() -> list[tuple[str, int, str | None]]:
    """gold にある月、その月の観測ドメイン数、回した母集団。"""
    root = gold_root()
    if not root.is_dir():
        return []
    out: list[tuple[str, int, str | None]] = []
    for p in sorted(root.glob("month=*")):
        if not p.is_dir() or not any(p.iterdir()):
            continue
        out.append((p.name.split("=", 1)[1], _observed_domains(p) or 0, _gold_population(p)))
    return out


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
    empty_months: list[tuple[str, str | None]] | None = None,
) -> NextAction:
    runnable = [p for p in pops if p.runnable]
    empty_months = empty_months or []

    # まだ一度も測っていない。**ここで9件の作業を見せない。**
    if not months:
        # 回ったのに1件も観測できていない月がある。**放っておくと毎月これが
        # 積み上がる**（月次は自動で回り、実行レポートは success と言う）。
        if empty_months and not runnable:
            month, pop_id = empty_months[-1]
            # **空振りの原因は、その月に回した母集団のものを出す。**
            # 全母集団から一番安い鍵を選ぶと、関係のない鍵を指すことになる
            blocked = next((p for p in pops if p.id == pop_id and p.missing_required), None)
            steps = [f"runs/{month}.md の警告を見る（NO_SEED_DOMAINS が出ているはず）"]
            if blocked:
                keys = sorted(
                    blocked.missing_required, key=lambda n: COST_ORDER.get(CREDENTIALS[n].cost, 9)
                )
                c = CREDENTIALS[keys[0]]
                steps.append(
                    f"{c.name} を入れると {blocked.id} が通る（{c.cost_label}・{c.where}）"
                )
            steps.append(
                "待つ間に測るなら us-all-listed。"
                "要るのは MAILAUTH_CONTACT_EMAIL だけで登録も申請も要らない"
            )
            return NextAction(
                headline=f"{month} は回ったが1件も測れていない",
                why=(
                    "gold は書かれたが observed_domains が 0。"
                    "起点になる official_url が取れておらず、P2 以降の入力が空になっている"
                ),
                steps=steps,
            )
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

    # **相手が落ちていた月は、分母の話より先に出す。**
    # 分母の欠けは「そういう月だった」と読めるが、こちらは計測が成立して
    # いない。しかも**キャッシュが温かいうちに流し直す**のが一番安い
    outages = _ct_outage_months()
    if outages:
        month = outages[0]
        return NextAction(
            headline=f"{month} は CT ログの取得先が落ちている最中に回っている",
            why=(
                "crt.sh が応答しなかったため、その月の CT 由来の候補は"
                "キャッシュにあった分だけになっている。**少ないのは実態ではない。**"
                "このまま置くと、その月が翌月以降の比較の基準になる（原則5）"
            ),
            steps=[
                "https://crt.sh/ が 200 を返すか見る",
                f"戻っていたら Actions → 月次計測 → run_id: {month} で流し直す",
                "**同じ run_id なら、積み上がっているキャッシュはそのまま使われる**",
                f"runs/{month}.md の CT_UPSTREAM_UNAVAILABLE と "
                "breakdown.ct_response.upstream_down に理由が残っている",
            ],
        )

    # 設定は済んでいる。**次に効くのは分母である。**
    gap = _worst_entity_gap()
    if gap is not None:
        month, total, with_domains = gap
        missing = total - with_domains
        return NextAction(
            headline=f"{month} は {total} 社のうち {missing} 社が計測に現れていない",
            why=(
                f"起点になる公式サイトが取れず、{missing} 社は候補ドメインが"
                f"1件も無い。公開している採用率は残りの {with_domains} 社の話で、"
                "**「取れなかった」が数字から消えている**（原則5）"
            ),
            steps=[
                f"runs/{month}.md の ACCEPTANCE_MISSING_RATE と "
                "breakdown.wikidata_identity を見る",
                "filled が 0 なら法人番号の突合が効いていない",
                "still_missing が大きいなら Wikidata 側に公式サイトが無い。"
                "EDINET コード（P5090）での突合を足す余地がある",
                "BACKLOG.md の 1d（米国はティッカーで突合）も同じ形の話",
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
    #: gold はあるが観測が 0 件の月。**「回った」と「測れた」を分ける**（原則5）
    empty_months: list[str] = field(default_factory=list)

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
            "empty_months": list(self.empty_months),
            "runs": list(self.runs),
            "later": [i.to_dict() for i in self.later],
            "next_action": self.next_action.to_dict(),
        }


def diagnose() -> Report:
    pops = _population_readiness()
    gold = _gold_months()
    months = [m for m, n, _ in gold if n]
    empty = [(m, pop) for m, n, pop in gold if not n]
    later = _later_items()
    return Report(
        populations=pops,
        months=months,
        runs=_run_reports(),
        later=later,
        next_action=_next_action(pops, months, later, empty),
        empty_months=[m for m, _ in empty],
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
        lines.append(f"  観測できた月: {len(report.months)} か月（{span}）")
    else:
        lines.append("  観測できた月: まだ無い")
    if report.empty_months:
        # **「回った」を「測れた」と読ませない。** 実行は成功し gold も
        # 書かれているので、ここで言わないと気付けない
        lines.append(f"  回ったが観測 0 件の月: {', '.join(report.empty_months)}")
    lines.append("")

    lines.append("あとでよいもの（無くても計測は回る）")
    for item in report.later:
        mark = "○" if item.done else "・"
        lines.append(f"  {mark} {item.label}")
        lines.append(f"      {item.detail}")

    return "\n".join(lines)
