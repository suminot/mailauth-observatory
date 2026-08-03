"""法務・倫理の受け入れ基準（DESIGN.md 第9章）をコード検査で確認する。

これらはコメントで宣言するだけでは守られない。CI で機械的に落とす。
"""

from __future__ import annotations

import re

import pytest

from mailauth.paths import repo_root

#: 検査対象。テスト自身と、方針を説明している文書は除く。
SEARCH_GLOBS = ["src/**/*.py", "console/**/*.py", "console/frontend/src/**/*.ts*", "configs/**/*"]
EXCLUDE_PARTS = {"node_modules", "dist", ".venv", "__pycache__"}


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
