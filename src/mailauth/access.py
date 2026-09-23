"""アクセス制御（Cloudflare Access ＋ Entra ID）の記述と**検査**。

第2層（個社名付き明細）は認証の内側にしか置けない。この条件は
`configs/publish.yaml` の `tier2.access_control_configured` という真偽値で
表現されていたが、**これは人が書き込む自己申告であって、実際に認証が
掛かっていることの証拠ではない。**

設定を書き換えただけ、ポリシーの対象パスを間違えた、Access アプリを
作る前に true にした ── どれも「設定済み」と読める状態になり、
その状態で個社明細が公開される。取り返しがつかない（キャッシュも
インデックスも残る）。

そこでこのモジュールは、**公開 URL に認証なしでアクセスして、
実際に弾かれることを確かめる。** 確かめられたときだけ第2層に進む。

## 三値で答える

原則5（「取れなかった」と「無かった」を区別する）をそのまま適用する。

  protected   認証に弾かれた（Cloudflare Access へ転送された / 401 / 403）
  open        認証なしで本文が返った ── **公開されている**
  unknown     ネットワークで確かめられなかった

**unknown を protected として扱わない。** 確かめられなかったことを
「守られている」と読むと、このモジュールを作った意味が消える。

## Entra ID 側で効かせるべき条件

Cloudflare Access のポリシーを「ログイン方法 = Entra」だけで組むと、
**その Entra テナントのゲストアカウント（外部ドメインのメール）も通る。**
所属を限定したいなら、メールドメインの条件を別に置く必要がある。
`allowed_email_domains` はその意図を設定として残すためにある
（値そのものを検査に使うことはできない ── 弾かれた側からは
誰が通れるのか分からないため）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import load_yaml

USER_AGENT = "mailauth-observatory/0.1 (+https://github.com/suminot/mailauth-observatory)"

#: 認証に弾かれたと判断する応答
PROTECTED_STATUSES = frozenset({401, 403})

#: Cloudflare Access のログインへ転送されるときの行き先に含まれる文字列
ACCESS_REDIRECT_MARKERS = (
    "cloudflareaccess.com",
    "/cdn-cgi/access/login",
)

#: Entra ID のログイン画面へ直接飛ばされる構成もある
IDP_REDIRECT_MARKERS = (
    "login.microsoftonline.com",
    "login.microsoft.com",
)

PROTECTED = "protected"
OPEN = "open"
UNKNOWN = "unknown"

#: 想定しているログイン方式。**検査の仕組みはどちらでも変わらない**
#: （どちらもログイン画面への転送を見ている）が、要求する条件が違う。
ONE_TIME_PIN = "one_time_pin"
ENTRA_ID = "entra_id"
KNOWN_IDPS = frozenset({ONE_TIME_PIN, ENTRA_ID})

#: ドメイン条件が無いと誰でも通ってしまう方式。
#: OTP は「そのアドレスに届くコードを入力できる」ことしか確かめないので、
#: **`allowed_email_domains` を空にすると認証の意味が無くなる。**
#: entra_id なら「ログイン方法 = Entra」で最低限テナント内には絞られる。
IDPS_REQUIRING_EMAIL_DOMAIN = frozenset({ONE_TIME_PIN})


@dataclass
class ProbeResult:
    """1つの URL を認証なしで叩いた結果。"""

    url: str
    state: str
    status_code: int | None = None
    location: str | None = None
    detail: str = ""

    @property
    def protected(self) -> bool:
        return self.state == PROTECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state,
            "status_code": self.status_code,
            "location": self.location,
            "detail": self.detail,
        }


@dataclass
class AccessConfig:
    """`configs/publish.yaml` の `access` ブロック。"""

    provider: str = "cloudflare_access"
    idp: str | None = None
    allowed_email_domains: list[str] = field(default_factory=list)
    #: 認証の内側にあるべきパス。第2層のパスは必ず含める
    protected_paths: list[str] = field(default_factory=list)
    #: 公開サイトの基点 URL。**未設定なら検査できない**
    verify_base_url: str | None = None

    @classmethod
    def from_publish_config(cls, cfg: dict[str, Any]) -> AccessConfig:
        raw = cfg.get("access") or {}
        paths = list(raw.get("protected_paths") or [])
        # 第2層のパスは設定漏れがあっても必ず検査する。**ここを落とすと、
        # 守るべき唯一の場所を検査しないまま「検査した」と言うことになる**
        tier2_path = ((cfg.get("deploy") or {}).get("tier2_path")) or None
        if tier2_path and tier2_path not in paths:
            paths.append(tier2_path)
        return cls(
            provider=raw.get("provider") or "cloudflare_access",
            idp=raw.get("idp"),
            allowed_email_domains=list(raw.get("allowed_email_domains") or []),
            protected_paths=paths,
            verify_base_url=raw.get("verify_base_url"),
        )


def probe(url: str, *, client: httpx.Client | None = None, timeout: float = 15.0) -> ProbeResult:
    """認証なしで1回だけ叩く。**リダイレクトは追わない。**

    追ってしまうと、ログイン画面が 200 で返ってきたものを「公開されている」
    と読み違える。転送そのものが認証の証拠なので、転送先を見て判断する。
    """
    own = client is None
    c = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        resp = c.get(url, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        return ProbeResult(
            url=url,
            state=UNKNOWN,
            detail=f"到達できなかった: {exc}。**守られているとは言えない**",
        )
    finally:
        if own:
            c.close()

    location = resp.headers.get("location")
    if resp.status_code in PROTECTED_STATUSES:
        return ProbeResult(
            url=url,
            state=PROTECTED,
            status_code=resp.status_code,
            detail=f"{resp.status_code} で拒否された",
        )

    if 300 <= resp.status_code < 400 and location:
        target = location.lower()
        for marker in ACCESS_REDIRECT_MARKERS:
            if marker in target:
                return ProbeResult(
                    url=url,
                    state=PROTECTED,
                    status_code=resp.status_code,
                    location=location,
                    detail="Cloudflare Access のログインへ転送された",
                )
        for marker in IDP_REDIRECT_MARKERS:
            if marker in target:
                return ProbeResult(
                    url=url,
                    state=PROTECTED,
                    status_code=resp.status_code,
                    location=location,
                    detail="ID プロバイダのログインへ転送された",
                )
        # 認証と無関係な転送（末尾スラッシュの正規化など）は判断できない
        return ProbeResult(
            url=url,
            state=UNKNOWN,
            status_code=resp.status_code,
            location=location,
            detail="転送されたが行き先が認証のものか判断できない",
        )

    if resp.status_code == 200:
        return ProbeResult(
            url=url,
            state=OPEN,
            status_code=200,
            detail="認証なしで本文が返った。**公開されている**",
        )

    # 404 などはここに来る。無いものは守られているとは言えない
    return ProbeResult(
        url=url,
        state=UNKNOWN,
        status_code=resp.status_code,
        detail=f"{resp.status_code}。認証の有無を判断できない",
    )


@dataclass
class AccessReport:
    config: AccessConfig
    probes: list[ProbeResult] = field(default_factory=list)
    #: 検査そのものができなかった理由
    unavailable_reason: str | None = None
    #: 設定だけで分かる不備。**外から叩いて弾かれても、これがあれば通さない**
    config_problems: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """**すべての対象パスが弾かれたときだけ True。**"""
        if self.unavailable_reason or self.config_problems or not self.probes:
            return False
        return all(p.protected for p in self.probes)

    @property
    def open_paths(self) -> list[ProbeResult]:
        return [p for p in self.probes if p.state == OPEN]

    @property
    def unknown_paths(self) -> list[ProbeResult]:
        return [p for p in self.probes if p.state == UNKNOWN]

    def reasons(self) -> list[str]:
        """第2層を止める理由。verified なら空。"""
        if self.unavailable_reason:
            return [self.unavailable_reason]
        out: list[str] = list(self.config_problems)
        for p in self.open_paths:
            out.append(f"{p.url} が認証なしで開いている。第2層は認証の内側にしか置けない")
        for p in self.unknown_paths:
            out.append(f"{p.url} の認証状態を確かめられなかった（{p.detail}）")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "idp": self.config.idp,
            "allowed_email_domains": list(self.config.allowed_email_domains),
            "verified": self.verified,
            "unavailable_reason": self.unavailable_reason,
            "probes": [p.to_dict() for p in self.probes],
        }


def verify(
    config: AccessConfig,
    *,
    client: httpx.Client | None = None,
) -> AccessReport:
    """対象パスを認証なしで叩いて、実際に弾かれることを確かめる。"""
    report = AccessReport(config=config)

    if not config.verify_base_url:
        report.unavailable_reason = (
            "access.verify_base_url が未設定のため、認証が実際に掛かっているかを"
            "確かめられない。**設定ファイルの申告だけで第2層を公開しない**"
        )
        return report

    if not config.protected_paths:
        report.unavailable_reason = (
            "認証の内側にあるべきパスが1つも設定されていない（access.protected_paths）"
        )
        return report

    # 設定だけで分かる不備。**外から叩いて弾かれても、これがあれば通さない。**
    # OTP は「そのアドレスに届くコードを入力できる」ことしか確かめないので、
    # ドメインの条件が無ければ誰でも通る ── 弾かれたことは確かめられても、
    # 誰が通れるのかは外からは分からない
    if config.idp in IDPS_REQUIRING_EMAIL_DOMAIN and not config.allowed_email_domains:
        report.config_problems.append(
            f"idp が {config.idp} なのに allowed_email_domains が空。"
            "ワンタイム PIN はメールドメインの条件が無いと誰でも通る"
        )

    base = config.verify_base_url.rstrip("/")
    own = client is None
    c = client or httpx.Client(timeout=15.0, follow_redirects=False)
    try:
        for path in config.protected_paths:
            url = base + "/" + path.lstrip("/")
            report.probes.append(probe(url, client=c))
    finally:
        if own:
            c.close()
    return report


# --------------------------------------------------------------------------
# 検索エンジンに載せない
# --------------------------------------------------------------------------
#
# アクセス制御（誰が開けるか）とは別の話だが、**確かめ方が同じ**なので
# ここに置く。設定ファイルに書いたことと、配信されているものは別である。
#
# 検索避けは3層ある。
#
#   1. `site/static/_headers` の `X-Robots-Tag`  ── **実体はこれ**
#   2. 各ページの `<meta name="robots">`         ── HTML にしか効かない
#   3. `robots.txt`                              ── **巡回を止めるだけ**
#
# 3 を `Disallow: /` にして 1・2 と併用してはいけない。取りに来られなければ
# noindex を読めないので、他サイトからリンクされていれば URL だけが
# 検索結果に残りうる。**索引から外したいなら、巡回させて noindex を読ませる。**

ROBOTS_HEADER = "x-robots-tag"


@dataclass
class NoindexResult:
    url: str
    state: str
    header: str | None = None
    meta: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state,
            "header": self.header,
            "meta": self.meta,
            "detail": self.detail,
        }


def probe_noindex(url: str, *, client: httpx.Client | None = None) -> NoindexResult:
    """配信されている応答に noindex が付いているかを確かめる。

    **ヘッダを優先して見る。** meta robots は HTML にしか書けないので、
    公開データ（JSON / CSV / Parquet）を直接リンクされた場合に届かない。
    """
    own = client is None
    c = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        resp = c.get(url, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        return NoindexResult(
            url=url,
            state=UNKNOWN,
            detail=f"到達できなかった: {exc}。**載らないとは言えない**",
        )
    finally:
        if own:
            c.close()

    # **無いものは「載らない」とも言えない。** デプロイ前の Pages は 404 を
    # 返すので、ここを OPEN にすると「検索結果に載りうる」と嘘の警告が出る。
    # 逆に PROTECTED にすると、中身が入った瞬間に無防備になっていても
    # 気付けない。判断しないのが正しい（原則5）。
    if resp.status_code >= 400:
        return NoindexResult(
            url=url,
            state=UNKNOWN,
            detail=f"HTTP {resp.status_code}。まだ中身が無いか、届いていない",
        )

    header = resp.headers.get(ROBOTS_HEADER)
    has_header = bool(header and "noindex" in header.lower())
    body = resp.text if resp.headers.get("content-type", "").startswith("text/html") else ""
    has_meta = 'name="robots"' in body and "noindex" in body.lower()

    if has_header:
        return NoindexResult(
            url=url,
            state=PROTECTED,
            header=header,
            meta=has_meta,
            detail="X-Robots-Tag が付いている",
        )
    if has_meta:
        return NoindexResult(
            url=url,
            state=UNKNOWN,
            meta=True,
            detail=(
                "meta robots はあるが X-Robots-Tag が無い。"
                "**HTML 以外（JSON / CSV）は索引され得る**"
            ),
        )
    return NoindexResult(url=url, state=OPEN, detail="noindex の指定が無い。**検索結果に載りうる**")


def tier1_is_gated(report: AccessReport, tier2_path: str | None) -> bool:
    """第1層まで認証の内側に入っているか。

    DESIGN.md は第1層を無条件公開と定めている。試験中にサイト全体へ
    Access を掛けるのは妥当だが、**掛けたまま忘れると「公開している」
    という前提が静かに崩れる。** 外し忘れに気付けるようにする。
    """
    for p in report.config.protected_paths:
        normalized = "/" + p.strip("/")
        if normalized == "/":
            return True
        if tier2_path and normalized != "/" + tier2_path.strip("/"):
            return True
    return False


def load(config_path: str = "configs/publish.yaml") -> AccessConfig:
    return AccessConfig.from_publish_config(load_yaml(config_path))
