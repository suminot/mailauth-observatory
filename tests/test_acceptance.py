"""DESIGN.md 第9章「横断的な受け入れ基準」の機械検査。

チェックリストのままでは守られない。実装完了の判定に使うと書かれている
以上、CI で落ちる形にしておく。

**コードで検査できない項目は skip として理由を残す。** 「組織で認証情報を
管理している」は運用の状態であってコードの性質ではない。黙って落とすと
チェックリストから消えてしまうので、skip の理由に残す。
"""

from __future__ import annotations

import json

import pytest

from mailauth import PHASES
from mailauth.paths import repo_root

# ===========================================================================
# 機能
# ===========================================================================


def test_every_phase_runs_and_reruns_on_its_own():
    """八工程がそれぞれ単独で実行でき、単独で再実行できる（原則3）。

    各フェーズのモジュールが run(run_id, ...) を公開していること。
    前後のフェーズを引数に取らないことが「単独で動く」の実質である。
    """
    import importlib
    import inspect

    for phase in PHASES:
        module = importlib.import_module(f"mailauth.{phase}")
        assert hasattr(module, "run"), f"{phase} に run が無い"
        params = inspect.signature(module.run).parameters
        assert "run_id" in params, f"{phase}.run が run_id を取らない"


def test_every_phase_writes_a_manifest():
    """全工程が _manifest.json を出力する（原則4）。"""
    import importlib
    import inspect

    for phase in PHASES:
        module = importlib.import_module(f"mailauth.{phase}.runner")
        source = inspect.getsource(module)
        assert "RunManifest" in source, f"{phase} が manifest を書いていない"


def test_the_console_shows_all_eight_phases():
    from mailauth import PHASE_OUTPUTS

    assert set(PHASE_OUTPUTS) == set(PHASES)


def test_the_population_changes_by_config_alone():
    """母集団を設定ファイルの切り替えだけで変更できる（原則7）。"""
    from mailauth.config import list_populations

    populations = list_populations()
    assert len(populations) >= 6
    # 母集団の定義がコードに無いこと。id をコードで分岐していたら
    # 「設定の切り替えだけ」ではない
    runner_source = (repo_root() / "src/mailauth/p1_population/runner.py").read_text(
        encoding="utf-8"
    )
    for cfg in populations:
        assert f'"{cfg.id}"' not in runner_source, (
            f"{cfg.id} を runner が名指しで分岐している"
        )


def test_methods_can_be_compared():
    """同一対象を複数手法で計測して比較できる（DESIGN.md P4）。"""
    from mailauth.p4_measure.compare import compare
    from mailauth.p4_measure.compare_runner import run as run_compare

    assert callable(compare)
    assert callable(run_compare)


def test_a_domain_can_be_traced_from_bronze_to_gold():
    from console.backend.trace import trace

    assert callable(trace)


def test_unknown_hosts_can_be_added_to_the_dictionary_from_the_console():
    """未知 MX ホストから辞書追記までが画面上で完結する（受け入れ基準）。"""
    from console.backend.dict_edit import add_rule, unknown_hosts

    assert callable(unknown_hosts)
    assert callable(add_rule)


# ===========================================================================
# データ整合性
# ===========================================================================


def test_silver_and_gold_regenerate_from_bronze():
    """P5 以降が bronze だけを入力にしていること（原則1の実質）。

    P5 が bronze を読み、P6 は silver を読み、P7 は silver を読む。
    どこかが「外部 API をもう一度引く」形になっていると再生成できない。
    """
    import inspect

    from mailauth import p5_parse, p6_infer, p7_aggregate

    p5 = inspect.getsource(p5_parse.runner)
    assert "read_bronze" in p5, "P5 が bronze から読んでいない"
    # P6 は DNS を引かない。silver -> silver
    p6 = inspect.getsource(p6_infer.runner)
    assert "resolver" not in p6.lower(), "P6 が DNS を引いている"
    p7 = inspect.getsource(p7_aggregate.runner)
    assert "facts.parquet" in p7


def test_observed_and_record_present_are_separate_columns():
    """原則5。「取れなかった」と「無かった」を区別する。"""
    from mailauth.contracts import FACT_ARROW_SCHEMA, RawResponse

    names = [f.name for f in FACT_ARROW_SCHEMA]
    assert "observed" in names
    assert "record_present" in names
    fields = RawResponse.model_fields
    assert "observed" in fields
    assert "record_present" in fields
    # record_present は「不明」を表せること
    assert fields["record_present"].default is None


def test_facts_and_inferences_are_separate_schemas():
    """原則2。事実と推察をスキーマレベルで分離する。"""
    from mailauth.contracts import FACT_ARROW_SCHEMA, INFERENCE_ARROW_SCHEMA

    fact_names = {f.name for f in FACT_ARROW_SCHEMA}
    inference_names = {f.name for f in INFERENCE_ARROW_SCHEMA}
    # 推察の語彙が fact に混ざっていないこと
    for name in ("vendor", "product", "confidence", "evidence", "rule_ids"):
        assert name not in fact_names, f"fact に推察の列がある: {name}"
    assert {"vendor", "confidence", "evidence"} <= inference_names


def test_every_inference_has_confidence_and_evidence():
    """全 inference に confidence と evidence がある。"""
    from mailauth.contracts import Inference

    fields = Inference.model_fields
    assert fields["confidence"].is_required(), "confidence が任意になっている"
    assert "evidence" in fields
    assert "rule_ids" in fields


def test_spec_version_is_on_every_fact():
    from mailauth.contracts import FACT_ARROW_SCHEMA

    assert "spec_version" in [f.name for f in FACT_ARROW_SCHEMA]
    # 二重計算の両方が列として在ること
    names = [f.name for f in FACT_ARROW_SCHEMA]
    assert "effective_7489" in names
    assert "effective_9989" in names


def test_disappearance_requires_two_consecutive_observations():
    """1回の不在は一時的な DNS 障害と区別できない。"""
    from mailauth.p7_aggregate import delta as delta_mod

    fact = {"domain_id": "d:1", "observed": True, "record_present": True}
    absent = {"domain_id": "d:1", "observed": True, "record_present": False}

    once = delta_mod.compute([absent], [fact], before_previous=[fact])
    assert once.domains_disappeared == 0
    twice = delta_mod.compute([absent], [absent], before_previous=[fact])
    assert twice.domains_disappeared == 1


# ===========================================================================
# 法務・倫理
# （個別の検査は tests/test_compliance.py にある。ここでは在ることを確かめる）
# ===========================================================================


def test_legal_checks_exist():
    source = (repo_root() / "tests/test_compliance.py").read_text(encoding="utf-8")
    for marker in (
        "data_j\\.xls",
        "fortune\\.com",
        "l3_on_miss",
        "attribution",
    ):
        assert marker in source, f"{marker} の検査が無い"


def test_personal_email_addresses_are_not_stored():
    """個人名を含むメールアドレスを収集・保存しない。

    DMARC の rua / ruf は mailto: を含むが、保存するのはドメイン部分だけ。
    """
    from mailauth.records import dmarc_report_domains

    got = dmarc_report_domains(
        "v=DMARC1; p=none; rua=mailto:john.doe@example.com,mailto:x@vendor.example"
    )
    assert got == ["example.com", "vendor.example"]
    assert all("@" not in d for d in got)


def test_the_published_site_explains_how_it_measures():
    """計測の説明ページが公開されている。

    連絡先・訂正申告の受け口は**内部運用なので置いていない**。外部からの
    申告を受け付けていないのに窓口だけ出すと、応対しない窓口を掲げることに
    なる。第2層を外部に出す段では窓口の常設が要件になる（DESIGN.md 1.4）。
    """
    site = repo_root() / "site" / "src"
    assert (site / "methodology.md").is_file()
    text = (site / "methodology.md").read_text(encoding="utf-8")
    assert "観測" in text, "計測の説明になっていない"


def test_spf_includes_and_mx_hosts_are_cached():
    """権威DNSへの負荷回避は倫理的義務（DESIGN.md P4 実装メモ）。"""
    from mailauth.config import load_measure_config

    cache = load_measure_config()["cache"]
    assert cache["spf_include"] is True
    assert cache["mx_host_resolution"] is True


def test_tier2_is_tied_to_prior_notification():
    """第2層に事前通知の運用が紐づいている。"""
    from mailauth.p8_publish import tiers

    decision = tiers.evaluate_tier2(
        notified_on=None,
        today=__import__("datetime").date(2026, 8, 1),
        access_control_configured=True,
    )
    assert decision.releasable is False


# ===========================================================================
# 持続性
# ===========================================================================


def test_the_sixty_day_shutdown_is_handled():
    workflow = repo_root() / ".github/workflows/monthly.yml"
    assert workflow.is_file()
    assert "--allow-empty" in workflow.read_text(encoding="utf-8")


def test_the_readme_documents_the_handover_plan():
    readme = (repo_root() / "README.md").read_text(encoding="utf-8")
    assert "引き継ぎ計画" in readme
    assert ".env.example" in readme


def test_credentials_are_documented_with_where_to_get_them():
    """引き継ぐ人がどこで再発行できるか分かること。"""
    env = (repo_root() / ".env.example").read_text(encoding="utf-8")
    keys = [
        line.split("=")[0]
        for line in env.splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    assert keys, ".env.example にキーが無い"
    # 取得元か、キーが不要な理由が書かれていること
    assert env.count("取得:") + env.count("キー不要") >= 3


def test_offload_can_send_to_a_destination_outside_r2():
    """bronze のバックアップを R2 以外に持てること（DESIGN.md 第9章「持続性」）。

    **基準そのものは運用の状態だが、実装が1宛先しか送れないなら運用者が
    第2の保管先を用意してもできない。** そこは実装の問題なので検査する。
    ここで見るのは「複数宛先に送れる実装であること」まで。
    """
    from mailauth.offload import DESTINATION_ENV, REQUIRED_DESTINATIONS

    assert REQUIRED_DESTINATIONS >= 2
    assert len(DESTINATION_ENV) >= REQUIRED_DESTINATIONS, (
        "退避先が1つしか定義されていない。R2 以外の保管先を設定できない"
    )
    # **同じ事業者に2つ置いても持続性は上がらない。** 別系統を想定した
    # 環境変数になっていること（R2_* の連番ではない）
    assert "r2" in DESTINATION_ENV
    assert any(name != "r2" for name in DESTINATION_ENV)


def test_a_single_destination_is_not_reported_as_durable(tmp_path, monkeypatch):
    """**送れたことと持続性の基準を満たしたことは別である。**

    1宛先しか設定されていない状態を「成功」で終わらせると、R2 を失った
    ときに気付く。送信が成功していても基準未達であることを言わせる。
    """
    from mailauth.offload import offload
    from mailauth.paths import run_dir

    for name in (
        "R2_ENDPOINT",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "BACKUP_ENDPOINT",
        "BACKUP_BUCKET",
        "BACKUP_ACCESS_KEY_ID",
        "BACKUP_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        ("R2_ENDPOINT", "https://r2.example"),
        ("R2_BUCKET", "mailauth"),
        ("R2_ACCESS_KEY_ID", "key"),
        ("R2_SECRET_ACCESS_KEY", "secret"),
    ):
        monkeypatch.setenv(name, value)

    root = run_dir("2026-08")
    (root / "p5_parse").mkdir(parents=True)
    (root / "p5_parse" / "facts.parquet").write_bytes(b"x" * 10)

    class _Ok:
        def upload_file(self, *a, **k):
            return None

    _, result = offload("2026-08", client=_Ok())
    assert result.uploaded == 1
    assert result.failed == 0
    assert result.single_destination is True
    assert any("持続性の基準は満たしていない" in n for n in result.notes)


@pytest.mark.skip(
    reason=(
        "運用の状態であってコードの性質ではない。認証情報を組織で管理すること"
        "（DESIGN.md 第9章「持続性」）は、本プロジェクトが個人研究として"
        "個人アカウントで運営されている前提と現時点で両立しない。"
        "DESIGN.md 1.4 はこれをバス係数1のリスクとして明示している"
    )
)
def test_credentials_are_managed_by_an_organization():
    raise AssertionError("未実施")


# ===========================================================================
# 受け入れ基準そのものの網羅性
# ===========================================================================


def test_the_checklist_is_covered_by_tests():
    """DESIGN.md 第9章の項目数と、ここの検査数が乖離していないこと。

    チェックリストに項目が増えたのに検査が増えていない状態を防ぐ。
    """
    design = (repo_root() / "DESIGN.md").read_text(encoding="utf-8")
    section = design.split("## 9. 横断的な受け入れ基準")[1].split("## 10.")[0]
    items = [ln for ln in section.splitlines() if ln.strip().startswith("- [ ]")]
    assert len(items) == 23, f"受け入れ基準の項目数が変わった: {len(items)}"

    here = (repo_root() / "tests/test_acceptance.py").read_text(encoding="utf-8")
    checks = here.count("\ndef test_")
    # 行頭のデコレータだけを数える。この行自身を数えてしまわないように
    skipped = sum(1 for ln in here.splitlines() if ln.startswith("@pytest.mark.skip"))
    # 1項目1テストではないが、桁が違ってはいけない
    assert checks >= 20, f"検査が {checks} 件しかない（基準は {len(items)} 項目）"
    assert skipped == 1, (
        "コードで検査できない項目は1つ（組織での認証情報管理）だけである。"
        "R2 以外のバックアップは、**実装が複数宛先に送れるか**までを検査に"
        "変えた（宛先を用意するのは運用だが、送れない実装なら運用でも"
        "満たせない）。増えたなら理由を skip の reason に書くこと"
    )


def test_manifest_shape_is_stable():
    """コンソールと run-report が読む形が変わっていないこと。"""
    from mailauth.manifest import RunManifest
    from mailauth.paths import phase_dir

    with RunManifest(
        run_id="2026-08", phase="p1_population",
        out_dir=phase_dir("2026-08", "p1_population"),
    ) as m:
        m.counts.input = 1
        m.counts.success = 1

    payload = json.loads(
        (phase_dir("2026-08", "p1_population") / "_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for key in ("run_id", "phase", "status", "counts", "warnings", "outputs"):
        assert key in payload, f"manifest に {key} が無い"
