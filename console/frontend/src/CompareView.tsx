// 画面4 手法比較 ── 複数バックエンド・複数リゾルバの観測差分。
//
// 分類の意味を取り違えさせないことが、この画面の要件である。
// 「両方が観測できたのに食い違う」は手法の問題だが、
// 「片方が引けなかった」はその時たまたま引けなかっただけのことが多い。
// 前者だけを赤く見せ、後者は注意の色に留める。

import { useEffect, useState } from "react";
import { api, type CompareResult, type CompareSample } from "./api";

/** 重い差分から順に並べる。 */
const KIND_ORDER = [
  "presence_differs",
  "values_differ",
  "only_in",
  "observation_differs",
  "agree",
];

function kindClass(kind: string): string {
  if (kind === "presence_differs" || kind === "values_differ") return "kind-bad";
  if (kind === "agree") return "kind-ok";
  return "kind-warn";
}

function SampleTable({ samples }: { samples: CompareSample[] }) {
  return (
    <table className="compare-table">
      <thead>
        <tr>
          <th>ドメイン</th>
          <th>クエリ</th>
          <th>purpose</th>
          <th>内容</th>
        </tr>
      </thead>
      <tbody>
        {samples.map((s, i) => (
          <tr key={i}>
            <td>
              <code>{s.domain}</code>
            </td>
            <td className="small">
              <code>
                {s.query_name} {s.query_type}
              </code>
            </td>
            <td className="small">{s.purpose}</td>
            <td className="small">
              <code>{JSON.stringify(s.detail)}</code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CompareView({ runId }: { runId: string | null }) {
  const [data, setData] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    setError(null);
    api
      .compare(runId)
      .then(setData)
      .catch(() => {
        setData(null);
        setError(
          `${runId} の比較結果がありません。画面2 で p4_measure を別の手法でも実行し、` +
            "mailauth p4-compare を走らせてください。",
        );
      });
  }, [runId]);

  if (!runId) return <p className="muted">run がありません。</p>;

  const notCompared = (data?.bronze_methods ?? []).filter(
    (m) => !(data?.methods ?? []).includes(m),
  );

  return (
    <div className="compare">
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <section>
            <h2>比べた手法</h2>
            <p className="muted small">
              基準は <code>{data.methods[0]}</code>。他の手法をこれと突き合わせています。
              {!!notCompared.length && (
                <> bronze にはあるが比較していない手法: {notCompared.join(", ")}。</>
              )}
            </p>
            <table className="compare-table">
              <thead>
                <tr>
                  <th>手法</th>
                  <th className="num">記録</th>
                  <th className="num">観測できた</th>
                  <th className="num">レコードあり</th>
                  <th>rcode</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.stats).map(([method, s]) => (
                  <tr key={method}>
                    <td>
                      <code>{method}</code>
                    </td>
                    <td className="num">{s.records}</td>
                    <td className="num">{s.observed}</td>
                    <td className="num">{s.record_present}</td>
                    <td className="small muted">
                      {Object.entries(s.by_rcode)
                        .map(([k, v]) => `${k}:${v}`)
                        .join(" ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h2>差分</h2>
            <p className="muted small">
              共通の purpose で {data.compared} クエリを突き合わせ、一致率は{" "}
              {data.agreement_rate === null
                ? "—"
                : `${(data.agreement_rate * 100).toFixed(1)}%`}
              。
            </p>
            <table className="compare-table">
              <tbody>
                {KIND_ORDER.filter((k) => data.counts[k]).map((kind) => (
                  <tr key={kind} className={kindClass(kind)}>
                    <td>{data.kind_labels[kind] ?? kind}</td>
                    <td className="num">{data.counts[kind]}</td>
                    <td>
                      {data.samples[kind] && (
                        <button
                          className="link"
                          onClick={() => setOpen(open === kind ? null : kind)}
                        >
                          {open === kind ? "閉じる" : "例を見る"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {open && data.samples[open] && <SampleTable samples={data.samples[open]} />}
          </section>

          {!!Object.keys(data.plan_differs).length && (
            <section>
              <h2>引いている purpose が違う組み合わせ</h2>
              <p className="muted small">
                クロスチェックは MX / SPF / DMARC しか引きません。個別の差分としては
                数えず、共通の purpose だけを突き合わせています。
              </p>
              <table className="compare-table">
                <tbody>
                  {Object.entries(data.plan_differs).map(([pair, only]) => (
                    <tr key={pair}>
                      <td className="small">
                        <code>{pair}</code>
                      </td>
                      <td className="small muted">
                        {Object.entries(only)
                          .map(([m, ps]) => `${m} のみ: ${ps.join(", ")}`)
                          .join(" / ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {!!data.notes.length && (
            <section>
              <h2>注記</h2>
              <ul className="plain muted small">
                {data.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
