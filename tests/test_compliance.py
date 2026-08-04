"""法務・倫理の受け入れ基準（DESIGN.md 第9章）をコード検査で確認する。

これらはコメントで宣言するだけでは守られない。CI で機械的に落とす。
"""

from __future__ import annotations

import re

import pytest

from mailauth.paths import repo_root

#: 検査対象。テスト自身と、方針を説明している文書は除く。
SEARCH_GLOBS = [
    "src/**/*.py",
    "console/**/*.py",
    "console/frontend/src/**/*.ts*",
    "configs/**/*",
    # 公開サイトのページ。ここが外向きの文面そのものなので必ず含める
    "site/src/**/*.md",
    "site/src/**/*.js",
    "site/*.js",
]
EXCLUDE_PARTS = {"node_modules", "dist", ".venv", "__pycache__", ".observablehq"}


def _files():
    root = repo_root()
    for pattern in SEARCH_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if EXCLUDE_PARTS & set(path.parts):
                continue
            yield path


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _effective_text(path) -> str:
    """コメントと docstring を除いた「実際に効く」部分だけを返す。

    「data_j.xls は使わない」と書いた方針の説明までを違反として拾ってしまうと、
    方針を明文化するほどテストが落ちるという逆立ちした状態になる。
    検査したいのは、実際に読みに行く URL やパスの値である。
    """
    text = _read(path)
    suffix = path.suffix.lower()

    if suffix == ".py":
        import ast

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        return "\n".join(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        )

    if suffix in {".yaml", ".yml"}:
        import yaml

        def scalars(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield str(k)
                    yield from scalars(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from scalars(v)
            elif obj is not None:
                yield str(obj)

        try:
            return "\n".join(scalars(yaml.safe_load(text)))
        except yaml.YAMLError:
            return text

    if suffix in {".ts", ".tsx"}:
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        return re.sub(r"//.*", " ", text)

    # csv / txt / rq などは行頭コメントだけ落とす
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "#+")))


#: 設定や文書で「使わない」と方針を書くこと自体は違反ではない。
#: 違反なのは実際に取りに行ける形（URL・パス）で書かれていること。
#: Python は _effective_text が docstring とコメントを落としたあとなので、
#: 文字列定数に出てきた時点で違反として扱う。
_PROSE_SUFFIXES = {".yaml", ".yml", ".md", ".csv", ".txt", ".rq"}


def _offenders(code_pattern: str, prose_pattern: str) -> list[str]:
    hits = []
    for p in _files():
        pattern = prose_pattern if p.suffix.lower() in _PROSE_SUFFIXES else code_pattern
        if re.search(pattern, _effective_text(p)):
            hits.append(str(p.relative_to(repo_root())))
    return hits


def test_jpx_data_file_is_never_referenced():
    """JPX の data_j.xls は商用二次利用が規約で禁止。非商用でも方針として使わない。"""
    offenders = _offenders(
        code_pattern=r"data_j\.xls|jpx\.co\.jp/markets/statistics-equities",
        # 文書側は、パス区切りに続く形か JPX の配布URLだけを違反とする
        prose_pattern=r"[/\\]data_j\.xls|jpx\.co\.jp/markets/statistics-equities",
    )
    assert offenders == [], f"JPX の銘柄一覧ファイルを参照している: {offenders}"


def test_fortune_com_is_never_fetched():
    """fortune.com はスクレイピングと二次利用を規約で禁止。"""
    offenders = [
        str(p.relative_to(repo_root()))
        for p in _files()
        if re.search(r"https?://[\w.]*fortune\.com", _effective_text(p))
    ]
    assert offenders == [], f"fortune.com にアクセスしうる記述がある: {offenders}"


def test_no_credentials_are_hardcoded():
    """認証情報は .env から読む。コードにも設定にも書かない。"""
    pattern = re.compile(
        r"(Subscription-Key|api[_-]?token|app[_-]?id)\s*[:=]\s*['\"][A-Za-z0-9]{16,}",
        re.IGNORECASE,
    )
    offenders = [
        str(p.relative_to(repo_root())) for p in _files() if pattern.search(_effective_text(p))
    ]
    assert offenders == [], f"認証情報がハードコードされている: {offenders}"


def test_dkim_l3_dictionary_stays_disabled():
    """Tatang 辞書は GPL-3.0。法務確認が済むまで L3 は無効のままにする。"""
    from mailauth.config import load_measure_config

    dkim = load_measure_config()["dkim"]
    assert dkim["l3_on_miss"] is False
    assert "l3_extended" not in dkim["layers"]
    assert dkim["l3_status"] == "planned"


def test_l3_selector_file_is_empty():
    from mailauth.config import load_selector_list

    assert load_selector_list("configs/dkim_selectors/l3_extended.txt") == []


def test_attribution_is_declared_for_every_enabled_population():
    """出典表記は公共データ利用規約1.0 / 政府標準利用規約2.0 の要件。"""
    from mailauth.config import list_populations

    for cfg in list_populations():
        if cfg.enabled:
            assert cfg.attribution, f"{cfg.id} に出典表記がない"


@pytest.mark.parametrize(
    "path",
    ["configs/dkim_selectors/l1_core.txt", "configs/measure.yaml"],
)
def test_configs_are_externalized_not_hardcoded(path):
    """原則7 ── 設定はコード外に出す。ファイルが実在することを確認する。"""
    assert (repo_root() / path).is_file()


def test_bronze_and_silver_are_not_committed():
    """git-scraping の履歴肥大化を避けるため data/runs/ は .gitignore に入れる。"""
    gitignore = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "data/runs/" in gitignore


# -- 公開サイトの表現上の規約（DESIGN.md P8） --------------------------------
#
# 断定的な語彙や順位付けは、実在企業の設定状態を名指しで公開する以上、
# 名誉毀損のリスクに直結する。文面はコードと同じ扱いで CI に通す。


def _site_pages():
    site = repo_root() / "site" / "src"
    if not site.is_dir():
        return []
    return [
        p
        for p in sorted(site.rglob("*.md"))
        if not EXCLUDE_PARTS & set(p.parts)
    ]


def test_site_pages_avoid_assertive_vocabulary():
    """「危険」「脆弱」等の断定と、A〜F グレードを使わない。"""
    from mailauth.p8_publish import vocabulary

    problems = []
    for path in _site_pages():
        for v in vocabulary.check(path.read_text(encoding="utf-8")):
            problems.append(
                f"{path.relative_to(repo_root())}: 「{v.term}」── {v.advice}"
            )
    assert problems == [], "公開ページの表現が規約に反している:\n  " + "\n  ".join(problems)


def test_site_shows_the_disclaimer_on_every_page():
    """限界の明示を常時表示する。フッタに入れて全ページに出す。"""
    from mailauth.p8_publish import vocabulary

    config = repo_root() / "site" / "observablehq.config.js"
    assert config.is_file(), "site/observablehq.config.js が無い"
    assert vocabulary.has_disclaimer(config.read_text(encoding="utf-8")), (
        f"フッタに「{vocabulary.DISCLAIMER}」が無い"
    )


def test_publish_config_keeps_tier2_disabled():
    """第2層は既定で無効。訂正期間を経ていない個社明細を出さない。"""
    from mailauth.config import load_yaml

    tier2 = load_yaml("configs/publish.yaml")["tier2"]
    assert tier2["enabled"] is False
    assert tier2["notified_on"] is None
    assert tier2["access_control_configured"] is False


def test_required_public_pages_exist():
    """DESIGN.md P8 の必須ページ。"""
    names = {p.stem for p in _site_pages()}
    required = {"index", "methodology", "terms", "corrections", "changelog", "data"}
    assert required <= names, f"必須ページが足りない: {sorted(required - names)}"


def test_site_data_is_not_committed():
    """gold を正本にする。同じ数字を二重に持つと片方だけ古くなる。"""
    gitignore = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "site/src/data/*.json" in gitignore
    assert "site/src/data/*.csv" in gitignore


def test_fortune_membership_is_not_reconstructed_as_a_population():
    """Fortune の順位は編集著作物。母集団として持たない。

    実測で所属リストが CC0 / CC BY-SA から500社規模で再構築できないことが
    分かっている。計測は us-all-listed で行い、リストが手に入ったら
    ビューとして重ねる（README「Fortune 500 は母集団ではなくビュー」）。
    """
    from mailauth.config import list_populations

    by_id = {c.id: c for c in list_populations()}
    for pid in ("us-fortune500", "global500"):
        assert by_id[pid].implemented is False, f"{pid} を母集団として有効にしている"
        assert "再構築できない" in (by_id[pid].blocked_by or ""), pid


def test_sec_user_agent_is_not_faked():
    """SEC は連絡先つきの User-Agent を必須としている。偽の値を埋めない。"""
    import re as _re

    offenders = [
        str(p.relative_to(repo_root()))
        for p in _files()
        if _re.search(
            r"User-Agent[\"']?\s*:\s*[\"'][^\"']*(example\.com|test@|noreply@)",
            _effective_text(p),
        )
    ]
    assert offenders == [], f"偽の連絡先を User-Agent に書いている: {offenders}"


def test_monthly_workflow_commits_even_without_changes():
    """60日自動停止対策（DESIGN.md Sprint 8）。

    GitHub Actions は60日間リポジトリに活動が無いと schedule を止める。
    データが変わらなかった月でも必ずコミットする必要がある。
    """
    workflow = repo_root() / ".github" / "workflows" / "monthly.yml"
    assert workflow.is_file(), "月次実行のワークフローが無い"
    text = workflow.read_text(encoding="utf-8")
    assert "--allow-empty" in text, "変更が無い月にコミットしない実装になっている"
    assert "schedule" in text and "cron" in text


def test_monthly_workflow_does_not_stop_at_the_first_failure():
    """途中で止めると manifest が揃わず、どこで何件落ちたか分からなくなる。"""
    text = (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(
        encoding="utf-8"
    )
    assert text.count("continue-on-error: true") >= 8, (
        "工程の途中で止まる実装になっている（原則4）"
    )
    # 最終的な成否は集約レポートで判断する
    assert "--fail-on-error" in text


def test_monthly_workflow_reads_credentials_from_secrets():
    """認証情報をワークフローに直書きしない。"""
    import re as _re

    text = (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(
        encoding="utf-8"
    )
    for line in text.splitlines():
        if _re.search(r"MAILAUTH_\w+:\s*\S", line):
            assert "secrets." in line, f"認証情報が直書きされている: {line.strip()}"
