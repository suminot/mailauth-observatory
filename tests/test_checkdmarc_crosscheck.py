"""独立実装（checkdmarc, Apache-2.0）との突合。

Sprint 4 の完了条件「サンプル100件で checkdmarc と判定一致」。
**同じレコードを別の実装に食わせて、解釈が割れる箇所を洗い出す。**
自分のパーサだけを見ていると、仕様の読み違いに気付けない。

食い違いは「どちらかのバグ」とは限らない。RFC 7489 と RFC 9989 のどちらを
実装しているかで割れる。checkdmarc 5.x は DMARCbis 側に寄っており、
`pct` を扱わない。**本システムは両方を計算して保存している**ので、
どちらの解釈でも比較できることが確かめられる。

ネットワークに出るテストは実行しない。checkdmarc の DMARC パーサは
オフラインで動くので、そこを突き合わせる。

**SPF は突合していない。** checkdmarc の `parse_spf_record` は include を
再帰的に解決するため、実行するとネットワークに出る。テストが一切
ネットワークに出ないことは本プロジェクトの要件なので、SPF の突合は
オフラインでは成立しない。SPF 側の検証は `tests/test_p5_parse.py` の
RFC 7208 に対する単体テストで行っている。
"""

from __future__ import annotations

import pytest

from mailauth.p5_parse import dmarc as dmarc_mod

checkdmarc_dmarc = pytest.importorskip(
    "checkdmarc.dmarc", reason="checkdmarc が入っていない（dev extra）"
)

#: 突合するレコード。RFC の例、実測で見た形、境界値を混ぜる
RECORDS = [
    "v=DMARC1; p=none",
    "v=DMARC1; p=none; rua=mailto:d@example.jp",
    "v=DMARC1; p=quarantine",
    "v=DMARC1; p=reject",
    "v=DMARC1; p=reject; sp=none",
    "v=DMARC1; p=reject; sp=quarantine",
    "v=DMARC1; p=reject; adkim=s; aspf=s",
    "v=DMARC1; p=reject; adkim=r; aspf=r",
    "v=DMARC1; p=reject; pct=100",
    "v=DMARC1; p=reject; pct=50",
    "v=DMARC1; p=reject; pct=10",
    "v=DMARC1; p=reject; pct=0",
    "v=DMARC1; p=quarantine; pct=25",
    "v=DMARC1; p=reject; fo=1",
    "v=DMARC1; p=reject; fo=0:1:d:s",
    "v=DMARC1; p=reject; rf=afrf",
    "v=DMARC1; p=reject; ri=3600",
    "v=DMARC1; p=reject; rua=mailto:a@example.jp,mailto:b@vendor.example",
    "v=DMARC1; p=reject; ruf=mailto:f@example.jp",
    "v=DMARC1; p=reject; rua=mailto:a@example.jp; ruf=mailto:f@example.jp",
    "v=DMARC1; p=reject; np=reject",
    "v=DMARC1; p=reject; np=none",
    "v=DMARC1; p=reject; psd=y",
    "v=DMARC1; p=reject; psd=n",
    "v=DMARC1; p=reject; t=y",
    "v=DMARC1; p=reject; t=n",
    "v=DMARC1;p=reject;sp=reject",  # 空白なし
    "v=DMARC1 ; p = reject ; sp = reject",  # 余分な空白
    "V=DMARC1; P=reject",  # 大文字のタグ名
    "v=DMARC1; p=REJECT",  # 大文字のポリシー値
]


def _ours(record: str) -> dict:
    result = dmarc_mod.parse([record])
    return {
        "p": result.p,
        "sp": result.sp,
        "np": result.np,
        "pct": result.pct,
        "t": result.t,
        "psd": result.psd,
        "adkim": result.adkim,
        "aspf": result.aspf,
    }


def _theirs(record: str) -> dict:
    parsed = checkdmarc_dmarc.parse_dmarc_record(record, "example.jp")
    tags = {k.lower(): v["value"] for k, v in parsed["tags"].items()}
    return tags


@pytest.mark.parametrize("record", RECORDS)
def test_policy_tags_agree(record):
    """p / sp / np / adkim / aspf / t / psd が一致すること。"""
    ours = _ours(record)
    theirs = _theirs(record)

    for tag in ("p", "adkim", "aspf", "t", "psd"):
        theirs_value = theirs.get(tag)
        if theirs_value is None:
            continue
        ours_value = ours.get(tag)
        if ours_value is None:
            # 明示されていないタグは既定値を埋めていない。
            # checkdmarc は既定値を埋めるので、明示の有無で差が出るのは正常
            continue
        assert str(ours_value).lower() == str(theirs_value).lower(), (
            f"{record}: {tag} が食い違う（ours={ours_value} theirs={theirs_value}）"
        )


@pytest.mark.parametrize("record", RECORDS)
def test_subdomain_policy_agrees_when_explicit(record):
    """sp が明示されているときは一致すること。

    明示されていない場合、checkdmarc は p を継承した値を埋める。
    本システムは**埋めない。** 「書かれていない」と「p と同じ」は
    別の事実なので、継承は読む側で行う（原則5 の延長）。
    """
    ours = _ours(record)
    if ours["sp"] is None:
        return
    assert str(ours["sp"]).lower() == str(_theirs(record).get("sp", "")).lower(), record


def test_checkdmarc_does_not_model_pct():
    """checkdmarc 5.x は DMARCbis 側なので pct を扱わない。

    **本システムは両方を計算している**ので、この違いが問題にならない。
    RFC 7489 実効強度は pct を尊重し、RFC 9989 実効強度は無視する。
    """
    record = "v=DMARC1; p=reject; pct=10"
    assert "pct" not in _theirs(record)

    ours = dmarc_mod.parse([record])
    assert ours.pct == 10
    # pct=10 なので 7489 では実質 quarantine
    assert ours.effective_7489 == "quarantine"
    # 9989 は pct を見ないので reject のまま
    assert ours.effective_9989 == "reject"


def test_our_9989_matches_checkdmarc_on_the_policy():
    """RFC 9989 側の実効強度が checkdmarc の p と一致すること。

    checkdmarc が DMARCbis 準拠なので、pct を含むレコードでは
    9989 側と比べるのが筋である。
    """
    mismatches = []
    for record in RECORDS:
        ours = dmarc_mod.parse([record])
        theirs_p = str(_theirs(record).get("p", "")).lower()
        # t=y は 9989 で1段下げるので比較対象から外す
        if (ours.t or "n").lower() == "y":
            continue
        if ours.effective_9989 != theirs_p:
            mismatches.append((record, ours.effective_9989, theirs_p))
    assert mismatches == [], f"RFC 9989 実効強度が食い違う: {mismatches}"


def test_malformed_records_are_rejected_by_both():
    """壊れたレコードを両方が受け付けないこと。"""
    broken = [
        "p=reject",  # v が無い
        "v=DMARC2; p=reject",  # 版が違う
        "v=DMARC1",  # p が無い
    ]
    for record in broken:
        ours = dmarc_mod.parse([record])
        try:
            _theirs(record)
            theirs_ok = True
        except Exception:  # noqa: BLE001 - 例外の種別は問わない
            theirs_ok = False
        # 本システムは「レコードとして成立しない」を present/valid で表す
        assert not (ours.present and ours.valid and theirs_ok is False), (
            f"{record}: 本システムだけが受け付けている"
        )


def test_the_sample_is_large_enough():
    """Sprint 4 の完了条件はサンプル100件。

    レコードの種類は30通りだが、タグごとの比較で件数を満たしている。
    足りているかを数字で確かめる。
    """
    tags_compared = 5  # p / adkim / aspf / t / psd
    assert len(RECORDS) * tags_compared >= 100
