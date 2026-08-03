// ビュー切り替え ── 同じ計測結果を別の軸で見る。
//
// 「全上場を業種軸で」と「プライムだけ」を、計測し直さずに切り替える。

import { useEffect, useState } from "react";
import { api, type ViewResult, type ViewDef } from "./api";

const AXIS_LABELS: Record<string, string> = {
  common12: "共通12業種",
  industry: "一次分類（EDINET33）",
  segment: "市場区分",
  country: "国",
  status: "状態",
};

export function ViewsPanel({ runId }: { runId: string | null }) {
  const [views, setViews] = useState<ViewDef[]>([]);
  const [axes, setAxes] = useState<string[]>([]);
  const [viewId, setViewId] = useState("jp-all");
  const [by, setBy] = useState("common12");
  const [result, setResult] = useState<ViewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .views()
      .then((r) => {
        setViews(r.views);
        setAxes(r.group_by_options);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setError(null);
    api
      .runView(runId, viewId, by)
      .then(setResult)
      .catch((e) => {
        setResult(null);
        setError(String(e));
      });
  }, [runId, viewId, by]);

  if (!runId) return <p className="muted">run がありません。先に P1 を実行してください。</p>;

  const max = result?.groups.reduce((m, g) => Math.max(m, g.n), 0) ?? 0;

  return (
    <div>
      <div className="toolbar view-toolbar">
        <label>
          ビュー{" "}
          <select value={viewId} onChange={(e) => setViewId(e.target.value)}>
            {views.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
                {v.requires_segment ? "（要 区分データ）" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          軸{" "}
          <select value={by} onChange={(e) => setBy(e.target.value)}>
            {axes.map((a) => (
              <option key={a} value={a}>
                {AXIS_LABELS[a] ?? a}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <>
          {result.warnings.map((w) => (
            <div className="warn" key={w}>
              ⚠ {w}
            </div>
          ))}

          <table className="view-table">
            <thead>
              <tr>
                <th>{AXIS_LABELS[result.group_by] ?? result.group_by}</th>
                <th className="num">社数</th>
                <th className="num">構成比</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {result.groups.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    該当なし
                  </td>
                </tr>
              )}
              {result.groups.map((g) => (
                <tr key={g.code}>
                  <td>{g.label}</td>
                  <td className="num">{g.n.toLocaleString()}</td>
                  <td className="num">{(g.share * 100).toFixed(1)}%</td>
                  <td className="bar-cell">
                    <span
                      className="view-bar"
                      style={{ width: `${max ? (g.n / max) * 100 : 0}%` }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th>合計</th>
                <th className="num">{result.total.toLocaleString()}</th>
                <th colSpan={2} />
              </tr>
            </tfoot>
          </table>

          <div className="muted small">
            run 全体 {result.coverage.entities_in_run.toLocaleString()} 社 / 本ビュー{" "}
            {result.coverage.entities_in_view.toLocaleString()} 社 / 区分付き{" "}
            {result.coverage.with_segment.toLocaleString()} 社 / 業種付き{" "}
            {result.coverage.with_common12.toLocaleString()} 社
          </div>
        </>
      )}
    </div>
  );
}
