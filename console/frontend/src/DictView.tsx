// 画面5 辞書メンテナンス ── 未知ホストを頻度順に見て、その場で辞書に足す。
//
// リサーチでは NRIセキュア、ラック、富士通、NEC、ソフトバンク、大塚商会など
// 多数の国内ベンダーの固定ホスト名を特定できなかった。これらは実測データからの
// 帰納的発見でしか埋まらない。この画面がその発見を辞書に変える唯一の経路である。

import { useCallback, useEffect, useState } from "react";
import {
  api,
  streamJob,
  type DictionaryFile,
  type FingerprintsResult,
  type UnknownHost,
  type UnknownHostsResult,
} from "./api";

const CONFIDENCE = ["high", "medium", "low"];

interface Draft {
  host: UnknownHost;
  file: string;
  id: string;
  vendor: string;
  product: string;
  record: string;
  pattern: string;
  region: string;
  confidence: string;
  note: string;
}

/** 登録ドメインから正規表現の下書きを作る。手直しできるので概算で足りる。 */
function suggestPattern(registeredDomain: string): string {
  return `\\.${registeredDomain.replace(/\./g, "\\.")}\\.?$`;
}

/** 規則 id の下書き。英小文字・数字・ハイフンだけが通る。 */
function suggestId(registeredDomain: string, taken: number): string {
  const stem = registeredDomain
    .split(".")[0]
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${stem || "vendor"}-mx-${String(taken + 1).padStart(2, "0")}`;
}

export function DictView({ runId }: { runId: string | null }) {
  const [dict, setDict] = useState<FingerprintsResult | null>(null);
  const [unknown, setUnknown] = useState<UnknownHostsResult | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rerunLog, setRerunLog] = useState<string[]>([]);
  const [rerunning, setRerunning] = useState(false);

  const refresh = useCallback(() => {
    api.fingerprints().then(setDict).catch((e) => setError(String(e)));
    if (!runId) {
      setUnknown(null);
      return;
    }
    api
      .unknownHosts(runId)
      .then((r) => {
        setUnknown(r);
        setError(null);
      })
      .catch(() => {
        setUnknown(null);
        setError(`${runId} の P6 がまだ実行されていません。画面2 で p6_infer を実行してください。`);
      });
  }, [runId]);

  useEffect(refresh, [refresh]);

  const editable: DictionaryFile[] = (dict?.dictionaries ?? []).filter((d) => d.editable);

  function startDraft(host: UnknownHost) {
    const target =
      editable.find((d) => d.category === "security_gateway") ?? editable[0];
    setNotice(null);
    setDraft({
      host,
      file: target?.file ?? "configs/fingerprints/security_gw.yaml",
      id: suggestId(host.registered_domain, target?.rule_count ?? 0),
      vendor: "",
      product: "",
      record: "MX",
      pattern: suggestPattern(host.registered_domain),
      region: "JP",
      confidence: "high",
      note: `実測から発見（${host.examples[0] ?? host.registered_domain}、${host.count}件）`,
    });
  }

  async function save() {
    if (!draft) return;
    setError(null);
    try {
      const res = await api.addRule({
        file: draft.file,
        id: draft.id,
        vendor: draft.vendor,
        record: draft.record,
        pattern: draft.pattern,
        product: draft.product || null,
        confidence: draft.confidence,
        region: draft.region || null,
        note: draft.note || null,
      });
      setDraft(null);
      setNotice(
        `${res.rule_id} を追記しました（${draft.file} は ${res.rule_count} 件）。` +
          "P6 を再実行すると効果が確認できます。",
      );
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  /** 辞書を足したら、その場で P6 だけ再実行して効果を見る。P4 の再計測は不要。 */
  async function rerunP6() {
    if (!runId) return;
    setRerunLog([]);
    setRerunning(true);
    try {
      const job = await api.createJob({ phases: ["p6_infer"], run_id: runId });
      streamJob(
        job.id,
        (line) => setRerunLog((prev) => [...prev, line]),
        () => {
          setRerunning(false);
          refresh();
        },
      );
    } catch (e) {
      setRerunning(false);
      setError(String(e));
    }
  }

  return (
    <div className="dict">
      <div className="toolbar">
        <button onClick={rerunP6} disabled={!runId || rerunning}>
          {rerunning ? "P6 実行中…" : "P6 を再実行して効果を見る"}
        </button>
        <span className="muted small">
          辞書の更新だけなら P4 の再計測は不要。bronze から作り直せる
        </span>
      </div>

      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <section>
        <h2>未知 MX ホスト（頻度順）</h2>
        <p className="muted small">
          集約は登録ドメイン単位。顧客別ホスト名を1件ずつ数えても辞書を育てる手がかりに
          ならないため。
          {unknown?.domains_with_no_inference != null && (
            <>
              {" "}
              推定が付かないドメイン {unknown.domains_with_no_inference} 件（
              {((unknown.no_inference_rate ?? 0) * 100).toFixed(1)}%、受け入れ基準は 20% 未満）。
            </>
          )}
        </p>
        {!unknown?.hosts.length && (
          <p className="muted">
            未知ホストはありません。辞書がこの run の MX をすべて説明できています。
          </p>
        )}
        {!!unknown?.hosts.length && (
          <table>
            <thead>
              <tr>
                <th>登録ドメイン</th>
                <th className="num">件数</th>
                <th>実例</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {unknown.hosts.map((h) => (
                <tr key={h.registered_domain}>
                  <td>
                    <code>{h.registered_domain}</code>
                  </td>
                  <td className="num">{h.count}</td>
                  <td className="muted small">{h.examples.join(", ")}</td>
                  <td>
                    <button onClick={() => startDraft(h)}>辞書に追加</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {draft && (
        <section className="panel">
          <h2>
            規則を追記 ── <code>{draft.host.registered_domain}</code>
          </h2>
          <div className="form-grid">
            <label>
              追記先
              <select
                value={draft.file}
                onChange={(e) => setDraft({ ...draft, file: e.target.value })}
              >
                {editable.map((d) => (
                  <option key={d.file} value={d.file}>
                    {d.file} （{d.category}, {d.rule_count}件）
                  </option>
                ))}
              </select>
            </label>
            <label>
              規則 id
              <input
                value={draft.id}
                onChange={(e) => setDraft({ ...draft, id: e.target.value })}
              />
            </label>
            <label>
              ベンダー
              <input
                value={draft.vendor}
                placeholder="NRIセキュアテクノロジーズ"
                onChange={(e) => setDraft({ ...draft, vendor: e.target.value })}
              />
            </label>
            <label>
              製品
              <input
                value={draft.product}
                placeholder="任意"
                onChange={(e) => setDraft({ ...draft, product: e.target.value })}
              />
            </label>
            <label>
              レコード種別
              <select
                value={draft.record}
                onChange={(e) => setDraft({ ...draft, record: e.target.value })}
              >
                {(dict?.record_types ?? ["MX"]).map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label>
              確度
              <select
                value={draft.confidence}
                onChange={(e) => setDraft({ ...draft, confidence: e.target.value })}
              >
                {CONFIDENCE.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="wide">
              正規表現
              <input
                value={draft.pattern}
                onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
              />
            </label>
            <label className="wide">
              注記
              <input
                value={draft.note}
                onChange={(e) => setDraft({ ...draft, note: e.target.value })}
              />
            </label>
          </div>
          <div className="toolbar">
            <button onClick={save} disabled={!draft.vendor || !draft.pattern}>
              追記する
            </button>
            <button onClick={() => setDraft(null)}>やめる</button>
            <span className="muted small">
              追記後に辞書を読み直して検証し、通らなければ元に戻します
            </span>
          </div>
        </section>
      )}

      {!!rerunLog.length && (
        <section>
          <h2>P6 の実行ログ</h2>
          <pre className="log">{rerunLog.join("\n")}</pre>
        </section>
      )}

      <section>
        <h2>辞書の現況</h2>
        <p className="muted small">
          全 {dict?.total_rules ?? 0} 規則 / 版 <code>{dict?.version ?? "-"}</code>
        </p>
        <table>
          <thead>
            <tr>
              <th>ファイル</th>
              <th>カテゴリ</th>
              <th className="num">規則</th>
              <th className="num">うち国内</th>
              <th>編集</th>
            </tr>
          </thead>
          <tbody>
            {(dict?.dictionaries ?? []).map((d) => (
              <tr key={d.file}>
                <td>
                  <code>{d.file}</code>
                </td>
                <td>{d.category}</td>
                <td className="num">{d.rule_count}</td>
                <td className="num">{d.jp_rule_count}</td>
                <td className="muted small">{d.editable ? "可" : "読み取りのみ"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>DNS では検出できない製品</h2>
        <p className="muted small">
          MX を変更せず API / OAuth で連携するため、原理的に痕跡を残しません。
          <strong>「検出されなかった＝使っていない」ではない</strong>ことを、
          集計とレポートの両方で明示します。
        </p>
        <ul className="plain">
          {(dict?.undetectable_by_dns ?? []).map((u) => (
            <li key={u.vendor}>
              {u.vendor}
              {u.product ? ` — ${u.product}` : ""}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
