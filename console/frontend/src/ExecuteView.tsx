// 画面2 ── フェーズ実行（DESIGN.md 7.2）

import { useEffect, useRef, useState } from "react";
import { api, streamJob, type Job, type Population } from "./api";

const PHASES = [
  { id: "p1_population", label: "P1 母集団確定" },
  { id: "p2_candidates", label: "P2 ドメイン候補生成" },
  { id: "p3_domains", label: "P3 メールドメイン確定" },
  { id: "p4_measure", label: "P4 DNS計測" },
  { id: "p5_parse", label: "P5 パース" },
  { id: "p6_infer", label: "P6 推察" },
  { id: "p7_aggregate", label: "P7 集計" },
  { id: "p8_publish", label: "P8 公開" },
];

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function ExecuteView({ onJobFinished }: { onJobFinished: () => void }) {
  const [populations, setPopulations] = useState<Population[]>([]);
  const [populationId, setPopulationId] = useState<string>("");
  const [runId, setRunId] = useState(currentMonth());
  const [selected, setSelected] = useState<string[]>(["p1_population"]);
  const [limit, setLimit] = useState<string>("");
  const [dryRun, setDryRun] = useState(false);
  const [sourceFile, setSourceFile] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    api
      .populations()
      .then((r) => {
        setPopulations(r.populations);
        const first = r.populations.find((p) => p.implemented && p.enabled);
        if (first) setPopulationId(first.id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  const population = populations.find((p) => p.id === populationId);

  function togglePhase(id: string) {
    setSelected((cur) =>
      cur.includes(id) ? cur.filter((p) => p !== id) : [...cur, id],
    );
  }

  function selectFrom(id: string) {
    const idx = PHASES.findIndex((p) => p.id === id);
    setSelected(PHASES.slice(idx).map((p) => p.id));
  }

  async function submit() {
    setError(null);
    setLines([]);
    try {
      const created = await api.createJob({
        phases: selected,
        run_id: runId,
        config: population?.path,
        limit: limit ? Number(limit) : null,
        dry_run: dryRun,
        source_file: sourceFile || null,
      });
      setJob(created);
      streamJob(
        created.id,
        (line) => setLines((cur) => [...cur, line]),
        (status) => {
          setJob((j) => (j ? { ...j, status: status.split(":")[0] as Job["status"] } : j));
          onJobFinished();
        },
      );
    } catch (e) {
      setError(String(e));
    }
  }

  const canRun = selected.length > 0 && !!runId && job?.status !== "running";

  return (
    <div className="execute">
      <div className="form">
        <label>
          母集団
          <select value={populationId} onChange={(e) => setPopulationId(e.target.value)}>
            {populations.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.implemented || !p.enabled}>
                {p.id} — {p.label}
                {p.implemented && p.enabled ? "" : "（未実装）"}
              </option>
            ))}
          </select>
        </label>
        {population && !population.implemented && (
          <div className="warn">
            ⚠ この母集団は未実装です{population.blocked_by ? `: ${population.blocked_by}` : ""}
          </div>
        )}

        <label>
          run ID
          <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="2026-08" />
        </label>

        <fieldset>
          <legend>実行するフェーズ</legend>
          {PHASES.map((p) => (
            <div className="phase-row" key={p.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(p.id)}
                  onChange={() => togglePhase(p.id)}
                />
                {p.label}
              </label>
              <button className="link" onClick={() => selectFrom(p.id)}>
                ここから連続
              </button>
            </div>
          ))}
        </fieldset>

        <label>
          件数制限（--limit）
          <input
            value={limit}
            onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))}
            placeholder="開発中の高速反復用。空欄で全件"
          />
        </label>

        <label>
          ソースファイル（--source-file）
          <input
            value={sourceFile}
            onChange={(e) => setSourceFile(e.target.value)}
            placeholder="指定するとネットワークに出ない。省略可"
          />
        </label>

        <label className="inline">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          ドライラン（出力を書かない）
        </label>

        <div className="actions">
          <button disabled={!canRun} onClick={submit}>
            実行
          </button>
          {job?.status === "running" && (
            <button
              onClick={() => api.stopJob(job.id).then((j) => setJob(j))}
              className="secondary"
            >
              停止
            </button>
          )}
        </div>

        {error && <div className="error">{error}</div>}
      </div>

      <div className="log-pane">
        <div className="log-head">
          実行ログ
          {job && (
            <span className={`badge badge-${job.status}`}>
              #{job.id} {job.status}
            </span>
          )}
        </div>
        <pre ref={logRef} className="log">
          {lines.length ? lines.join("\n") : "（まだ実行していません）"}
        </pre>
      </div>
    </div>
  );
}
