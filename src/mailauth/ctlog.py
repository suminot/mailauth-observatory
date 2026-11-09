"""Certificate Transparency ログからのドメイン発見（crt.sh）。

crt.sh は重い。レート制限とリトライを必ず入れ、結果は run 単位で
キャッシュする（DESIGN.md P2 実装メモ）。

CT ログは1ドメインあたり数百〜数千の証明書を返すことがある。
eTLD+1 に正規化してから重複排除しないと候補が爆発する。

## キャッシュは月で区切る

**CT ログは追記されていく。** 先月の応答を今月の観測として使うと、
その間に発行された証明書が永久に見えない。新しく作られたドメインは
CT 経由でしか見つからないことがあるので、**候補生成が初月の状態で
凍結する。** しかも数字は動かないだけなので気付けない。

そこでキャッシュを `month=YYYY-MM/` で区切る。

  - 同じ月の再実行はキャッシュを使う（原則6 冪等、crt.sh に再負荷をかけない）
  - 月が変われば取り直す（新しい証明書が入る）

`month` を渡さない場合は月で区切らない。テストと単発の調査用で、
**月次計測の経路では必ず渡す。**

## qps は「投げる間隔」であって「同時本数」ではない

2026-09 の国内計測で P2 が5時間17分走っても終わらず、ジョブの上限で
打ち切られた。`qps: 0.5` にしているので2秒に1本しか投げない計算だが、
**実測の1件あたりは9.5秒以上あった。** 直列に呼んでいるため、応答を
待っている7秒以上のあいだ何もしていない。相手に遠慮しているのではなく、
ただ待っているだけの時間である。

そこで `prefetch()` を置いた。**投げる間隔は 0.5 qps のまま全スレッドで
共有し、応答待ちだけを重ねる。** crt.sh から見た単位時間あたりの本数は
変わらない ── 礼儀として絞っている qps を緩めたわけではない。
変わるのは、こちらが待っている時間だけである。

  直列  ── 投げる 待つ9.5秒 投げる 待つ9.5秒 …
  並行  ── 投げる 投げる 投げる 投げる（2秒間隔）… 応答は重なって返る

`concurrency` 本を超えては走らせない。多くすれば無条件に速くなるもの
ではなく、**qps が下限**になる。

**測る場所で答えが変わる。** 下の2つは条件が違うので、片方だけ読むと
誤る。

  開発機から（2026-09-24、8〜12ドメイン）
    直列     1件 9.1秒
    同時4本  1件 2.4秒
    同時8本  1件 2.3秒   ← ここでは4本で下限（1件2.0秒）に届いている

  GitHub Actions から（2026-09、run 35963788831、新規1,005件）
    同時4本  1件 16.3秒  ← 下限の8倍。**届いていない**

crt.sh は Actions の IP 帯に対して遅い。同時4本で1件16.3秒ということは、
応答1本あたり約65秒かかっている。下限に届かせるには同時30本前後が要る
計算になるが、**上げる判断はまだしない** ── 同じ IP から数時間続けて叩く
ほど遅くなっている可能性があり（失敗率が序盤4%→終盤25%）、その場合は
本数を増やすのは逆効果である。P2 の manifest（`breakdown.ct_response`）に
応答時間の分布を残すようにしたので、次の実行の数字で判断する。
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from .normalize import etld_plus_one

CRTSH_ENDPOINT = "https://crt.sh/"
USER_AGENT = "mailauth-observatory/0.1 (research; contact via github.com/suminot)"


#: 証明書に載ったサブドメインのうち、**メールのドメインに使われがちな名前**（左端の
#: ラベル）。受信用のホスト名（imap・owa など）は入れない ── ホストであって、
#: アドレスの右側に来る名前ではない。数字の付いたもの（mail2）も採る
MAIL_NAME_LABELS = (
    "mail", "mails", "email", "e-mail", "mx", "smtp", "post", "news", "newsletter",
    "mag", "magazine", "info", "mm", "mkt", "marketing", "crm", "em", "e", "m",
    "send", "notice", "notify", "bounce", "return", "reply", "noreply", "no-reply", "ml",
)
_MAIL_NAME_RE = re.compile(
    r"^(?:" + "|".join(re.escape(x) for x in MAIL_NAME_LABELS) + r")\d{0,2}$"
)
#: 1回の検索で残す数の上限
MAX_MAIL_NAMES = 30


def mail_name(candidate: str, apex: str | None) -> str | None:
    """`candidate` が `apex` の配下（2段まで）で、左端がメール用らしい名前なら返す。"""
    if not apex or not candidate.endswith("." + apex):
        return None
    labels = candidate[: -len(apex) - 1].split(".")
    if len(labels) > 2 or not _MAIL_NAME_RE.match(labels[0]):
        return None
    return candidate


def _collect_mail_names(names, apex: str | None) -> list[str]:
    out: dict[str, None] = {}
    for n in names:
        hit = mail_name(n, apex)
        if hit:
            out.setdefault(hit, None)
    return sorted(out)[:MAX_MAIL_NAMES]


@dataclass
class CtResult:
    domain: str
    #: 見つかった eTLD+1 の集合
    found: list[str] = field(default_factory=list)
    #: 引いたドメインの配下で、**メール用らしい名前**の FQDN（`mail.`・`news.` など、
    #: `MAIL_NAME_LABELS`）。サブドメインの候補にする（BACKLOG 52）。P3 が自分の MX か
    #: `_dmarc` を持つものだけ測る
    mail_names: list[str] = field(default_factory=list)
    #: 正規化前に見た FQDN の件数。爆発の度合いを記録する
    raw_names: int = 0
    from_cache: bool = False
    #: キャッシュを使った場合、それがどの月のものか。
    #: **当月以外なら「今月の観測」ではない**（原則5）
    cache_month: str | None = None
    error: str | None = None
    #: 失敗の種類。**「失敗」の一語にまとめない。**
    #: timeout / http / transport / decode で手当てが違う
    #: （時間切れなら待ち方、HTTP なら頼み方を変えることになる）
    error_kind: str | None = None
    #: 実際に応答が返るまでの秒数。キャッシュに当たった場合は None
    duration_sec: float | None = None
    #: 投げ直した回数。0 なら一発で返っている
    attempts: int = 1
    #: どこから取ったか（crtsh / certspotter）。**取り方が違えば見え方も違う**
    #: ので、混ぜても数えられるようにしておく
    source: str = "crtsh"
    #: 共用の証明書とみなして捨てた行（証明書）の数（`tls_san.SHARED_CERT_LIMIT`）
    shared_skipped: int = 0


#: 同時に応答を待つ本数の既定値。**投げる間隔（qps）とは別のつまみ。**
#: 増やしても qps より速くはならない
DEFAULT_CONCURRENCY = 4

#: 相手が落ちていると判断するまでに見る本数。
#:
#: 2026-09-24、crt.sh は**トップページごと 502 Bad Gateway** を返していた
#: （nginx は生きていて、後ろが落ちている）。この状態で3,191ドメインを
#: 順に投げても1件も取れない。**失敗の投げ直しに枠を使い切るだけ**で、
#: しかも落ちている相手を叩き続けることになる。
#:
#: これだけ連続して1本も成功しないなら、こちらの頼み方の問題ではない。
#: **止めて、後で出直す。**
DEFAULT_DOWN_AFTER = 20


class CtSource(Protocol):
    def search(self, domain: str) -> CtResult: ...

    def search_org(self, name: str) -> CtResult:
        """組織名で引く。**持たない実装があるので P2 は有無を見る。**"""
        ...

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        """まとめて取る。`search()` と同じ結果を domain 別に返す。"""
        ...


class CrtShClient:
    """crt.sh の JSON 出力を使う。

    identity 検索（`?q=%.<domain>&output=json`）で SAN を含む証明書を引き、
    name_value（改行区切りの FQDN 群）から eTLD+1 を取り出す。
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        month: str | None = None,
        qps: float = 0.5,
        timeout: float = 60.0,
        retries: int = 2,
        concurrency: int = DEFAULT_CONCURRENCY,
        down_after: int = DEFAULT_DOWN_AFTER,
        fetch_budget_sec: float = 0.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        #: キャッシュを区切る月（`YYYY-MM`）。**月次計測では必ず渡す。**
        #: 渡さないと先月の応答を今月の観測として使ってしまう
        self.month = month
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.timeout = timeout
        self.retries = retries
        #: 同時に応答を待つ本数。**qps とは別物**（モジュール冒頭参照）
        self.concurrency = max(int(concurrency), 1)
        self._client = client
        self._last_call = 0.0
        # **投げる間隔は全スレッドで共有する。** スレッドごとに持つと、
        # 本数ぶんだけ crt.sh への実効 qps が上がってしまう
        self._throttle_lock = threading.Lock()
        # 応答時間と失敗の内訳。**prefetch は複数スレッドから書く**ので
        # 間隔の錠とは別の錠で守る（同じ錠を使うと、記録のたびに投げる
        # 権利を奪い合うことになる）
        self._stats_lock = threading.Lock()
        self._timings: list[float] = []
        self._error_kinds: dict[str, int] = {}
        self._retried = 0
        #: 実際に取りにいって成功した本数。キャッシュ命中は数えない
        self._succeeded = 0
        #: 相手が落ちていると判断するまでに見る本数。**少なすぎると、
        #: たまたま重い数件で止まる。** 20本すべてが失敗する確率は、
        #: 通常の失敗率が5割でも 1/100万 に満たない
        #: **0 以下なら止めない**（設定で無効にできる）
        self.down_after = int(down_after) if int(down_after) > 0 else 0
        #: 落ちていると判断したあと、ここに理由を入れる
        self._down_reason: str | None = None
        #: **取りにいくのは、最初に取りにいってからこの秒数まで。** 0 なら
        #: 切らない。過ぎたらキャッシュだけを返す（下の `_budget_check`）
        self.fetch_budget_sec = max(float(fetch_budget_sec or 0), 0.0)
        self._fetch_started: float | None = None
        #: 持ち時間を使い切ったために取りにいかなかった本数
        self._budget_skipped = 0

    def _throttle(self) -> None:
        """次の1本を投げてよい時刻まで待つ。**全スレッドで1つの間隔。**

        ロックを保ったまま眠る。こうすると「投げる瞬間」だけが直列になり、
        応答を待つ時間は重なる。ロックの外で眠ると全スレッドが同じ時刻に
        目を覚まして一斉に投げるので、絞っている意味が無くなる。
        """
        if self.min_interval <= 0:
            return
        with self._throttle_lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _record(self, elapsed: float, *, kind: str | None, attempts: int) -> None:
        """1本ぶんの応答時間と結果を控える。**worker スレッドから呼ばれる。**"""
        with self._stats_lock:
            self._timings.append(elapsed)
            if kind:
                self._error_kinds[kind] = self._error_kinds.get(kind, 0) + 1
            else:
                self._succeeded += 1
            if attempts > 1:
                self._retried += 1
            # **1本も成功しないまま既定の本数に達したら、相手が落ちている。**
            # 一度立てたら降ろさない（降ろすと、たまたま1件通るたびに
            # 叩き直しが始まる）
            if (
                self.down_after > 0
                and self._down_reason is None
                and self._succeeded == 0
                and len(self._timings) >= self.down_after
            ):
                kinds = ", ".join(f"{k}={v}" for k, v in sorted(self._error_kinds.items()))
                self._down_reason = (
                    f"crt.sh に {len(self._timings)} 本投げて1本も成功していない（{kinds}）"
                )
            # **断られた回数が、答えてもらえた回数を上回った。**
            #
            # 上の判定は「丸ごと落ちている」しか捕まえない。2026-09-25 の
            # crt.sh は 30% → 17% → 10% と**まだらに落ちて**いて、
            # 投げ直し2回のおかげで連続失敗が20本に届かない ── つまり
            # 上の判定は作動しないまま、3分の1しか見えていない月ができる。
            #
            # `http`（502 / 404 / 429 など）は**相手が断った**ということで、
            # `timeout` と違ってドメインの重さでは起きない。**断りが答えを
            # 上回っているなら、こちらの頼み方の問題ではない。**
            #
            # 割合の閾値は置いていない（置くと「いくつが正常か」を
            # 測らずに決めることになる）。「半分を超えた」だけで判定する。
            http_errors = self._error_kinds.get("http", 0)
            if (
                self.down_after > 0
                and self._down_reason is None
                and http_errors >= self.down_after
                and http_errors > self._succeeded
            ):
                self._down_reason = (
                    f"crt.sh に断られた回数（{http_errors}）が、"
                    f"答えが返った回数（{self._succeeded}）を上回っている"
                )

    def _budget_spent(self) -> bool:
        """取りにいく持ち時間を使い切ったか。**最初の1本で時計を始める。**

        crt.sh の速さの上限は相手側にあり、同時本数を上げても件数は増えない
        （BACKLOG 12）。P2 が応答の続く限り待つと、工程全体の持ち時間
        （`limits.max_phase_minutes`）を超えて**企業ごと打ち切られる**。
        CT だけを先に切り、残りは積み残しとして月のあいだの補充に回す。
        """
        if self.fetch_budget_sec <= 0:
            return False
        now = time.monotonic()
        with self._stats_lock:
            if self._fetch_started is None:
                self._fetch_started = now
                return False
            if now - self._fetch_started <= self.fetch_budget_sec:
                return False
            self._budget_skipped += 1
            return True

    @property
    def fetch_budget_skipped(self) -> int:
        """持ち時間を使い切ったために取りにいかなかった本数。"""
        with self._stats_lock:
            return self._budget_skipped

    @property
    def upstream_down(self) -> str | None:
        """相手が落ちていると判断した理由。判断していなければ None。

        **「取れなかった」と「相手が落ちていた」は別である**（原則5）。
        前者は候補が少ない月として扱えるが、後者は計測そのものが成立して
        いない。呼び出し側がこれを読んで、そう記録する。
        """
        with self._stats_lock:
            return self._down_reason

    def response_stats(self) -> dict[str, object]:
        """応答時間の分布と失敗の内訳。**manifest に載せて次の判断に使う。**

        2026-09 の実行では、P2 の実時間の大半が crt.sh の待ちだった可能性が
        あるのに、残っていたのは `errors` の件数だけだった。**時間切れと
        HTTP エラーの区別も、どれだけ待たされたかも無い。** 判断材料が
        無いまま調整すると、次の実行も5時間かけて同じことを学ぶ。

        取れた本数が0なら、0 で埋めずに**空を返す**（原則5：測っていない
        ことと 0 秒だったことは別）。
        """
        with self._stats_lock:
            timings = sorted(self._timings)
            kinds = dict(sorted(self._error_kinds.items()))
            retried = self._retried
            down_reason = self._down_reason
        if not timings:
            return {"requests": 0, "note": "crt.sh に1本も投げていない"}

        def pct(q: float) -> float:
            idx = min(int(q * len(timings)), len(timings) - 1)
            return round(timings[idx], 3)

        return {
            "requests": len(timings),
            # 中央値は statistics.median に合わせる（P2 の他の分布と同じ流儀）
            "p50_sec": round(statistics.median(timings), 3),
            "p90_sec": pct(0.90),
            "p99_sec": pct(0.99),
            "max_sec": round(timings[-1], 3),
            "mean_sec": round(sum(timings) / len(timings), 3),
            # **時間切れは待ち方、HTTP は頼み方。** 一語にまとめない
            "errors_by_kind": kinds,
            "retried": retried,
            "timeout_sec": self.timeout,
            # **「取れなかった」のか「相手が落ちていた」のかを残す**（原則5）
            "upstream_down": down_reason,
            "fetch_budget_sec": self.fetch_budget_sec or None,
            "fetch_budget_skipped": self.fetch_budget_skipped,
        }

    def _cache_path(self, domain: str) -> Path | None:
        """キャッシュの置き場所。**月で区切る。**

        月を跨いだ再利用は「先月の観測を今月として出す」ことになるので、
        パスの階層で分けて物理的に起こらないようにする。
        """
        if self.cache_dir is None:
            return None
        safe = domain.replace("/", "_")
        base = self.cache_dir / f"month={self.month}" if self.month else self.cache_dir
        return base / f"{safe}.json"

    def search(self, domain: str, *, client: httpx.Client | None = None) -> CtResult:
        """1ドメイン分を取る。

        `client` を渡すと接続を使い回す。`prefetch()` が本数分の接続を
        張り直さないために使う（`httpx.Client` はスレッド安全）。
        """
        return self._query(
            domain,
            {"q": f"%.{domain}", "output": "json"},
            _extract,
            client=client,
        )

    def search_org(self, name: str, *, client: httpx.Client | None = None) -> CtResult:
        """**組織名で引く。** 起点ドメインが無い企業のための経路。

        DESIGN.md P2 は「crt.sh で組織名を検索」と書いていたが、実装は
        ドメイン検索だけだった。起点が取れない企業は候補ゼロになり、
        **そのまま分母から消えていた**（2026-09 の国内計測で 628 社）。

        `?O=<名前>` は**あいまい一致で他社を返す。** 実測（2026-09-29、
        `O=Toyota Motor Corporation`）では1万件中 6,721 件が
        `Toyota Motor Credit Corporation`（別会社）、385 件が
        `Toyota Motor Corporation Australia Limited` だった。
        **そのまま候補にすると他社のドメインをその企業のものとして測る。**

        なので `name_value`（当たった組織名）が**完全に一致する行だけ**を
        採る。同じ実測で完全一致は 2,684 件あり、取れたのは
        `toyota.co.jp` / `toyotatimes.jp` / `nettam.jp` などだった。
        """
        key = _org_cache_key(name)
        return self._query(
            key,
            {"O": name, "output": "json"},
            lambda _key, rows: _extract_org(key, name, rows),
            client=client,
        )

    def _query(
        self,
        cache_key: str,
        params: dict[str, str],
        extract: Callable[[str, list[dict]], CtResult],
        *,
        client: httpx.Client | None = None,
    ) -> CtResult:
        """crt.sh を1回引く。**キャッシュ・間隔・停止判定はここに集約する。**

        ドメイン検索と組織名検索で、相手への当たり方を変えない。
        """
        domain = cache_key
        cached = self._cache_path(domain)
        if cached and cached.is_file():
            try:
                payload = json.loads(cached.read_text(encoding="utf-8"))
                return CtResult(
                    domain=domain,
                    found=payload.get("found", []),
                    raw_names=payload.get("raw_names", 0),
                    mail_names=payload.get("mail_names", []),
                    from_cache=True,
                    cache_month=payload.get("month"),
                    source=payload.get("source", "crtsh"),
                )
            except (json.JSONDecodeError, OSError):
                pass

        # **落ちている相手を叩き続けない。** キャッシュは上で先に見るので、
        # 持っているものは引き続き返る。ここから先は取りにいかない
        down = self.upstream_down
        if down is not None:
            return CtResult(
                domain=domain,
                error=f"crt.sh が応答していないため取りにいっていない（{down}）",
                error_kind="upstream_down",
                attempts=0,
            )
        # **持ち時間を過ぎたら取りにいかない。** 相手が落ちているのではない
        # ので `upstream_down` とは分ける。積み残しには入る
        if self._budget_spent():
            return CtResult(
                domain=domain,
                error=(
                    f"CT を取りにいく持ち時間（{self.fetch_budget_sec / 60:.0f} 分）を"
                    "使い切ったため取りにいっていない"
                ),
                error_kind="fetch_budget",
                attempts=0,
            )

        given = client or self._client
        client = given or httpx.Client(timeout=self.timeout, follow_redirects=True)
        close = given is None
        attempt = 0
        started = time.monotonic()

        def failed(kind: str, message: str) -> CtResult:
            """失敗も**どれだけ待たされたか**を残す。

            時間切れと HTTP エラーでは手当てが違う。件数だけ数えていると、
            「待ち方を変えるべきか」「頼み方を変えるべきか」が分からない。
            """
            elapsed = time.monotonic() - started
            self._record(elapsed, kind=kind, attempts=attempt + 1)
            return CtResult(
                domain=domain,
                error=message,
                error_kind=kind,
                duration_sec=round(elapsed, 3),
                attempts=attempt + 1,
            )

        try:
            while True:
                self._throttle()
                try:
                    resp = client.get(
                        CRTSH_ENDPOINT,
                        params=params,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    )
                except httpx.TimeoutException as exc:
                    # **時間切れは別に数える。** 相手が遅いだけなら、
                    # 待ち時間を延ばす方が投げ直すより成功率が上がる
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(2**attempt)
                        continue
                    return failed("timeout", f"timeout: {exc}"[:200])
                except httpx.HTTPError as exc:
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(2**attempt)
                        continue
                    return failed("transport", f"request failed: {exc}"[:200])

                if resp.status_code in (429, 502, 503, 504) and attempt < self.retries:
                    attempt += 1
                    time.sleep(2**attempt)
                    continue
                if resp.status_code != 200:
                    # **404 を「証明書が無い」と読まない。** 2026-09-25 に
                    # 実地で確かめた ── 0件のとき crt.sh は 200 と空配列
                    # `[]` を返す。停止中に出る 404 は HTML のエラーページで、
                    # あれを「空」と解釈すると**サーバが壊れているときに
                    # 「証明書が無い」と記録する**ことになる（原則5）
                    return failed("http", f"HTTP {resp.status_code}")
                try:
                    rows = resp.json()
                except ValueError:
                    return failed("decode", "JSON として解釈できない応答")
                break
        finally:
            if close:
                client.close()

        elapsed = time.monotonic() - started
        self._record(elapsed, kind=None, attempts=attempt + 1)

        result = extract(domain, rows)
        result.cache_month = self.month
        result.duration_sec = round(elapsed, 3)
        result.attempts = attempt + 1
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(
                    {
                        "found": result.found,
                        "raw_names": result.raw_names,
                        "mail_names": result.mail_names,
                        # **どの月の観測かを payload にも書く。** パスだけに
                        # 頼ると、ディレクトリを動かしたときに月が分からなくなる
                        "month": self.month,
                        "source": "crtsh",
                    }
                ),
                encoding="utf-8",
            )
        return result

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        """まとめて取る。**応答待ちだけを重ねる。**

        投げる間隔は `_throttle()` が全スレッドで共有しているので、crt.sh
        から見た単位時間あたりの本数は直列のときと変わらない。

        戻り値は入力順の dict。**完了順にしない。** 完了順は実行ごとに
        変わるので、そのまま集計に流すと同じ入力から違うバイト列が出て
        原則6（冪等）が壊れる。

        `on_result` は**呼び出し側のスレッドで**、入力順に呼ぶ。進捗表示は
        スレッド安全に作っていないので、worker から呼ばせない。
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for d in domains:
            # 同じ起点ドメインを持つ企業が複数あることがある。
            # **2度取らない**（相手への本数がそのぶん増える）
            if d and d not in seen:
                seen.add(d)
                ordered.append(d)
        if not ordered:
            return {}

        out: dict[str, CtResult] = {}
        own = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                # map は入力順に結果を返す。完了順ではない
                for domain, result in zip(
                    ordered,
                    pool.map(lambda d: self.search(d, client=client), ordered),
                    strict=True,
                ):
                    out[domain] = result
                    if on_result is not None:
                        on_result(domain, result)
        finally:
            if own:
                client.close()
        return out


    def cached_org(self, name: str) -> CtResult | None:
        """組織名検索の**キャッシュだけ**を見る。無ければ None（取りにいかない）。

        起点のある会社の組織名検索は、P2 では取りにいかず、月のあいだの補充
        （`backfill`）が埋めたキャッシュだけを使う。P2 の持ち時間を増やさない
        """
        path = self._cache_path(_org_cache_key(name))
        if not path or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return CtResult(
            domain=_org_cache_key(name),
            found=payload.get("found", []),
            raw_names=payload.get("raw_names", 0),
            from_cache=True,
            cache_month=payload.get("month"),
            source=payload.get("source", "crtsh"),
        )

    def prefetch_org(self, names: Iterable[str]) -> dict[str, CtResult]:
        """組織名をまとめて引く。`prefetch` と同じく**応答待ちだけを重ねる。**

        P2 は起点の無い企業の組織名検索を1社ずつ順に投げていた。crt.sh が
        応答している日は1本 40 秒前後かかり、国内の約 400 社で CT の持ち時間
        （120 分）を食い尽くしていた（2026-11-02）。投げる間隔は `_throttle()`
        が全スレッドで共有するので、crt.sh から見た本数の上限は変わらない。
        """
        ordered = list(dict.fromkeys(n for n in names if n))
        if not ordered:
            return {}
        own = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                results = pool.map(lambda n: self.search_org(n, client=client), ordered)
                return dict(zip(ordered, results, strict=True))
        finally:
            if own:
                client.close()


#: crt.sh が1回の応答で返す上限。これに達したら**取りこぼしている。**
CRTSH_ROW_LIMIT = 10000


def normalize_org(name: str | None) -> str:
    """組織名を突き合わせられる形に揃える。**丸めすぎない。**

    証明書の O は大文字だったり空白が重なっていたりする（実測で
    `TOYOTA MOTOR CORPORATION` と `Toyota Motor Corporation` の両方、
    `Toyota  Motor Corporation` もあった）。そこだけ吸収する。

    **法人格の語（Inc. / Ltd. / Corporation）は落とさない。**
    落とすと `Toyota Motor Corporation` と
    `Toyota Motor Corporation Australia Limited` の距離が縮む方向にしか
    働かない ── 別会社を同じ会社として扱うのが、この経路で一番まずい。
    """
    return " ".join((name or "").split()).casefold()


def _org_cache_key(name: str) -> str:
    """組織名のキャッシュ名。ドメインのキャッシュと衝突させない。"""
    safe = "".join(c if c.isalnum() or c in "-_. " else "_" for c in name).strip()
    return "org=" + (safe.replace(" ", "+") or "unknown")[:120]


def _extract_org(key: str, name: str, rows: list[dict]) -> CtResult:
    """組織名検索の応答から、**その会社の**ドメインだけを取り出す。

    `?O=` はあいまい一致なので、`name_value`（当たった組織名）が完全に
    一致する行だけを採る。ドメインは `common_name` に入る
    （`?q=` のときと違い、`name_value` は組織名である）。
    """
    want = normalize_org(name)
    seen: dict[str, None] = {}
    raw = 0
    matched = 0
    for row in rows or []:
        if normalize_org(row.get("name_value")) != want:
            continue
        matched += 1
        cn = str(row.get("common_name") or "").strip().lstrip("*.").rstrip(".").lower()
        if not cn or " " in cn:
            continue
        raw += 1
        apex = etld_plus_one(cn)
        if apex:
            seen.setdefault(apex, None)
    result = CtResult(domain=key, found=sorted(seen), raw_names=raw)
    if len(rows or []) >= CRTSH_ROW_LIMIT and matched == 0:
        # **上限に当たって、しかも1件も一致しなかった。**
        # 「その会社の証明書が無い」ではなく「あいまい一致に押し出された」
        # 可能性がある。0件として静かに通さない（原則5）
        result.error = (
            f"crt.sh が上限 {CRTSH_ROW_LIMIT} 件を返し、完全一致が0件だった。"
            "同名・類似名の他社に押し出されている可能性がある"
        )
        result.error_kind = "org_truncated"
    return result


def _extract(domain: str, rows: list[dict]) -> CtResult:
    """crt.sh の行から eTLD+1 を取り出して重複排除する。

    **共用の証明書の行は捨てる。** crt.sh の1行は証明書1枚で、CDN の共用 SAN
    証明書には無関係な会社のドメインが数十並ぶ。そのまま採ると、他社の
    ドメインをその企業の候補にする（P3 で MX と SPF があれば likely になり、
    その企業のドメインとして測られる）。1枚に `SHARED_CERT_LIMIT` を超える
    登録ドメインが載っていれば、その行からは何も拾わない（tls_san.py と同じ線）
    """
    from .tls_san import SHARED_CERT_LIMIT

    seen: dict[str, None] = {}
    raw = 0
    shared = 0
    own_apex = etld_plus_one(domain)
    names_seen: list[str] = []
    for row in rows or []:
        names = str(row.get("name_value") or "")
        apexes: dict[str, None] = {}
        for fqdn in names.splitlines():
            candidate = fqdn.strip().lstrip("*.").rstrip(".").lower()
            if not candidate or " " in candidate:
                continue
            raw += 1
            names_seen.append(candidate)
            apex = etld_plus_one(candidate)
            if apex:
                apexes.setdefault(apex, None)
        if len(apexes) > SHARED_CERT_LIMIT:
            shared += 1
            continue
        for apex in apexes:
            seen.setdefault(apex, None)
    return CtResult(domain=domain, found=sorted(seen), raw_names=raw, shared_skipped=shared,
                    mail_names=_collect_mail_names(names_seen, own_apex))


class CacheOnlyCt:
    """**月のキャッシュだけを読む** CT の取得口（取りにいかない）。

    グループ会社（BACKLOG 52）は起点が千を超え、crt.sh（0.5 qps・応答 1 本 1 分前後）
    で引くと P2 が終わらない。キャッシュは補充が CertSpotter の名前1つの検索（100 本/時
    の枠）で埋める。キャッシュに無い起点は error_kind="not_cached" で返し、P2 は
    積み残し（`via: certspotter`）として書く。
    """

    def __init__(self, cache_dir: Path, month: str) -> None:
        self.cache_dir = cache_dir
        self.month = month
        self.upstream_down = False

    def _path(self, domain: str) -> Path:
        return self.cache_dir / f"month={self.month}" / f"{domain.replace('/', '_')}.json"

    def search(self, domain: str) -> CtResult:
        path = self._path(domain)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return CtResult(domain=domain, error="月のキャッシュに無い", error_kind="not_cached")
        return CtResult(
            domain=domain,
            found=payload.get("found", []),
            raw_names=payload.get("raw_names", 0),
            mail_names=payload.get("mail_names", []),
            from_cache=True,
            cache_month=payload.get("month"),
            source=payload.get("source", "crtsh"),
        )

    def prefetch(self, domains, *, on_result=None) -> dict[str, CtResult]:
        out = {}
        for d in domains:
            out[d] = self.search(d)
            if on_result is not None:
                on_result(d, out[d])
        return out


class StaticCtSource:
    """テスト用。ネットワークに出ない。"""

    def __init__(
        self,
        mapping: dict[str, list[str]] | None = None,
        orgs: dict[str, list[str]] | None = None,
        mail_names: dict[str, list[str]] | None = None,
    ) -> None:
        self.mapping = mapping or {}
        self.mail_names = mail_names or {}
        #: 組織名 -> ドメイン。**空でも `search_org` は持つ。**
        #: 持たないと P2 が「この CT ソースは組織名検索を持たない」として
        #: 経路ごと落とし、検査が**通っているのに何も見ていない**状態になる
        self.orgs = orgs or {}
        self.calls: list[str] = []
        self.org_calls: list[str] = []

    def search(self, domain: str) -> CtResult:
        self.calls.append(domain)
        found = self.mapping.get(domain, [])
        return CtResult(domain=domain, found=list(found), raw_names=len(found),
                        mail_names=list(self.mail_names.get(domain, [])))

    def search_org(self, name: str) -> CtResult:
        self.org_calls.append(name)
        found = self.orgs.get(name, [])
        return CtResult(domain=f"org={name}", found=list(found), raw_names=len(found))

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        return _serial_prefetch(self.search, domains, on_result)


class DisabledCtSource:
    """CT 探索を無効にしたときの実装。呼ばれたら空を返す。"""

    def search(self, domain: str) -> CtResult:
        return CtResult(domain=domain, found=[], raw_names=0)

    def search_org(self, name: str) -> CtResult:
        return CtResult(domain=f"org={name}", found=[], raw_names=0)

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        return _serial_prefetch(self.search, domains, on_result)


def _serial_prefetch(
    search: Callable[[str], CtResult],
    domains: Iterable[str],
    on_result: Callable[[str, CtResult], None] | None,
) -> dict[str, CtResult]:
    """ネットワークに出ない実装用。**並行にする意味が無いので直列。**"""
    out: dict[str, CtResult] = {}
    for domain in domains:
        if not domain or domain in out:
            continue
        out[domain] = search(domain)
        if on_result is not None:
            on_result(domain, out[domain])
    return out


# ===========================================================================
# 積み残しと、月のあいだの補充（BACKLOG 12）
# ===========================================================================
#
# **CT の取得を「月初の1回」に賭けない。**
#
# crt.sh は 2026-09 から 2026-11 まで、月次計測のたびに落ちていた
# （CT_UPSTREAM_UNAVAILABLE）。落ち方はまだらで、一日のうちに戻る時間帯も
# ある。月初に一度だけ数時間叩いても、その時間帯に落ちていれば終わりである。
#
# そこで P2 が取れなかった起点ドメインを**積み残し**として書き、別の定期
# 実行（ct-backfill）が月のあいだ少しずつ取り直す。取れたものは同じ月の
# キャッシュに入るので、同じ run_id で流し直すとそのまま使われる。
#
# **相手への当たり方は変えない。** crt.sh は qps 0.5 のまま、CertSpotter は
# 無料枠の上限（1時間に10件）の内側で、どちらも同じ IP から投げる。
# 制限をすり抜けるために IP や経路を変えることはしない。

#: 積み残しを置く場所。月のキャッシュの中に置き、キャッシュと一緒に持ち越す
PENDING_DIRNAME = "_pending"


def pending_path(cache_dir: Path, month: str, population: str) -> Path:
    return cache_dir / f"month={month}" / PENDING_DIRNAME / f"{population}.json"


def read_pending(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_pending(
    cache_dir: Path,
    month: str,
    population: str,
    domains: Iterable[str],
    *,
    total: int,
    config: str | None = None,
    now: str | None = None,
    via: str | None = None,
) -> Path:
    """P2 が取れなかった起点ドメインを書く。**流し直しの回数は引き継ぐ。**

    `via="certspotter"` の積み残しは、補充が crt.sh を使わず CertSpotter の名前1つの
    検索だけで埋める（グループ会社）。
    """
    path = pending_path(cache_dir, month, population)
    before = read_pending(path)
    payload = {
        "month": month,
        "population": population,
        "config": config or before.get("config"),
        # P2 が取りにいった起点ドメインの数。**積み残しの割合の分母**
        "total": int(total),
        "pending": sorted({d for d in domains if d}),
        "updated_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # 補充が済んで流し直しを頼んだ回数。**頼み続けない**ための数
        "reruns_requested": int(before.get("reruns_requested", 0)),
    }
    if via:
        payload["via"] = via
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


#: 組織名検索の積み残し（起点のある会社の、まだ引いていない組織名）
ORG_PENDING_DIRNAME = "_org_pending"


def org_pending_path(cache_dir: Path, month: str, population: str) -> Path:
    return cache_dir / f"month={month}" / ORG_PENDING_DIRNAME / f"{population}.json"


def write_org_pending(cache_dir: Path, month: str, population: str, names: Iterable[str]) -> Path:
    """P2 がキャッシュに無かった組織名を書く。補充が余った時間で引く。"""
    path = org_pending_path(cache_dir, month, population)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"month": month, "population": population,
               # **並びを保つ。** 補充は前から引く（先に引きたい会社を P2 が前に置く）
               "pending": list(dict.fromkeys(n for n in names if n))}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


MAIL_QUEUE_DIRNAME = "_mail_queue"
#: サブドメインの名前を、1ドメインあたり何ページまで引くか（1ページ = 枠の1本）
SUB_MAX_PAGES = 5
#: 1ドメインで残すメール用らしい名前の上限（ページをまたいで）
SUB_MAX_NAMES = 50


def mail_queue_path(cache_dir: Path, month: str, population: str) -> Path:
    return cache_dir / f"month={month}" / MAIL_QUEUE_DIRNAME / f"{population}.json"


def write_mail_queue(cache_dir: Path, month: str, population: str, domains: Iterable[str]) -> Path:
    """CertSpotter でサブドメインの名前を取る順番（先に引きたい会社を前に）。"""
    path = mail_queue_path(cache_dir, month, population)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"month": month, "population": population,
               "domains": list(dict.fromkeys(d for d in domains if d))}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def read_mail_queue(cache_dir: Path, month: str) -> list[str]:
    base = cache_dir / f"month={month}" / MAIL_QUEUE_DIRNAME
    out: list[str] = []
    for p in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            out.extend(json.loads(p.read_text(encoding="utf-8")).get("domains", []))
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(out))


def subdomain_names_pass(
    certspotter: CertSpotterClient,
    cache_dir: Path,
    month: str,
    *,
    deadline: float | None = None,
) -> dict:
    """**CertSpotter のサブドメイン込みの検索で、メール用らしい名前を取る**（BACKLOG 52）。

    crt.sh のキャッシュには登録ドメインしか残っておらず（#247 まで）、名前を取り
    直すと crt.sh を同じ本数だけ叩くことになる。CertSpotter の Small プランは
    サブドメイン込みの検索が 1 時間 10 本なので、**1本 = 1ページ**ずつ、順番の
    一覧（P2 が日経225 の会社を前に置く）の前から進める。まだ1ページも引いて
    いないドメインを先に、続きのあるドメインはその後に。進み具合（`cs_sub_after`・
    `cs_sub_done`）は月の CT キャッシュに書くので、回をまたいで続きから引ける。

    **月のキャッシュにあるドメインだけ**を扱う（無いドメインに書くと、crt.sh で
    取ったことにしてしまう）。
    """
    from .tls_san import SHARED_CERT_LIMIT

    base = cache_dir / f"month={month}"
    queue = read_mail_queue(cache_dir, month)
    stats = {"queue": len(queue), "requests": 0, "pages": 0, "domains_started": 0,
             "domains_done": 0, "names_added": 0, "apexes_added": 0, "errors": 0}

    def _load(domain: str) -> tuple[Path, dict] | None:
        path = base / f"{domain.replace('/', '_')}.json"
        if not path.is_file():
            return None
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _todo() -> list[str]:
        fresh, cont = [], []
        for d in queue:
            got = _load(d)
            if got is None:
                continue
            payload = got[1]
            if payload.get("cs_sub_done"):
                continue
            # crt.sh（`%.ドメイン`）で取った月は、名前も一緒に残っている（#247 以降）
            if payload.get("source", "crtsh") == "crtsh" and "mail_names" in payload \
                    and "cs_sub_pages" not in payload:
                continue
            pages = int(payload.get("cs_sub_pages") or 0)
            if pages >= SUB_MAX_PAGES:
                continue
            (cont if payload.get("cs_sub_after") else fresh).append(d)
        return fresh + cont

    for domain in _todo():
        if deadline is not None and time.monotonic() > deadline:
            break
        if certspotter.stopped or certspotter.requests >= certspotter.max_requests:
            break
        path, payload = _load(domain) or (None, None)
        if path is None:
            continue
        after = payload.get("cs_sub_after")
        rows, err = certspotter.page(domain, after)
        stats["requests"] += 1
        if rows is None:
            if certspotter.stopped:
                break
            stats["errors"] += 1
            continue
        stats["pages"] += 1
        if not after:
            stats["domains_started"] += 1
        own = etld_plus_one(domain)
        names = list(payload.get("mail_names") or [])
        found = list(payload.get("found") or [])
        for row in rows:
            fqdns = [str(f).strip().lstrip("*.").rstrip(".").lower()
                     for f in (row.get("dns_names") or [])]
            fqdns = [f for f in fqdns if f and " " not in f]
            apexes = {a for a in (etld_plus_one(f) for f in fqdns) if a}
            if len(apexes) > SHARED_CERT_LIMIT:
                continue  # 共用の証明書（CDN）からは拾わない
            for a in sorted(apexes):
                if a not in found:
                    found.append(a)
                    stats["apexes_added"] += 1
            for n in _collect_mail_names(fqdns, own):
                if n not in names and len(names) < SUB_MAX_NAMES:
                    names.append(n)
                    stats["names_added"] += 1
        payload["mail_names"] = sorted(names)
        payload["found"] = sorted(found)
        payload["cs_sub_pages"] = int(payload.get("cs_sub_pages") or 0) + 1
        if len(rows) < CERTSPOTTER_PAGE_SIZE:
            payload["cs_sub_done"] = True
            payload.pop("cs_sub_after", None)
            stats["domains_done"] += 1
        else:
            payload["cs_sub_after"] = str(rows[-1].get("id"))
        path.write_text(json.dumps(payload), encoding="utf-8")
    stats["queue_left"] = len(_todo())
    stats["stopped"] = certspotter.stopped
    stats["ratelimit_limit"] = certspotter.limit_seen
    return stats


def list_pending(cache_dir: Path, month: str) -> list[Path]:
    base = cache_dir / f"month={month}" / PENDING_DIRNAME
    return sorted(base.glob("*.json")) if base.is_dir() else []


CERTSPOTTER_ENDPOINT = "https://api.certspotter.com/v1/issuances"

#: CertSpotter が1回の応答で返す件数。これより少なければ最後のページ。
#: `Link: rel="next"` は最後のページにも付くので、それでは判定できない
#: （2026-11-01 に実地で確かめた）
CERTSPOTTER_PAGE_SIZE = 100


class CertSpotterClient:
    """SSLMate CertSpotter の issuances API。**crt.sh が落ちているときの補充用。**

    証明書ごとの `dns_names` を返すので、crt.sh と同じく「別 apex への
    飛び移り」が取れる（BACKLOG 12）。違いは2つある。

      - **期限内の証明書だけを返す**（2026-11-01 の応答で、期限切れの
        ものは出てこなかった）。crt.sh は期限切れも返すので、こちらの方が
        少なく出る。**どちらから取ったかをキャッシュに書き、P2 で数える**
      - 鍵なしは**1時間に10件**（`x-ratelimit-limit: 10`）。ページ送りも
        1件と数える。`max_requests` で1回の実行の上限を決め、応答の
        `x-ratelimit-remaining` が 0 になったらその場でやめる

    鍵（`MAILAUTH_CERTSPOTTER_API_KEY`）があれば上限が上がる。無くても動く。
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None,
        month: str | None,
        api_key: str | None = None,
        max_requests: int = 9,
        max_pages: int = 5,
        min_interval: float = 2.0,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        include_subdomains: bool = True,
    ) -> None:
        self.cache_dir = cache_dir
        #: **枠が2つある**（Small プラン：名前1つの検索 100 本/時、サブドメイン込みの
        #: 検索 10 本/時。sslmate.com/pricing/ct_search_api、2026-11-08 に確認）。
        #: 起点ドメインの補充は名前1つの検索（`match_wildcards` 付き）で足りる ──
        #: 公式サイトのドメインそのものの証明書の SAN から別の登録ドメインを拾う
        self.include_subdomains = include_subdomains
        #: 応答の `x-ratelimit-limit`（プランの枠を確かめる。鍵は出さない）
        self.limit_seen: int | None = None
        self.month = month
        self.api_key = api_key
        self.max_requests = max(int(max_requests), 0)
        self.max_pages = max(int(max_pages), 1)
        self.min_interval = min_interval
        self.timeout = timeout
        self._client = client
        self.requests = 0
        self._last_call = 0.0
        #: 相手に止められた理由。立ったらこの実行ではもう投げない
        self.stopped: str | None = None

    def _cache_path(self, domain: str) -> Path | None:
        if self.cache_dir is None:
            return None
        base = self.cache_dir / f"month={self.month}" if self.month else self.cache_dir
        return base / f"{domain.replace('/', '_')}.json"

    def _get(self, client: httpx.Client, params: dict[str, str]) -> httpx.Response | None:
        if self.stopped or self.requests >= self.max_requests:
            return None
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        self.requests += 1
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = client.get(CERTSPOTTER_ENDPOINT, params=params, headers=headers)
        remaining = resp.headers.get("x-ratelimit-remaining")
        limit = resp.headers.get("x-ratelimit-limit")
        if limit is not None and limit.strip().isdigit():
            self.limit_seen = int(limit.strip())
        if resp.status_code == 429:
            retry = resp.headers.get("retry-after")
            self.stopped = f"CertSpotter の上限に達した（HTTP 429、Retry-After={retry}）"
        elif remaining is not None and remaining.strip() == "0":
            # **使い切ったら、次の1本を投げて断られる前にやめる**
            self.stopped = "CertSpotter の1時間あたりの上限を使い切った"
        return resp

    def page(self, domain: str, after: str | None) -> tuple[list[dict] | None, str | None]:
        """サブドメイン込みの検索を **1ページだけ** 引く。(行, 失敗の理由)。

        枠が小さい（Small で 10 本/時）ので、1本ごとに進み具合をキャッシュに書く
        （`subdomain_names_pass`）。
        """
        own = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        params = {"domain": domain, "include_subdomains": "true", "expand": "dns_names"}
        if after:
            params["after"] = after
        try:
            resp = self._get(client, params)
        except httpx.HTTPError as exc:
            return None, f"transport: {exc}"[:200]
        finally:
            if own:
                client.close()
        if resp is None:
            return None, self.stopped or "budget"
        if resp.status_code != 200:
            return None, f"http {resp.status_code}"
        try:
            rows = resp.json()
        except ValueError:
            return None, "decode"
        return list(rows or []), None

    def search(self, domain: str) -> CtResult:
        cached = self._cache_path(domain)
        if cached and cached.is_file():
            try:
                payload = json.loads(cached.read_text(encoding="utf-8"))
                return CtResult(
                    domain=domain,
                    found=payload.get("found", []),
                    raw_names=payload.get("raw_names", 0),
                    from_cache=True,
                    cache_month=payload.get("month"),
                    source=payload.get("source", "crtsh"),
                )
            except (json.JSONDecodeError, OSError):
                pass

        own = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        started = time.monotonic()
        seen: dict[str, None] = {}
        names_seen: list[str] = []
        raw = 0
        after: str | None = None
        pages = 0
        try:
            while True:
                params = {
                    "domain": domain,
                    "include_subdomains": "true" if self.include_subdomains else "false",
                    "expand": "dns_names",
                }
                if not self.include_subdomains:
                    params["match_wildcards"] = "true"
                if after:
                    params["after"] = after
                try:
                    resp = self._get(client, params)
                except httpx.HTTPError as exc:
                    return CtResult(
                        domain=domain,
                        error=f"CertSpotter: {exc}"[:200],
                        error_kind="transport",
                        source="certspotter",
                    )
                if resp is None:
                    return CtResult(
                        domain=domain,
                        error=self.stopped or "この実行の CertSpotter の枠を使い切った",
                        error_kind="budget",
                        attempts=0,
                        source="certspotter",
                    )
                if resp.status_code != 200:
                    return CtResult(
                        domain=domain,
                        error=f"CertSpotter: HTTP {resp.status_code}",
                        error_kind="http",
                        source="certspotter",
                    )
                try:
                    rows = resp.json()
                except ValueError:
                    return CtResult(
                        domain=domain,
                        error="CertSpotter: JSON として解釈できない応答",
                        error_kind="decode",
                        source="certspotter",
                    )
                pages += 1
                for row in rows or []:
                    for fqdn in row.get("dns_names") or []:
                        candidate = str(fqdn).strip().lstrip("*.").rstrip(".").lower()
                        if not candidate or " " in candidate:
                            continue
                        raw += 1
                        names_seen.append(candidate)
                        apex = etld_plus_one(candidate)
                        if apex:
                            seen.setdefault(apex, None)
                if len(rows or []) < CERTSPOTTER_PAGE_SIZE:
                    break
                if pages >= self.max_pages:
                    # **途中までを「全部」として書かない**（原則5）。
                    # 証明書の多い大企業は crt.sh が戻ったときに取る
                    return CtResult(
                        domain=domain,
                        error=f"CertSpotter: {pages} ページで打ち切った（まだ続きがある）",
                        error_kind="truncated",
                        source="certspotter",
                    )
                after = str(rows[-1].get("id"))
        finally:
            if own:
                client.close()

        result = CtResult(
            domain=domain,
            found=sorted(seen),
            raw_names=raw,
            mail_names=_collect_mail_names(names_seen, etld_plus_one(domain)),
            cache_month=self.month,
            duration_sec=round(time.monotonic() - started, 3),
            attempts=pages,
            source="certspotter",
        )
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(
                    {
                        "found": result.found,
                        "raw_names": result.raw_names,
                        "mail_names": result.mail_names,
                        "month": self.month,
                        "source": "certspotter",
                    }
                ),
                encoding="utf-8",
            )
        return result


@dataclass
class BackfillReport:
    month: str
    populations: dict[str, dict] = field(default_factory=dict)
    crtsh: dict = field(default_factory=dict)
    certspotter: dict = field(default_factory=dict)
    #: 流し直しを頼んでよい母集団（積み残しがほぼ無くなった）
    ready_for_rerun: list[str] = field(default_factory=list)
    #: 組織名検索の積み残しを引いた結果
    crtsh_org: dict = field(default_factory=dict)
    #: CertSpotter でサブドメインの名前を取った結果（BACKLOG 52）
    certspotter_sub: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "month": self.month,
            "populations": self.populations,
            "crtsh": self.crtsh,
            "certspotter": self.certspotter,
            "crtsh_org": self.crtsh_org,
            "certspotter_sub": self.certspotter_sub,
            "ready_for_rerun": self.ready_for_rerun,
        }


#: 積み残しがこの割合以下になったら、流し直してよい
RERUN_PENDING_RATE = 0.02
#: 1か月に頼む流し直しの上限。**頼み続けない**
MAX_RERUNS_PER_MONTH = 2
#: 2回目からの流し直しは、この割合（分母は起点全部）か `RERUN_MIN_FILLED`
#: の大きい方だけ新しく取れたときに限る。月次計測は国内で2時間半かかり、
#: その間は補充が止まる（ほかの母集団の補充も待つ）。2026-11-02 の国内は、
#: 1回目の流し直しのあとに残った数件を補充で埋めただけで2回目を頼んでいた
RERUN_MIN_FILLED_RATE = 0.005
RERUN_MIN_FILLED = 10


def backfill(
    cache_dir: Path,
    month: str,
    *,
    crtsh: CrtShClient | None,
    certspotter: CertSpotterClient | None,
    deadline: float | None = None,
    org_only: bool = False,
    certspotter_sub: CertSpotterClient | None = None,
) -> BackfillReport:
    """積み残しを取り直す。**crt.sh を先に、だめなら CertSpotter で。**

    取れたものは月のキャッシュに入り、積み残しから消える。
    """
    report = BackfillReport(month=month)
    # **組織名だけを引く回**（月次計測が走っている間）。ドメインの積み残しと
    # 流し直しの判断には触らない ── 月次計測の P2 と同じ積み残しを書き合わない
    paths = [] if org_only else list_pending(cache_dir, month)
    files = {p: read_pending(p) for p in paths}
    wanted: list[str] = []
    #: crt.sh を使わず CertSpotter だけで埋める起点（`via: certspotter`、グループ会社）
    cs_only: set[str] = set()
    for payload in files.values():
        for d in payload.get("pending", []):
            if d not in wanted:
                wanted.append(d)
            if payload.get("via") == "certspotter":
                cs_only.add(d)

    got: set[str] = set()

    def _cached(domain: str) -> bool:
        path = cache_dir / f"month={month}" / f"{domain.replace('/', '_')}.json"
        return path.is_file()

    remaining = [d for d in wanted if not _cached(d)]
    got.update(d for d in wanted if _cached(d))

    crtsh_remaining = [d for d in remaining if d not in cs_only]
    if crtsh is not None and crtsh_remaining:
        tried = 0
        # **応答待ちだけを重ねる**（P2 と同じ `prefetch`）。1本ずつ待つと、
        # 応答の中央値 11 秒に対して qps 0.5 の上限の1割も使えず、1回 45 分で
        # 150 件前後しか進まなかった（2026-11-01）。投げる間隔は 0.5 qps の
        # まま全スレッドで共有するので、crt.sh から見た本数は変わらない。
        # 持ち時間と「落ちている」の判定は、同時本数ぶんの塊ごとに見る
        chunk = max(int(getattr(crtsh, "concurrency", 1)), 1)
        for start in range(0, len(crtsh_remaining), chunk):
            if deadline is not None and time.monotonic() > deadline:
                break
            if crtsh.upstream_down:
                break
            batch = crtsh_remaining[start : start + chunk]
            for domain, result in crtsh.prefetch(batch).items():
                # 落ちていると見切った後の分は投げていない。本数に数えない
                if result.error_kind != "upstream_down":
                    tried += 1
                if not result.error:
                    got.add(domain)
        report.crtsh = {
            "tried": tried,
            "got": sum(1 for d in crtsh_remaining if d in got),
            "upstream_down": crtsh.upstream_down,
            "response": crtsh.response_stats(),
        }
        remaining = [d for d in remaining if d not in got]

    if certspotter is not None and remaining:
        tried = 0
        errors: dict[str, int] = {}
        for domain in remaining:
            if deadline is not None and time.monotonic() > deadline:
                break
            if certspotter.stopped or certspotter.requests >= certspotter.max_requests:
                break
            result = certspotter.search(domain)
            tried += 1
            if result.error:
                errors[result.error_kind or "error"] = (
                    errors.get(result.error_kind or "error", 0) + 1
                )
            else:
                got.add(domain)
        report.certspotter = {
            "tried": tried,
            "got": sum(1 for d in remaining if d in got),
            "requests": certspotter.requests,
            "ratelimit_limit": certspotter.limit_seen,
            "errors_by_kind": dict(sorted(errors.items())),
            "stopped": certspotter.stopped,
        }

    # **CertSpotter のもう1つの枠（サブドメイン込みの検索）で、メール用らしい名前を
    # 取る。** crt.sh を使わないので、組織名の補充と時間を取り合わない（先に回す）
    if certspotter_sub is not None:
        report.certspotter_sub = subdomain_names_pass(
            certspotter_sub, cache_dir, month, deadline=deadline
        )

    # **組織名の積み残しは、ドメインの積み残しを片づけた残りの時間で引く。**
    # 同じ crt.sh のクライアント（0.5 qps）を使うので、本数の上限は変わらない。
    # 流し直しは頼まない（次の月次計測で使われる）
    org_base = cache_dir / f"month={month}" / ORG_PENDING_DIRNAME
    org_files = sorted(org_base.glob("*.json")) if org_base.is_dir() else []
    if crtsh is not None and org_files and not crtsh.upstream_down:
        names: list[str] = []
        org_payloads = {p: read_pending(p) for p in org_files}
        for payload in org_payloads.values():
            for n in payload.get("pending", []):
                if n not in names:
                    names.append(n)
        todo = [n for n in names if crtsh.cached_org(n) is None]
        tried = got_org = 0
        org_errors: dict[str, int] = {}
        chunk = max(int(getattr(crtsh, "concurrency", 1)), 1)
        for start in range(0, len(todo), chunk):
            if deadline is not None and time.monotonic() > deadline:
                break
            if crtsh.upstream_down:
                break
            for _name, result in crtsh.prefetch_org(todo[start : start + chunk]).items():
                if result.error_kind != "upstream_down":
                    tried += 1
                if not result.error:
                    got_org += 1
                else:
                    # **取れなかった理由を数える。** 2026-11-08 は 8 本試して 0 本で、
                    # 理由が残っていなかった（大きい組織名は crt.sh がすぐ 502 を返す）
                    kind = result.error_kind or "other"
                    org_errors[kind] = org_errors.get(kind, 0) + 1
        left = 0
        for path, payload in org_payloads.items():
            payload["pending"] = [n for n in payload.get("pending", [])
                                  if crtsh.cached_org(n) is None]
            left += len(payload["pending"])
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        # **落ちていると見切ったことも残す。** 残さないと、ドメインの補充が無い回
        # （crtsh が空）は「crt.sh は落ちていない」と見なされ、組織名で1本でも
        # 取れれば次をすぐ頼む ── 2026-11-09、弱っている crt.sh に5分おきに
        # 8本ずつ投げ続けた（2026-11-01 と同じ形）
        report.crtsh_org = {"tried": tried, "got": got_org, "pending_after": left,
                            "errors_by_kind": dict(sorted(org_errors.items())),
                            "upstream_down": crtsh.upstream_down}

    for path, payload in files.items():
        pop = payload.get("population") or path.stem
        before = list(payload.get("pending", []))
        after = [d for d in before if d not in got]
        total = int(payload.get("total") or 0)
        payload["pending"] = after
        rate = (len(after) / total) if total else 0.0
        report.populations[pop] = {
            "total": total,
            "pending_before": len(before),
            "pending_after": len(after),
            "pending_rate": round(rate, 4),
            "reruns_requested": int(payload.get("reruns_requested", 0)),
        }
        # **取れたものがあり、積み残しがほぼ無くなったら流し直してよい。**
        # 回数に上限を置く（取れないまま残るドメインで頼み続けない）
        filled = len(before) - len(after)
        reruns = int(payload.get("reruns_requested", 0))
        # 1回目は取れたものがあればよい。**2回目からは取れた量を見る**
        need = 1 if reruns == 0 else max(RERUN_MIN_FILLED, int(total * RERUN_MIN_FILLED_RATE))
        report.populations[pop]["filled"] = filled
        if (
            before
            and filled >= need
            and rate <= RERUN_PENDING_RATE
            and reruns < MAX_RERUNS_PER_MONTH
        ):
            report.ready_for_rerun.append(pop)
        elif before and filled > 0 and rate <= RERUN_PENDING_RATE and reruns < MAX_RERUNS_PER_MONTH:
            report.populations[pop]["rerun_held"] = (
                f"取れたのは {filled} 件で、{reruns + 1} 回目の流し直しには {need} 件要る"
            )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def mark_rerun_requested(cache_dir: Path, month: str, population: str) -> None:
    path = pending_path(cache_dir, month, population)
    payload = read_pending(path)
    if not payload:
        return
    payload["reruns_requested"] = int(payload.get("reruns_requested", 0)) + 1
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
