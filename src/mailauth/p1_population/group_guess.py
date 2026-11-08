"""企業サイトの登録が無い子会社の .co.jp を、読みから推測する（BACKLOG 52、2026-11-08）。

gBizINFO の法人検索で法人は1つに決まったが、企業ホームページの登録が無い
（`no_url`）子会社は 5,155 社あった。Wikidata にサイトがあるものは先に埋める。
残りを、上場会社と同じ推測器（`co_jp_guess.CoJpGuesser`）で推測する。
読み（カナ）と英字名は、法人番号で gBizINFO の法人基本情報から取る。

**上場会社より1つ多く確かめる：そのページに親会社が載っていること。**

- 子会社の商号はありふれていることが多い（「株式会社サンコー」は全国に
  何社もある）。.co.jp は1組織1つでも、**同じ商号の別の会社**が持っている
  ことがある。商号がページに載っていても、別の会社かもしれない
- 子会社のサイトはたいてい親会社に触れている（「◯◯グループ」・会社概要の
  株主・親会社のサイトへのリンク）。**商号を確かめたのと同じドメインの
  ページに、親会社の名前（法人格を除いた芯）・英字名・自社ドメインの
  どれかが載っているときだけ採る。** 子会社の商号の中に出る分は数えない
  （「親電機サービス」の「親電機」は親会社に触れたことにならない）
- 証明書の組織名（O）だけで決める経路は使わない（ページを読まないので、
  親会社を確かめられない）

推測の結果は上場会社と同じキャッシュ（`co_jp_homepage`）に、親会社の印も
鍵に入れて持ち越す。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..normalize import normalize_houjin_bangou
from ..ordered import in_input_order
from .co_jp_guess import _EN_SUFFIX, _alnum_upper, _squash, name_core
from .group_population import MANY_PARENTS

#: 親会社の日本語の芯がこれより短ければ使わない（「AOKI」程度は残す）
MIN_PARENT_CORE = 2
#: 親会社の英字名（法人格を除く）がこれより短ければ使わない
MIN_PARENT_EN = 6


def guess_targets(
    group_rows: dict[tuple[str, str], tuple[str, str, str]],
    parent_codes: Iterable[str],
    listed_numbers: set[str],
    sites: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """推測する子会社：法人番号 → (商号, 親の EDINET コード)。

    `build_group_entities` で外れるもの（親が上場していない・上場会社そのもの・
    3社以上の親の下に出る法人）と、Wikidata にサイトがあるものは引かない。
    同じ法人番号は EDINET コードの若い親に付ける（`build_group_entities` と同じ）。
    """
    parents = set(parent_codes)
    parents_of: dict[str, set[str]] = {}
    for (code, _name), (number, _domain, _status) in group_rows.items():
        hb = normalize_houjin_bangou(number) if number else None
        if hb:
            parents_of.setdefault(hb, set()).add(code)
    out: dict[str, tuple[str, str]] = {}
    for (code, name), (number, _domain, status) in sorted(group_rows.items()):
        if status != "no_url" or not number or code not in parents:
            continue
        hb = normalize_houjin_bangou(number)
        if not hb or hb in out or hb in listed_numbers or hb in sites:
            continue
        if len(parents_of.get(hb, ())) >= MANY_PARENTS:
            continue
        out[hb] = (name, code)
    return out


def parent_marks(name_ja: str | None, name_en: str | None, domains: Iterable[str]) -> list[str]:
    """ページに載っていれば「親会社に触れている」とみなす文字列（小文字・空白なし）。"""
    marks: list[str] = []
    core = _squash(name_core(name_ja)).lower()
    if len(core) >= MIN_PARENT_CORE:
        marks.append(core)
    en = _EN_SUFFIX.sub("", _alnum_upper(name_en or "")).lower()
    if len(en) >= MIN_PARENT_EN:
        marks.append(en)
    for d in sorted({d.lower() for d in domains if d}):
        marks.append(d)
    return marks


def _mentions(page: str, marks: list[str], own: str = "") -> bool:
    """ページに親会社の印があるか。**子会社自身の商号の中に出る分は数えない**
    （「親電機サービス」の中の「親電機」で、親会社に触れたことにしない）。"""
    text = _squash(page).lower()
    if own:
        text = text.replace(own, " ")
    # 英字名は記号を除いた形でも探す（「Toyota Motor」→ toyotamotor）
    alnum = "".join(ch for ch in text if ch.isalnum())
    return any(m in text or (m.isascii() and m.isalnum() and m in alnum) for m in marks)


@dataclass
class GroupGuessStats:
    targets: int = 0
    attempted: int = 0
    cache_hit: int = 0
    #: 推測で商号まで確かめられ、親会社も載っていた
    found: int = 0
    #: 商号は確かめられたが、そのドメインのページに親会社が載っていなかった
    no_parent_mention: int = 0
    #: 持ち時間を過ぎて引かなかった（次の流し直しで引く）
    deferred: int = 0
    no_reading: int = 0
    sample_found: list[str] = field(default_factory=list)
    sample_no_parent_mention: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "attempted": self.attempted,
            "cache_hit": self.cache_hit,
            "found": self.found,
            "no_parent_mention": self.no_parent_mention,
            "deferred": self.deferred,
            "no_reading": self.no_reading,
            "sample_found": self.sample_found[:20],
            "sample_no_parent_mention": self.sample_no_parent_mention[:20],
        }


def guess_subsidiaries(
    targets: dict[str, tuple[str, str]],
    readings: dict[str, dict[str, str]],
    marks_by_parent: dict[str, list[str]],
    guesser: Any,
    *,
    cache: Any = None,
    concurrency: int = 8,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, str], GroupGuessStats]:
    """子会社ごとに推測する。法人番号 → ドメイン（親会社が載っていたものだけ）。

    `guesser` は `CoJpGuesser`。`fetch` を包んで、親会社が載っていたページの
    ホストを覚える（`fetch` は推測を呼んだスレッドで呼ばれる）。
    """
    stats = GroupGuessStats(targets=len(targets))
    lock = threading.Lock()
    local = threading.local()
    inner_fetch = guesser.fetch

    def fetch(url: str) -> str | None:
        page = inner_fetch(url)
        marks = getattr(local, "marks", None)
        if page is not None and marks and _mentions(page, marks, local.own):
            host = (urlsplit(url).hostname or "").lower()
            local.hosts.add(host)
        return page

    guesser.fetch = fetch
    # ページを読まずに決める経路（証明書の組織名）は使わない（モジュールの説明）
    guesser.cert_org = None

    def bump(name: str, sample: str | None = None, key: str | None = None) -> None:
        with lock:
            setattr(stats, name, getattr(stats, name) + 1)
            if sample and key:
                bucket = getattr(stats, key)
                if len(bucket) < 20:
                    bucket.append(sample)

    def one(item: tuple[str, tuple[str, str]]) -> str | None:
        number, (name, code) = item
        reading = readings.get(number) or {}
        kana, name_en = reading.get("kana") or None, reading.get("name_en") or None
        if not kana and not name_en:
            bump("no_reading")
            return None
        marks = marks_by_parent.get(code) or []
        args = (name, name_en, kana, "parent=" + "|".join(marks))
        if cache is not None:
            hit = cache.get(args)
            if hit is not None:
                bump("cache_hit")
                if hit[0]:
                    bump("found")
                return hit[0]
        if deadline is not None and clock() > deadline:
            bump("deferred")
            return None
        if not marks:
            return None
        bump("attempted")
        local.marks, local.hosts = marks, set()
        local.own = _squash(name_core(name)).lower()
        try:
            domain, kind = guesser.guess(name, name_en, kana)
        finally:
            hosts, local.marks = local.hosts, None
        if domain:
            if any(h == domain or h.endswith("." + domain) for h in hosts):
                bump("found", f"{domain} ← {name}", "sample_found")
            else:
                bump("no_parent_mention", f"{domain} ← {name}", "sample_no_parent_mention")
                domain, kind = None, "no_parent_mention"
        if cache is not None and kind != "unreachable":
            cache.put(args, domain, kind)
        return domain

    items = sorted(targets.items())
    found: dict[str, str] = {}
    try:
        results = in_input_order(one, items, concurrency=concurrency, window=64)
        for (number, _v), domain in zip(items, results, strict=True):
            if domain:
                found[number] = domain
    finally:
        guesser.fetch = inner_fetch
    return found, stats
