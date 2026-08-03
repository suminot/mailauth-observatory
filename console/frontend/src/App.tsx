import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { DictView } from "./DictView";
import { ExecuteView } from "./ExecuteView";
import { PipelineView } from "./PipelineView";
import { ViewsPanel } from "./ViewsPanel";

type Tab = "pipeline" | "execute" | "views" | "dict";

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [runs, setRuns] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [version, setVersion] = useState<string>("");
  const [dataRoot, setDataRoot] = useState<string>("");

  const refreshRuns = useCallback(() => {
    api.runs().then((r) => {
      setRuns(r.runs);
      setRunId((cur) => cur ?? r.runs[0] ?? null);
    });
  }, []);

  useEffect(() => {
    refreshRuns();
    api.health().then((h) => {
      setVersion(h.version);
      setDataRoot(h.data_root);
    });
  }, [refreshRuns]);

  return (
    <div className="app">
      <header>
        <h1>mailauth-observatory 運用コンソール</h1>
        <div className="muted small">
          v{version} / {dataRoot} / ローカル専用・認証なし
        </div>
        <nav>
          <button className={tab === "pipeline" ? "active" : ""} onClick={() => setTab("pipeline")}>
            画面1 パイプライン全景
          </button>
          <button className={tab === "execute" ? "active" : ""} onClick={() => setTab("execute")}>
            画面2 フェーズ実行
          </button>
          <button className={tab === "dict" ? "active" : ""} onClick={() => setTab("dict")}>
            画面5 辞書メンテナンス
          </button>
          <button className={tab === "views" ? "active" : ""} onClick={() => setTab("views")}>
            ビュー切り替え
          </button>
        </nav>
      </header>

      <main>
        {tab === "pipeline" && (
          <PipelineView
            runId={runId}
            runs={runs}
            onSelectRun={setRunId}
            onRerun={() => setTab("execute")}
          />
        )}
        {tab === "execute" && (
          <ExecuteView
            onJobFinished={() => {
              refreshRuns();
            }}
          />
        )}
        {tab === "dict" && <DictView runId={runId} />}
        {tab === "views" && <ViewsPanel runId={runId} />}
      </main>

      <footer className="muted small">
        画面3（レコード検査）は Sprint 2、画面4（手法比較）は Sprint 6、
        画面6（月次差分）は Sprint 6 で実装予定。
      </footer>
    </div>
  );
}
