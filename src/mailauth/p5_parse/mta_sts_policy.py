"""MTA-STS のポリシーを HTTPS で取って読む（RFC 8461、第2段）。

`_mta-sts` の TXT は「ポリシーがある」と言っているだけで、**強制しているのか
試しているだけなのかは HTTPS の先にしか書いていない。** TXT だけ数えると、
`mode: testing` や `mode: none`、取れないポリシー、MX と合わないポリシーも
「MTA-STS あり」に入る。

送る側の MTA が実際にやるのと同じ取り方をする（RFC 8461 §3.3）。

  - `https://mta-sts.<domain>/.well-known/mta-sts.txt` を1回だけ取る
  - 証明書は検証する。**リダイレクトは追わない**（追ってはならないと
    決まっている。追うと、送る側が失敗するポリシーを「取れた」と数える）
  - 64 KiB で打ち切る

取りに行くのは TXT に `v=STSv1` があるドメインだけで、国内で十数件、米国で
百数十件である。このファイルは送る側の MTA が誰でも取りに来ることを前提に
公開されている。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

#: RFC 8461 §3.2。これを超える max_age は不正
MAX_AGE_LIMIT = 31_557_600
#: 64 KiB（RFC 8461 §3.3 の推奨上限）
MAX_POLICY_BYTES = 64 * 1024
VALID_MODES = ("enforce", "testing", "none")


class FetchStatus:
    OK = "ok"
    #: 3xx。RFC は追うことを禁じている
    REDIRECT = "redirect"
    HTTP_ERROR = "http_error"
    TLS_ERROR = "tls_error"
    TIMEOUT = "timeout"
    #: 名前が引けない・つながらない
    UNREACHABLE = "unreachable"
    #: 取れたが中身がポリシーとして読めない
    INVALID = "invalid_policy"


@dataclass
class PolicyFetch:
    status: str
    body: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    error: str | None = None


PolicyFetcher = Callable[[str], PolicyFetch]


@dataclass
class Policy:
    valid: bool
    mode: str | None = None
    mx: list[str] = field(default_factory=list)
    max_age: int | None = None
    errors: list[str] = field(default_factory=list)


def parse_policy(text: str) -> Policy:
    """`key: value` を行ごとに読む。`mx` だけは複数ありうる。"""
    fields: dict[str, list[str]] = {}
    for line in re.split(r"\r?\n", text):
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields.setdefault(key.strip().lower(), []).append(value.strip())

    errors: list[str] = []
    version = (fields.get("version") or [""])[0]
    if version != "STSv1":
        errors.append(f"version が STSv1 でない: {version or '(無し)'}")
    mode = (fields.get("mode") or [""])[0].lower() or None
    if mode not in VALID_MODES:
        errors.append(f"mode が不正: {mode or '(無し)'}")
        mode = None
    max_age: int | None = None
    raw_age = (fields.get("max_age") or [""])[0]
    if raw_age.isdigit():
        max_age = int(raw_age)
        if max_age > MAX_AGE_LIMIT:
            errors.append(f"max_age が上限 {MAX_AGE_LIMIT} を超えている: {max_age}")
    else:
        errors.append(f"max_age が数でない: {raw_age or '(無し)'}")
    mx = [m.lower().rstrip(".") for m in fields.get("mx") or [] if m]
    if mode in ("enforce", "testing") and not mx:
        errors.append("mx が無い（none 以外では必須）")
    return Policy(valid=not errors, mode=mode, mx=mx, max_age=max_age, errors=errors)


def mx_matches(host: str, patterns: list[str]) -> bool:
    """RFC 8461 §4.1。`*.example.com` は**左端の1ラベルだけ**に当たる。"""
    host = host.lower().rstrip(".")
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and "." not in host[: -len(suffix)]:
                return True
        elif host == pattern:
            return True
    return False


def evaluate(fetch: PolicyFetch, mx_hosts: list[str]) -> dict:
    """facts に入れる値を作る。**取れなかったものに mode を付けない。**"""
    out: dict = {
        "mta_sts_policy_status": fetch.status,
        "mta_sts_mode": None,
        "mta_sts_max_age": None,
        "mta_sts_policy_mx": [],
        "mta_sts_mx_mismatch": None,
        "mta_sts_policy_raw": None,
    }
    if fetch.status != FetchStatus.OK or fetch.body is None:
        return out
    out["mta_sts_policy_raw"] = fetch.body[:MAX_POLICY_BYTES]
    policy = parse_policy(fetch.body)
    if not policy.valid:
        out["mta_sts_policy_status"] = FetchStatus.INVALID
        return out
    out.update(
        mta_sts_mode=policy.mode,
        mta_sts_max_age=policy.max_age,
        mta_sts_policy_mx=policy.mx,
    )
    # **enforce で MX がポリシーに無いと、送る側は配送をやめる。**
    # MX を移したのにポリシーを直し忘れた形で、メールが届かなくなる
    if policy.mode != "none" and mx_hosts:
        out["mta_sts_mx_mismatch"] = not all(mx_matches(h, policy.mx) for h in mx_hosts)
    return out


def live_fetcher(user_agent: str, timeout: float = 10.0) -> PolicyFetcher:
    """本番用。httpx で取る。**検査からは使わない**（ネットワークに出ない）。"""
    import httpx

    client = httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": user_agent},
    )

    def fetch(domain: str) -> PolicyFetch:
        url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
        try:
            with client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type")
                if 300 <= resp.status_code < 400:
                    return PolicyFetch(FetchStatus.REDIRECT, http_status=resp.status_code)
                if resp.status_code != 200:
                    return PolicyFetch(FetchStatus.HTTP_ERROR, http_status=resp.status_code)
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_POLICY_BYTES:
                        break
                return PolicyFetch(
                    FetchStatus.OK,
                    body=body[:MAX_POLICY_BYTES].decode("utf-8", errors="replace"),
                    http_status=200,
                    content_type=ctype,
                )
        except httpx.TimeoutException as exc:
            return PolicyFetch(FetchStatus.TIMEOUT, error=type(exc).__name__)
        except httpx.ConnectError as exc:
            # 証明書の失敗も ConnectError で来る。文面で分ける
            text = str(exc)
            status = (
                FetchStatus.TLS_ERROR
                if "CERTIFICATE" in text.upper() or "SSL" in text.upper()
                else FetchStatus.UNREACHABLE
            )
            return PolicyFetch(status, error=text[:200])
        except httpx.HTTPError as exc:
            return PolicyFetch(FetchStatus.UNREACHABLE, error=f"{type(exc).__name__}")

    return fetch
