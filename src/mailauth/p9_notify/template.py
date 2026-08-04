"""通知文面の描画（DESIGN.md Sprint 9）。

文面の要件（DESIGN.md より）

  - **検証可能な正規ドメイン上の URL を必ず含める。** 日本市場では
    「不審メール扱いされるリスク」が第一の障壁である。差出人のドメインと
    本文中の URL のドメインが一致していなければ、それ自体が不審メールの
    特徴になる。よってここで機械的に検査する
  - **営業要素を一切排除する。** 広告宣伝性がなければ特定電子メール法の
    「特定電子メール」には当たらないと整理できる。逆に一言でも勧誘が
    混じれば整理が崩れる。禁止語を機械で弾く
  - 公開サイトと同じ語彙規約を守る。`p8_publish.vocabulary` をそのまま
    使う。**通知だけ表現を緩めるのは筋が通らない**

書く内容は観測できた事実だけである。推察（どのベンダを使っているか）は
通知に載せない。**当てた推定を本人に送りつける必要がない**し、外れていた
場合に文面全体の信用が落ちる。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..p8_publish import vocabulary

#: 営業要素と見なす語。**一語でも入れば文面を出さない。**
#: 「広告宣伝性がない」という法的整理は、文面が実際にそうであることに依存する
SALES_TERMS: dict[str, str] = {
    "ご提案": "通知は計測結果の共有に限る",
    "無料診断": "役務の申し出は広告宣伝性を生む",
    "お見積": "取引の申し出を含めない",
    "御見積": "取引の申し出を含めない",
    "商談": "通知は商行為の入口にしない",
    "キャンペーン": "販促の語を使わない",
    "特別価格": "取引条件に触れない",
    "セミナー": "集客の告知を含めない",
    "ホワイトペーパー": "資料請求への誘導を含めない",
    "弊社サービス": "自社の役務に言及しない",
    "当社サービス": "自社の役務に言及しない",
    "導入をご検討": "採用の働きかけを含めない",
    "お問い合わせフォーム": "訂正窓口以外の窓口へ誘導しない",
}

#: 訂正期間。DESIGN.md P8 は最低30日・推奨60日
CORRECTION_DAYS_MINIMUM = 30
CORRECTION_DAYS_RECOMMENDED = 60


class TemplateError(RuntimeError):
    """文面が要件を満たしていない。**送信可能な形にしない。**"""


@dataclass
class Finding:
    """観測できた事実1件。**推察は入れない。**"""

    code: str
    #: 事実の記述。断定的な評価語を使わない
    statement: str
    #: 根拠となる標準。読み手が自分で確かめられるようにする
    standard: str | None = None

    def line(self) -> str:
        return f"- {self.statement}" + (f"（{self.standard}）" if self.standard else "")


@dataclass
class RenderedMessage:
    domain: str
    subject: str
    body: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "subject": self.subject,
            "body": self.body,
            "findings": [
                {"code": f.code, "statement": f.statement, "standard": f.standard}
                for f in self.findings
            ],
            "notes": self.notes,
        }


def findings_from_fact(fact: dict) -> list[Finding]:
    """P5 の Fact 行から、通知に載せられる事実を取り出す。

    **観測できていない行からは何も作らない。** 「取れなかった」を
    「無かった」として通知するのは、相手にとって事実に反する指摘になる
    （原則5）。ここは実害が出る箇所なので空を返す。
    """
    if not fact.get("observed"):
        return []

    out: list[Finding] = []

    if fact.get("spf_present") is False:
        out.append(
            Finding(
                code="spf_absent",
                statement=(
                    "apex の TXT を取得しましたが、SPF レコード"
                    "（v=spf1 で始まる TXT）は含まれていませんでした"
                ),
                standard="RFC 7208",
            )
        )
    elif fact.get("spf_valid") is False:
        out.append(
            Finding(
                code="spf_invalid",
                statement=(
                    "SPF レコードを構文として解釈できませんでした: "
                    f"{fact.get('spf_error') or '理由不明'}"
                ),
                standard="RFC 7208 §4",
            )
        )
    if fact.get("spf_exceeds_limit"):
        out.append(
            Finding(
                code="spf_lookup_limit",
                statement=(
                    "SPF の評価に必要な DNS 参照回数が "
                    f"{fact.get('spf_lookup_count')} 回で、上限の 10 回を"
                    "超えています。上限超過時の結果は permerror と規定されています"
                ),
                standard="RFC 7208 §4.6.4",
            )
        )
    if fact.get("spf_all_qualifier") in ("+", "?"):
        out.append(
            Finding(
                code="spf_all_permissive",
                statement=(
                    f"SPF の all 機構の修飾子が `{fact.get('spf_all_qualifier')}all` "
                    "です。一覧外の送信元を否定しない指定になります"
                ),
                standard="RFC 7208 §5.1",
            )
        )

    if fact.get("dmarc_present") is False:
        out.append(
            Finding(
                code="dmarc_absent",
                statement=(
                    "_dmarc の TXT を取得しましたが、DMARC レコードは"
                    "含まれていませんでした"
                ),
                standard="RFC 7489",
            )
        )
    else:
        policy = fact.get("effective_7489")
        if policy == "none":
            out.append(
                Finding(
                    code="dmarc_none",
                    statement=vocabulary.describe_policy(
                        "none", has_rua=bool(fact.get("dmarc_rua"))
                    ),
                    standard="RFC 7489 §6.3",
                )
            )
        if fact.get("blind_enforcement"):
            out.append(
                Finding(
                    code="dmarc_no_rua",
                    statement=(
                        f"DMARC の p={policy} を指定していますが rua タグが無く、"
                        "認証に失敗した送信の集計レポートを受け取れない設定です"
                    ),
                    standard="RFC 7489 §7",
                )
            )
        if fact.get("dmarc_multiple_records"):
            out.append(
                Finding(
                    code="dmarc_multiple",
                    statement=(
                        "_dmarc に DMARC レコードが複数あります。"
                        "受信側はポリシーを適用しないと規定されています"
                    ),
                    standard="RFC 7489 §6.6.3",
                )
            )

    if fact.get("dkim_status") == "not_found":
        out.append(
            Finding(
                code="dkim_not_found",
                statement=(
                    "既知のセレクタ名では DKIM 公開鍵を見つけられませんでした。"
                    "**DKIM を設定していないことの確認ではありません。**"
                    "セレクタ名は任意であり、外部から網羅できません"
                ),
                standard="RFC 6376 §3.6.2",
            )
        )
    if fact.get("dkim_revoked"):
        out.append(
            Finding(
                code="dkim_revoked",
                statement=(
                    "DKIM の公開鍵レコードの p= が空です。"
                    "この鍵は失効を意味します（意図的な設定である場合があります）"
                ),
                standard="RFC 6376 §3.6.1",
            )
        )

    return out


def _check_url(url: str, *, sender_domain: str) -> str:
    """本文に載せる URL を検査する。

    **差出人ドメインの外にある URL は載せない。** 短縮 URL や外部の
    フォームを挟むと、それ自体が不審メールの特徴になる。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise TemplateError(f"本文の URL は https でなければならない: {url}")
    host = (parsed.hostname or "").lower()
    apex = sender_domain.lower().strip(".")
    if not host or not (host == apex or host.endswith("." + apex)):
        raise TemplateError(
            f"本文の URL のホスト {host or '(なし)'} が差出人ドメイン {apex} の"
            "外にある。受信側が真正性を確かめられない文面は送らない"
        )
    return url


def check_sales_terms(text: str) -> list[tuple[str, str]]:
    """営業要素を探す。見つかった (語, 理由) を返す。"""
    return [(term, why) for term, why in SALES_TERMS.items() if term in text]


def render(
    domain: str,
    findings: list[Finding],
    *,
    sender_domain: str,
    detail_url: str,
    method_url: str,
    correction_contact: str,
    measured_month: str,
    entity_name: str | None = None,
    optout_contact: str | None = None,
    notified_on: dt.date | None = None,
    correction_days: int = CORRECTION_DAYS_RECOMMENDED,
) -> RenderedMessage:
    """1件分の文面を作る。

    要件を満たさない文面は返さず `TemplateError` を投げる。**警告付きで
    返すと、忙しいときにそのまま送られる。**
    """
    if not findings:
        raise TemplateError(
            f"{domain} に載せる事実が無い。観測できていない、または指摘に"
            "当たる事実が無い状態で通知を作らない"
        )
    if correction_days < CORRECTION_DAYS_MINIMUM:
        raise TemplateError(
            f"訂正期間が {correction_days} 日では足りない"
            f"（最低 {CORRECTION_DAYS_MINIMUM} 日、推奨 "
            f"{CORRECTION_DAYS_RECOMMENDED} 日）"
        )

    detail = _check_url(detail_url, sender_domain=sender_domain)
    method = _check_url(method_url, sender_domain=sender_domain)
    optout = optout_contact or correction_contact

    start = notified_on or dt.date.today()
    deadline = start + dt.timedelta(days=correction_days)
    who = f"{entity_name}（{domain}）" if entity_name else domain

    subject = f"[事前通知] {domain} のメール認証設定の計測結果について"

    body = "\n".join(
        [
            f"{who}のご担当者様",
            "",
            "公開情報である DNS レコードのみを用いて、上場企業のメール認証",
            "（SPF / DKIM / DMARC）の設定状況を月次で計測しています。",
            f"{measured_month} の計測で、御社のドメインについて次の点を観測しました。",
            "公開に先立ってお知らせするものです。",
            "",
            "■ 観測した事実",
            *[f.line() for f in findings],
            "",
            "■ 計測の限界",
            f"公開サイトに常時掲示している位置づけのとおりです ──「{vocabulary.DISCLAIMER}」。",
            "外部から DNS を参照しただけの観測であり、実際の送信状況や",
            "受信側での扱いは分かりません。観測できなかった項目を",
            "「設定が無い」とは記載していません。",
            "",
            "■ 内容の確認",
            f"御社分の計測結果: {detail}",
            f"計測方法とコード: {method}",
            "上記の URL はいずれもこの差出人と同じドメイン上にあります。",
            "リンクを開かずに、ドメイン名をブラウザに直接入力して",
            "たどっていただいても同じ内容に到達します。",
            "",
            "■ 訂正の申し出",
            f"事実と異なる点がありましたら {correction_contact} までご連絡ください。",
            f"{deadline.isoformat()} まで（{correction_days}日間）を訂正期間とし、",
            "この期間の申し出は公開前に反映します。期間の経過後も随時受け付け、",
            "訂正の履歴は公開サイトに残します。",
            "",
            "■ 今後の連絡が不要な場合",
            f"{optout} まで「不要」とだけご返信ください。",
            "以後この計測に関する連絡は行いません。",
            "",
            "この通知は計測結果をお知らせするためのものです。",
            "商品・役務の広告または宣伝を目的とする内容は含みません。",
            "",
            f"-- \n{sender_domain}",
        ]
    )

    message = RenderedMessage(
        domain=domain, subject=subject, body=body, findings=list(findings)
    )

    violations = vocabulary.check(body) + vocabulary.check(subject)
    if violations:
        raise TemplateError(
            "文面が語彙規約に反している（公開サイトと同じ基準で検査している）:\n  "
            + "\n  ".join(f"「{v.term}」→ {v.advice}" for v in violations)
        )
    sales = check_sales_terms(body) + check_sales_terms(subject)
    if sales:
        raise TemplateError(
            "文面に営業要素がある。**広告宣伝性がないという整理が崩れる**:\n  "
            + "\n  ".join(f"「{term}」→ {why}" for term, why in sales)
        )
    if not vocabulary.has_disclaimer(body):
        raise TemplateError("限界の明示が本文に無い")

    message.notes.append(
        "語彙規約・営業要素・URL の真正性を機械検査で通した文面である。"
        "**文面が適切であることと、送ってよいことは別の判断である**"
    )
    return message
