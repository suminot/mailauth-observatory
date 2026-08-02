"""運用コンソールのバックエンド（DESIGN.md 第7章）。

開発・運用者だけが使う内部ツール。ローカル専用・認証なし。

    uvicorn console.backend.main:app --reload --port 8000

公開サイト（site/）とは性格がまったく違うので混同しないこと。
こちらはローカルの localhost にしか出さない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mailauth import PHASE_LABELS, PHASES, __version__
from mailauth.paths import data_root, gold_root, repo_root

from . import dict_edit, execute, inspect, runs

app = FastAPI(
    title="mailauth-observatory 運用コンソール",
    description="各フェーズの実行と工程メトリクスの可視化。ローカル専用",
    version=__version__,
)

# Vite の開発サーバ（5173）から叩けるようにする。ローカル専用なので localhost に限る
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(execute.router)
app.include_router(inspect.router)
app.include_router(dict_edit.router)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "repo_root": str(repo_root()),
        "data_root": str(data_root()),
        "gold_root": str(gold_root()),
        "phases": [{"id": p, "label": PHASE_LABELS[p]} for p in PHASES],
    }


# ビルド済みフロントエンドがあれば同じポートで配る。
# 無くても API は動く（開発中は Vite の dev server を使う）。
_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_dist / "index.html")
