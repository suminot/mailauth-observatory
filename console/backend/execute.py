"""フェーズの起動（画面2 フェーズ実行）。

CLI を subprocess で起動し、出力を行単位で貯めてストリーミングする。
コンソールがフェーズのロジックを持たないのは意図的で、
「コンソールから実行した結果」と「手で CLI を叩いた結果」を同じにするため。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import itertools
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mailauth import PHASES
from mailauth.paths import default_run_id, repo_root

router = APIRouter(prefix="/api", tags=["execute"])

_job_ids = itertools.count(1)

#: 同時に走らせるフェーズは1つに絞る。同じ run に対して2つ走ると
#: manifest の書き込みが競合し、原則6（冪等）が壊れる。
_run_lock = asyncio.Lock()


@dataclass
class Job:
    id: str
    argv: list[str]
    run_id: str
    phases: list[str]
    started_at: dt.datetime
    status: str = "running"  # running | success | failed | cancelled
    exit_code: int | None = None
    finished_at: dt.datetime | None = None
    lines: list[str] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": " ".join(shlex.quote(a) for a in self.argv),
            "run_id": self.run_id,
            "phases": self.phases,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "line_count": len(self.lines),
        }


_jobs: dict[str, Job] = {}


class ExecuteRequest(BaseModel):
    phases: list[str] = Field(..., description="実行するフェーズ。p1_population 形式")
    run_id: str | None = None
    config: str | None = None
    limit: int | None = None
    dry_run: bool = False
    source_file: str | None = None


def _build_argv(phase: str, req: ExecuteRequest, run_id: str) -> list[str]:
    cmd = phase.replace("_", "-", 1).replace("_", "-")
    argv = [sys.executable, "-m", "mailauth.cli", cmd, "--run", run_id]
    if phase == "p1_population":
        if req.config:
            argv += ["--config", req.config]
        if req.source_file:
            argv += ["--source-file", req.source_file]
    if req.limit:
        argv += ["--limit", str(req.limit)]
    if req.dry_run:
        argv += ["--dry-run"]
    return argv


async def _stream_process(job: Job, argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(repo_root()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    job.process = proc
    assert proc.stdout is not None
    async for raw in proc.stdout:
        job.lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
    return await proc.wait()


async def _run_job(job: Job, req: ExecuteRequest, run_id: str) -> None:
    async with _run_lock:
        try:
            for phase in job.phases:
                argv = _build_argv(phase, req, run_id)
                job.lines.append(f"$ {' '.join(shlex.quote(a) for a in argv)}")
                code = await _stream_process(job, argv)
                if code != 0:
                    job.status = "failed"
                    job.exit_code = code
                    job.lines.append(f"[exit {code}] 以降のフェーズを実行しません")
                    return
            job.status = "success"
            job.exit_code = 0
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - ジョブの失敗でサーバを落とさない
            job.status = "failed"
            job.lines.append(f"[error] {exc}")
        finally:
            job.finished_at = dt.datetime.now(dt.UTC)


@router.post("/jobs")
async def create_job(req: ExecuteRequest) -> dict[str, Any]:
    unknown = [p for p in req.phases if p not in PHASES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"不明なフェーズ: {unknown}")
    if not req.phases:
        raise HTTPException(status_code=400, detail="phases が空です")

    run_id = req.run_id or default_run_id()
    # DESIGN.md 3.2 の順序どおりに並べ替える。画面から順不同で来ても正しい順で走る
    phases = sorted(req.phases, key=PHASES.index)

    job = Job(
        id=str(next(_job_ids)),
        argv=_build_argv(phases[0], req, run_id),
        run_id=run_id,
        phases=phases,
        started_at=dt.datetime.now(dt.UTC),
    )
    _jobs[job.id] = job
    asyncio.create_task(_run_job(job, req, run_id))  # noqa: RUF006
    return job.view()


@router.get("/jobs")
def list_jobs() -> dict[str, Any]:
    ordered = sorted(_jobs.values(), key=lambda j: int(j.id), reverse=True)
    return {"jobs": [j.view() for j in ordered]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} がありません")
    return {**job.view(), "lines": job.lines}


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} がありません")
    if job.process and job.process.returncode is None:
        job.process.terminate()
        job.status = "cancelled"
    return job.view()


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    """実行ログの逐次表示（Server-Sent Events）。"""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {job_id} がありません")

    async def generator():
        sent = 0
        try:
            while True:
                while sent < len(job.lines):
                    line = job.lines[sent].replace("\r", "")
                    sent += 1
                    yield f"data: {line}\n\n"
                if job.status != "running" and sent >= len(job.lines):
                    yield f"event: done\ndata: {job.status}:{job.exit_code}\n\n"
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:  # クライアントが閉じた
            with contextlib.suppress(Exception):
                pass
            raise

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
