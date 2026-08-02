// 画面1 ── パイプライン全景（DESIGN.md 7.2）
//
// 最重要要件は「どこで何件落ちたかが一目で分かる」こと。
// そのため工程カードの間に件数の変化を必ず出す。

import { useEffect, useState } from "react";
import { api, type PhaseView, type RunView } from "./api";

function statusClass(status: string): string {
  return `badge badge-${status}`;
}

function formatDuration(sec: number | null): string {
  if (sec == null) return "";
  if (sec < 60) return `${sec.toFixed(1)}秒`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}分${s}秒`;
}

function Bar({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div className="bar">
      <div className="bar-fill" style={{ width: `${Math.min(100, pct)}%` }} />
      <span className="bar-label">{total > 0 ? `${pct.toFixed(1)}%` : "―"}</span>
    </div>
  );
}

function PhaseCard({
  phase,
  onRerun,
}: {
  phase: PhaseView;
  onRerun: (phase: string) => void;
}) {
  const c = phase.counts;
  const failures = Object.entries(phase.failure_breakdown).sort((a, b) => b[1] - a[1]);
  const maxFailure = failures.length ? failures[0][1] : 0;

  return (
    <div className={`card card-${phase.status}`}>
      <div className="card-head">
        <strong>
          {phase.phase.toUpperCase().split("_")[0]} {phase.label}
        </strong>
        <span className={statusClass(phase.status)}>{phase.status}</span>
      </div>

      {phase.started_at && (
        <div className="muted small">
          {new Date(phase.started_at).toLocaleString("ja-JP")}
          {phase.finished_at && ` → ${new Date(phase.finished_at).toLocaleTimeString("ja-JP")}`}
          {phase.duration_sec != null && ` (${formatDuration(phase.duration_sec)})`}
        </div>
      )}

      {c ? (
        <>
          <div className="counts">
            <span>入力 {c.input.toLocaleString()}</span>
            <span className="ok">成功 {c.success.toLocaleString()}</span>
            <span className={c.failed > 0 ? "ng" : ""}>失敗 {c.failed.toLocaleString()}</span>
            <span className="muted">スキップ {c.skipped.toLocaleString()}</span>
          </div>
          <Bar value={c.success} total={c.input} />
        </>
      ) : (
        <div className="muted small">未実行</div>
      )}

      {failures.length > 0 && (
        <div className="failures">
          <div className="section-title">失敗内訳</div>
          {failures.map(([reason, n]) => (
            <div className="failure-row" key={reason}>
              <span className="failure-name">{reason}</span>
              <span className="failure-count">{n}</span>
              <span
                className="failure-bar"
                style={{ width: `${maxFailure ? (n / maxFailure) * 100 : 0}%` }}
              />
            </div>
          ))}
        </div>
      )}

      {phase.warnings.map((w) => (
        <div className="warn" key={w.code} title={w.message ?? ""}>
          ⚠ {w.code} {w.count > 1 ? `${w.count}件` : ""}
          {w.message && <div className="warn-msg">{w.message}</div>}
        </div>
      ))}

      {phase.error && <div className="error">{phase.error}</div>}

      {phase.outputs.map((o) => (
        <div className="output small" key={o.path}>
          → {o.path}
          {o.records != null && ` (${o.records.toLocaleString()} records)`}
        </div>
      ))}

      <div className="card-actions">
        <button onClick={() => onRerun(phase.phase)}>再実行</button>
      </div>
    </div>
  );
}

export function PipelineView({
  runId,
  runs,
  onSelectRun,
  onRerun,
}: {
  runId: string | null;
  runs: string[];
  onSelectRun: (runId: string) => void;
  onRerun: (phase: string) => void;
}) {
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    const load = () =>
      api
        .run(runId)
        .then((r) => alive && setRun(r))
        .catch((e) => alive && setError(String(e)));
    load();
    // 実行中の様子が見えるよう、控えめに追従する
    const timer = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runId]);

  if (!runId) return <p className="muted">run がまだありません。画面2 から実行してください。</p>;
  if (error) return <p className="error">{error}</p>;
  if (!run) return <p className="muted">読み込み中…</p>;

  return (
    <div>
      <div className="toolbar">
        <label>
          run{" "}
          <select value={runId} onChange={(e) => onSelectRun(e.target.value)}>
            {runs.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="pipeline">
        {run.phases.map((p, i) => {
          const prev = i > 0 ? run.phases[i - 1] : null;
          const from = prev?.counts?.success ?? null;
          const to = p.counts?.input ?? null;
          return (
            <div className="pipeline-item" key={p.phase}>
              {i > 0 && (
                <div className="arrow" title="工程間での件数の変化">
                  <span>↓</span>
                  <span className="arrow-counts">
                    {from != null && to != null
                      ? `${from.toLocaleString()} → ${to.toLocaleString()}`
                      : ""}
                  </span>
                </div>
              )}
              <PhaseCard phase={p} onRerun={onRerun} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
