// 画面3 レコード検査 ── 1ドメインを bronze から gold まで縦に並べる。
//
// 「なぜこの判定になったか」を追跡する主要な手段（DESIGN.md 7.2）。
// 原則2 をそのまま画面の構造にしている。bronze（生応答）と fact（解釈）は
// 事実、inference は推定として枠を分ける。混ぜて見せると、どこまでが
// DNS から読めたことなのかが分からなくなる。

import { useState } from "react";
import { api, type TraceEvidence, type TraceResult } from "./api";

/** bronze は加工しない。整形もしない。見えているものが保存されているものと同じ。 */
function Raw({ value }: { value: unknown }) {
  return <pre className="raw">{JSON.stringify(value, null, 1)}</pre>;
}

function Fields({
  data,
  keys,
}: {
  data: Record<string, unknown> | null;
  keys: string[];
}) {
  if (!data) return <p className="muted">記録がありません。</p>;
  return (
    <table className="trace-table">
      <tbody>
        {keys
          .filter((k) => k in data)
          .map((k) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{render(data[k])}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

const ENTITY_KEYS = [
  "entity_id",
  "name",
  "country",
  "population_ids",
  "common12_code",
  "common12_label",
  "official_domain",
  "status",
];

const DOMAIN_KEYS = [
  "domain_id",
  "domain",
  "domain_role",
  "confidence",
  "measure_tier",
  "is_measured",
  "evidence_count",
  "evidence",
  "note",
];

// 事実として見せる列。生値も含める（silver でも生を捨てない）
const FACT_KEYS = [
  "observed",
  "record_present",
  "raw_mx",
  "mx_present",
  "mx_null",
  "mx_hosts",
  "raw_spf",
  "spf_present",
  "spf_valid",
  "spf_error",
  "spf_all_qualifier",
  "spf_lookup_count",
  "spf_exceeds_limit",
  "spf_includes",
  "spf_mechanisms",
  "raw_dmarc",
  "dmarc_present",
  "dmarc_p",
  "dmarc_sp",
  "dmarc_pct",
  "dmarc_t",
  "effective_7489",
  "effective_9989",
  "policy_label",
  "blind_enforcement",
  "org_domain_psl",
  "org_domain_treewalk",
  "org_domain_divergence",
  "rua_external",
  "rua_authorized",
  "rua_domain_unregistered",
  "dkim_status",
  "dkim_selectors",
  "dkim_key_bits",
  "dkim_cname_targets",
  "dkim_wildcard_suspect",
  "dkim_wildcard_revoked",
  "mta_sts_present",
  "tls_rpt_present",
  "bimi_present",
  "bimi_has_vmc",
  "dnssec_signed",
  "dane_present",
  "dane_orphan",
  "spec_version",
  "parser_version",
];

function evidenceList(evidence: TraceInferenceEvidence): TraceEvidence[] {
  if (!evidence) return [];
  if (typeof evidence === "string") {
    try {
      return JSON.parse(evidence) as TraceEvidence[];
    } catch {
      return [];
    }
  }
  return evidence;
}

type TraceInferenceEvidence = TraceEvidence[] | string | null;

export function TraceView({ runId }: { runId: string | null }) {
  const [domain, setDomain] = useState("");
  const [result, setResult] = useState<TraceResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showRaw, setShowRaw] = useState<string | null>(null);

  async function search() {
    if (!runId || !domain.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await api.trace(runId, domain.trim()));
    } catch (e) {
      setResult(null);
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  if (!runId) {
    return <p className="muted">run がありません。先に P1 を実行してください。</p>;
  }

  return (
    <div className="trace">
      <div className="toolbar">
        <input
          value={domain}
          placeholder="example.co.jp"
          onChange={(e) => setDomain(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          className="domain-input"
        />
        <button onClick={search} disabled={loading || !domain.trim()}>
          {loading ? "検索中…" : "追跡する"}
        </button>
        <span className="muted small">
          bronze の生応答から gold への寄与までを縦に並べます
        </span>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <>
          <section>
            <h2>1. 母集団（P1）</h2>
            <Fields data={result.entity} keys={ENTITY_KEYS} />
          </section>

          <section>
            <h2>2. 候補として拾われた経路（P2）</h2>
            {!result.candidates.length ? (
              <p className="muted">候補の記録がありません。</p>
            ) : (
              <table className="trace-table">
                <thead>
                  <tr>
                    <th>発見方法</th>
                    <th>ソース</th>
                    <th>備考</th>
                  </tr>
                </thead>
                <tbody>
                  {result.candidates.map((c, i) => (
                    <tr key={i}>
                      <td>{render(c.discovery_method)}</td>
                      <td className="small">{render(c.source)}</td>
                      <td className="small muted">{render(c.note)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section>
            <h2>3. メールドメインの確定（P3）</h2>
            <Fields data={result.domain_row} keys={DOMAIN_KEYS} />
          </section>

          <section className="observed">
            <h2>4. DNS 計測（P4）── 生の応答</h2>
            <p className="muted small">
              加工していません。ここに保存されているものがそのまま表示されています。
              観測できなかったクエリ {result.bronze.not_observed} 件は「レコードが無い」
              ことを意味しません。
              {result.bronze.truncated && " 件数が多いため一部のみ表示しています。"}
            </p>
            <table className="trace-table">
              <thead>
                <tr>
                  <th>purpose</th>
                  <th className="num">クエリ</th>
                  <th className="num">観測できた</th>
                  <th className="num">レコードあり</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.bronze.by_purpose).map(([purpose, records]) => (
                  <tr key={purpose}>
                    <td>
                      <code>{purpose}</code>
                    </td>
                    <td className="num">{records.length}</td>
                    <td className="num">{records.filter((r) => r.observed).length}</td>
                    <td className="num">
                      {records.filter((r) => r.record_present).length}
                    </td>
                    <td>
                      <button
                        className="link"
                        onClick={() => setShowRaw(showRaw === purpose ? null : purpose)}
                      >
                        {showRaw === purpose ? "閉じる" : "生 JSON"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {showRaw && <Raw value={result.bronze.by_purpose[showRaw]} />}
          </section>

          <section className="observed">
            <h2>5. パース（P5）── 仕様に照らした解釈</h2>
            <Fields data={result.fact} keys={FACT_KEYS} />
          </section>

          <section className="inferred">
            <h2>6. 推察（P6）── 辞書との照合</h2>
            {!result.inferences.length ? (
              <p className="muted">推定が付いていません。辞書に無いパターンです。</p>
            ) : (
              result.inferences.map((inf, i) => (
                <div key={i} className="inference-card">
                  <div className="inference-head">
                    <span className="cat">{inf.category}</span>
                    <strong>{inf.vendor}</strong>
                    {inf.product && <span className="muted">{inf.product}</span>}
                    <span className={`conf conf-${inf.confidence}`}>{inf.confidence}</span>
                    {inf.is_stale && <span className="stale">過去の痕跡</span>}
                    {inf.stale_streak_months ? (
                      <span className="muted small">
                        裏付けなし {inf.stale_streak_months} か月目
                      </span>
                    ) : null}
                    {inf.undetectable_reason && (
                      <span className="muted small">{inf.undetectable_reason}</span>
                    )}
                  </div>
                  {!!evidenceList(inf.evidence).length && (
                    <table className="trace-table evidence">
                      <thead>
                        <tr>
                          <th>証拠の種別</th>
                          <th>一致した値</th>
                          <th>規則</th>
                        </tr>
                      </thead>
                      <tbody>
                        {evidenceList(inf.evidence).map((e, j) => (
                          <tr key={j}>
                            <td>{e.record_type}</td>
                            <td className="small">
                              <code>{e.matched_value}</code>
                            </td>
                            <td className="small">
                              <code>{e.rule_id}</code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {inf.note && <p className="muted small">{inf.note}</p>}
                  {inf.park_class && (
                    <p className="small">
                      パーク分類: <code>{inf.park_class}</code>
                    </p>
                  )}
                </div>
              ))
            )}
          </section>

          <section>
            <h2>7. 集計への寄与（P7）</h2>
            <p className="muted small">
              {result.gold.note ?? result.gold.reason}
            </p>
            {!result.gold.cells.length ? (
              <p className="muted">集計に寄与していません。</p>
            ) : (
              <table className="trace-table">
                <thead>
                  <tr>
                    <th>母集団</th>
                    <th>業種セル</th>
                    <th className="num">セルの企業数</th>
                    <th>備考</th>
                  </tr>
                </thead>
                <tbody>
                  {result.gold.cells.map((c, i) => (
                    <tr key={i} className={c.suppressed ? "caution" : ""}>
                      <td>{c.population_id}</td>
                      <td>
                        {c.common12_label ?? c.common12_code ?? "—"}
                      </td>
                      <td className="num">{c.n_entities ?? "—"}</td>
                      <td className="small muted">{c.note ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
