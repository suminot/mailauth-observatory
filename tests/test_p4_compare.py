"""P4 手法比較。

DESIGN.md P4「バックエンドの抽象化」は「複数手法を比較できることが要件」と
明記している。検証の主眼は差分の分類が意味を持つこと。
  - 「両方観測できたのに食い違う」と「片方が引けなかった」を混ぜない（原則5）
  - 引いている purpose が違うだけの組み合わせを差分として数えない
"""

from __future__ import annotations

import pytest

from mailauth.contracts import DOMAIN_ARROW_SCHEMA, MeasureTier
from mailauth.io import write_parquet
from mailauth.p4_measure import UnknownBackendError
from mailauth.p4_measure import compare as compare_mod
from mailauth.p4_measure import run as run_p4
from mailauth.p4_measure.compare_runner import read_compare
from mailauth.p4_measure.compare_runner import run as run_compare
from mailauth.p4_measure.crosscheck import sample_targets
from mailauth.p4_measure.runner import make_backend
from mailauth.paths import phase_output
from mailauth.resolver import StaticResolver, make_answer

RUN = "2026-08"


def _domains() -> None:
    write_parquet(
        [
            {
                "domain_id": "d:1", "entity_id": "jp:1", "run_id": RUN,
                "domain": "a.example.jp", "domain_role": "primary",
                "confidence": "confirmed", "is_measured": True,
                "measure_tier": MeasureTier.C, "evidence_count": 3,
            },
            {
                "domain_id": "d:2", "entity_id": "jp:1", "run_id": RUN,
                "domain": "b.example.jp", "domain_role": "related",
                "confidence": "likely", "is_measured": True,
                "measure_tier": MeasureTier.C, "evidence_count": 2,
            },
        ],
        phase_output(RUN, "p3_domains", "domains.parquet"),
        DOMAIN_ARROW_SCHEMA,
    )


class _Backend:
    version = "test"

    def __init__(self, answers, label="fake"):
        self._r = StaticResolver(answers)
        self.name = label
        self.resolver_label = label
        self.stats = {"queries": 0, "cache_hits": 0, "tcp_failed": 0}

    def query(self, query):
        self.stats["queries"] += 1
        return self._r.query(query.name, query.rtype)


def _answers(*, spf="v=spf1 -all", mx="mx.example.", present=True, observed=True):
    out = {}
    for domain in ("a.example.jp", "b.example.jp"):
        out[(domain, "MX")] = make_answer(
            domain, "MX", [mx] if present else [], observed=observed
        )
        out[(domain, "TXT")] = make_answer(
            domain, "TXT", [spf] if present else [], observed=observed
        )
        out[(f"_dmarc.{domain}", "TXT")] = make_answer(
            f"_dmarc.{domain}", "TXT", ["v=DMARC1; p=none"]
        )
    return out


# ===========================================================================
# 抽出
# ===========================================================================


def test_sampling_is_deterministic():
    """毎回違うドメインを引くと月次で差分を追えない。"""
    targets = [{"domain": f"d{i}.example"} for i in range(100)]
    a = sample_targets(targets, rate=0.05, run_id=RUN)
    b = sample_targets(targets, rate=0.05, run_id=RUN)
    assert a == b
    assert len(a) == 5
    # run_id が違えば別の抽出になる
    assert sample_targets(targets, rate=0.05, run_id="2026-09") != a


def test_sampling_always_takes_at_least_one():
    targets = [{"domain": "only.example"}]
    assert len(sample_targets(targets, rate=0.01, run_id=RUN)) == 1
    assert sample_targets(targets, rate=0, run_id=RUN) == []
    assert len(sample_targets(targets, rate=1.0, run_id=RUN)) == 1


# ===========================================================================
# バックエンドのラベル
# ===========================================================================


def test_resolver_can_be_pinned_by_label():
    """`dnspython@1.1.1.1` でリゾルバを固定できる。"""
    backend = make_backend("dnspython@1.1.1.1", {"resolver": {}, "rate": {}})
    assert backend.name == "dnspython"


def test_pinning_a_resolver_on_zdns_is_rejected():
    with pytest.raises(UnknownBackendError, match="dnspython のみ"):
        make_backend("zdns@1.1.1.1", {"resolver": {}, "rate": {}})


# ===========================================================================
# 差分の分類
# ===========================================================================


def test_identical_methods_agree():
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers()))

    result = compare_mod.compare(RUN, ["alpha", "beta"])
    assert result.compared > 0
    assert result.counts.get(compare_mod.AGREE) == result.compared
    assert not result.counts.get(compare_mod.VALUES_DIFFER)


def test_differing_values_are_detected():
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers(spf="v=spf1 -all")))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers(spf="v=spf1 ~all")))

    result = compare_mod.compare(RUN, ["alpha", "beta"])
    assert result.counts.get(compare_mod.VALUES_DIFFER) == 2
    sample = result.samples[compare_mod.VALUES_DIFFER][0]
    assert sample["detail"]["alpha"] != sample["detail"]["beta"]


def test_presence_disagreement_is_the_heaviest_class():
    """両方観測できたのにレコードの有無が食い違う。"""
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers(present=True)))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers(present=False)))

    result = compare_mod.compare(RUN, ["alpha", "beta"])
    assert result.counts.get(compare_mod.PRESENCE_DIFFERS)
    assert "食い違" in " / ".join(result.notes)


def test_a_failed_query_is_not_a_disagreement():
    """片方が引けなかっただけ。手法の食い違いではない（原則5）。"""
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers(observed=True)))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers(observed=False)))

    result = compare_mod.compare(RUN, ["alpha", "beta"])
    assert result.counts.get(compare_mod.OBSERVATION_DIFFERS)
    # レコードの有無の食い違いとしては数えない
    assert not result.counts.get(compare_mod.PRESENCE_DIFFERS)
    assert not result.counts.get(compare_mod.VALUES_DIFFER)


def test_a_single_method_cannot_be_compared():
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    result = compare_mod.compare(RUN, ["alpha"])
    assert result.compared == 0
    assert "2つ以上" in " / ".join(result.notes)


def test_different_plans_are_not_counted_as_divergences():
    """クロスチェックは3 purpose しか引かない。

    フル計測と突き合わせて数百件の「片方にしかない」を出すと、
    本当の食い違いが埋もれる。
    """
    _domains()
    run_p4(run_id=RUN, method="full", backend=_Backend(_answers()), tier=None)
    # C 階層でも dmarc_subdomain / dkim_wildcard を引くので purpose 数が違う
    from mailauth.p4_measure.crosscheck import measure_with
    from mailauth.paths import bronze_dir

    measure_with(
        _Backend(_answers(), label="cross"),
        [{"domain": "a.example.jp"}, {"domain": "b.example.jp"}],
        run_id=RUN,
        method_label="cross",
        bronze_root=bronze_dir(RUN),
    )

    result = compare_mod.compare(RUN, ["full", "cross"])
    assert result.plan_differs, "purpose の違いが記録されていない"
    only = next(iter(result.plan_differs.values()))
    assert "dkim_wildcard" in only["full"]
    # 個別の差分としては数えない
    assert not result.counts.get(compare_mod.ONLY_IN)


# ===========================================================================
# 通し
# ===========================================================================


def test_compare_is_read_only_by_default():
    """resolvers を渡さなければ DNS に出ない。"""
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers()))

    result = run_compare(run_id=RUN)
    assert result["status"] == "success"
    assert result["breakdown"]["compared_methods"] == ["alpha", "beta"]
    assert not result["breakdown"]["crosscheck"]

    payload = read_compare(RUN)
    assert payload["agreement_rate"] == 1.0


def test_compare_does_not_overwrite_the_measure_manifest():
    """P4 と同じディレクトリに書くと計測の manifest が消える。"""
    from mailauth.manifest import read_manifest
    from mailauth.paths import phase_dir

    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    run_compare(run_id=RUN)

    measure = read_manifest(phase_dir(RUN, "p4_measure"))
    assert measure["phase"] == "p4_measure"
    compare = read_manifest(phase_dir(RUN, "p4_measure") / "compare")
    assert compare["phase"] == "p4_measure_compare"


def test_compare_warns_about_a_method_without_bronze():
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    result = run_compare(run_id=RUN, methods=["alpha", "zdns"])
    assert any(w["code"] == "METHOD_NOT_MEASURED" for w in result["warnings"])
    assert any(w["code"] == "NOT_ENOUGH_METHODS" for w in result["warnings"])


def test_compare_dry_run_writes_nothing():
    _domains()
    run_p4(run_id=RUN, method="alpha", backend=_Backend(_answers()))
    run_p4(run_id=RUN, method="beta", backend=_Backend(_answers()))
    result = run_compare(run_id=RUN, dry_run=True)
    assert any(w["code"] == "DRY_RUN" for w in result["warnings"])
    assert read_compare(RUN) is None
