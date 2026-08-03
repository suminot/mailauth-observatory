// 画面6 月次差分 ── P7 が書いた gold をそのまま見る。
//
// 表示は P7 の出力そのまま。ここで計算し直すと、コンソールの数字と公開サイトの
// 数字が食い違って信用できなくなる。
//
// 「消えた」と「取れなかった」は必ず隣に並べる。domains_disappeared だけを見せると
// 運用者が「先月あったのに消えた」と読んでしまう。

import { useEffect, useState } from "react";
import { api, type GoldMonth, type GoldSector, type GoldStats } from "./api";

const STAGE_LABELS: Record<string, string> = {
  "0": "Stage 0 SPFのみ / 無し",
  "1": "Stage 1 SPF+DKIM+p=none",
  "2": "Stage 2 強制ポリシー",
  "3": "Stage 3 +MTA-STS/TLS-RPT+DNSSEC",
  "4": "Stage 4 +BIMI(VMC) / DANE",
};

/** 率は必ず observed_domains を分母にする。total_domains には未観測が混ざる。 */
function rate(numerator: number, denominator: number): string {
  if (!denominator) return "—";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

function Diff({ value }: { value: number | undefined }) {
  if (value === undefined || value === 0) return <span className="muted">±0</span>;
  return (
    <span className={value > 0 ? "up" : "down"}>
      {value > 0 ? "+" : ""}
      {value}
    </span>
  );
}

function OverallCard({ stats }: { stats: GoldStats }) {
  const obs = stats.observed_domains;
  const diff = stats.diff_prev_month;
  const delta = stats.delta_prev_month;
  const dist = stats.maturity_stage_dist ?? {};
  const distTotal = Object.values(dist).reduce((a, b) => a + b, 0);

  return (
    <section className="panel">
      <h2>{stats.population_id}</h2>
      <p className="muted small">
        企業 {stats.total_entities} 社 / ドメイン {stats.total_domains} 件（うち観測できた
        ものは {obs} 件）。
        <strong>率の分母は観測できたドメインのみ。</strong>取れなかったドメインを分母に
        入れると「取れなかった」が「未対応」に化ける。
      </p>

      <table className="gold-table">
        <thead>
          <tr>
            <th>指標</th>
            <th className="num">ドメイン</th>
            <th className="num">率</th>
            <th className="num">企業</th>
            <th className="num">前月比</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>SPF</td>
            <td className="num">{stats.spf_adopted_domains}</td>
            <td className="num">{rate(stats.spf_adopted_domains, obs)}</td>
            <td className="num">{stats.spf_adopted_entities}</td>
            <td className="num">
              <Diff value={diff?.spf_adopted_domains} />
            </td>
          </tr>
          <tr>
            <td>DMARC</td>
            <td className="num">{stats.dmarc_adopted_domains}</td>
            <td className="num">{rate(stats.dmarc_adopted_domains, obs)}</td>
            <td className="num">{stats.dmarc_adopted_entities}</td>
            <td className="num">
              <Diff value={diff?.dmarc_adopted_domains} />
            </td>
          </tr>
          <tr>
            <td>DMARC 強制（quarantine / reject）</td>
            <td className="num">{stats.dmarc_enforced_domains}</td>
            <td className="num">{rate(stats.dmarc_enforced_domains, obs)}</td>
            <td className="num">{stats.dmarc_enforced_entities}</td>
            <td className="num">
              <Diff value={diff?.dmarc_enforced_domains} />
            </td>
          </tr>
          <tr>
            <td>DKIM（既知セレクタで検出）</td>
            <td className="num">{stats.dkim_detected_domains}</td>
            <td className="num">{rate(stats.dkim_detected_domains, obs)}</td>
            <td className="num">—</td>
            <td className="num" />
          </tr>
        </tbody>
      </table>

      <h3>名目と実効</h3>
      <p className="muted small">
        <code>p=reject</code> と書いてあることと、それが効いていることは別の事実。
      </p>
      <table className="gold-table">
        <tbody>
          <tr>
            <td>名目 reject（そう書いてある）</td>
            <td className="num">{stats.nominal_reject_domains}</td>
          </tr>
          <tr>
            <td>実効 reject（pct 無し・t=n・rua 有）</td>
            <td className="num">{stats.enforced_reject_domains}</td>
            <td className="num">
              <Diff value={diff?.enforced_reject_domains} />
            </td>
          </tr>
          <tr>
            <td>blind reject（rua が無く何が落ちているか見えない）</td>
            <td className="num">{stats.blind_reject_domains}</td>
          </tr>
        </tbody>
      </table>

      <h3>成熟度ステージ</h3>
      <table className="gold-table">
        <tbody>
          {Object.keys(STAGE_LABELS).map((k) => (
            <tr key={k}>
              <td>{STAGE_LABELS[k]}</td>
              <td className="num">{dist[k] ?? 0}</td>
              <td className="bar-cell">
                <span
                  className="view-bar"
                  style={{ width: `${distTotal ? ((dist[k] ?? 0) / distTotal) * 100 : 0}%` }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>パークドメイン</h3>
      <p className="muted small">
        送信実績がなく監視もされていないドメインは、なりすましの出発点になる。
        送信ドメインの強制率と、非送信ドメインの防御率を分けて示す。
      </p>
      <table className="gold-table">
        <tbody>
          <tr>
            <td>送信ドメイン（active_sending）</td>
            <td className="num">{stats.sending_domains}</td>
            <td className="num">
              強制率 {rate(stats.sending_enforced, stats.sending_domains)}
            </td>
          </tr>
          <tr>
            <td>非送信ドメイン合計</td>
            <td className="num">{stats.parked_domains}</td>
            <td className="num">
              防御率{" "}
              {stats.park_defense_rate === null
                ? "—（分母なし）"
                : `${(stats.park_defense_rate * 100).toFixed(1)}%`}
            </td>
          </tr>
          <tr>
            <td>　hardened（Null MX + -all + p=reject）</td>
            <td className="num">{stats.parked_hardened}</td>
            <td className="num">
              <Diff value={diff?.parked_hardened} />
            </td>
          </tr>
          <tr>
            <td>　defended</td>
            <td className="num">{stats.parked_defended}</td>
            <td className="num" />
          </tr>
          <tr>
            <td>　intentional_no_send</td>
            <td className="num">{stats.parked_intentional}</td>
            <td className="num" />
          </tr>
          <tr>
            <td>　neglected（放置）</td>
            <td className="num">{stats.parked_neglected}</td>
            <td className="num">
              <Diff value={diff?.parked_neglected} />
            </td>
          </tr>
        </tbody>
      </table>

      <h3>前月差分</h3>
      {delta?.skipped ? (
        <p className="muted small">{delta.notes.join(" / ") || "前月の出力が無い"}</p>
      ) : (
        <>
          <table className="gold-table">
            <tbody>
              <tr>
                <td>ポリシー強化</td>
                <td className="num">{delta?.policy_upgraded ?? 0}</td>
              </tr>
              <tr>
                <td>ポリシー後退</td>
                <td className="num">{delta?.policy_downgraded ?? 0}</td>
              </tr>
              <tr>
                <td>新規ドメイン</td>
                <td className="num">{delta?.domains_new ?? 0}</td>
              </tr>
              <tr>
                <td>消滅（2連続観測で不在）</td>
                <td className="num">{delta?.domains_disappeared ?? 0}</td>
              </tr>
              <tr className="caution">
                <td>今月観測できず判定を保留 ── 消滅ではない</td>
                <td className="num">{delta?.domains_unobserved_this_month ?? 0}</td>
              </tr>
              <tr>
                <td>企業の新規 / 除外</td>
                <td className="num">
                  +{delta?.entities_new ?? 0} / −{delta?.entities_removed ?? 0}
                </td>
              </tr>
            </tbody>
          </table>
          {!!delta?.notes.length && (
            <p className="muted small">{delta.notes.join(" / ")}</p>
          )}
        </>
      )}
    </section>
  );
}

function SectorTable({ sectors }: { sectors: GoldSector[] }) {
  if (!sectors.length) return <p className="muted">業種別集計がありません。</p>;
  return (
    <table className="gold-table">
      <thead>
        <tr>
          <th>共通12業種</th>
          <th className="num">企業</th>
          <th className="num">観測ドメイン</th>
          <th className="num">DMARC</th>
          <th className="num">強制</th>
          <th className="num">実効 reject</th>
        </tr>
      </thead>
      <tbody>
        {sectors.map((s) => (
          <tr key={`${s.population_id}:${s.common12_code}`} className={s.suppressed ? "caution" : ""}>
            <td>
              {s.common12_label}
              {s.suppressed && <span className="muted small"> （n&lt;5 を束ねた）</span>}
            </td>
            <td className="num">{s.n_entities}</td>
            <td className="num">{s.observed_domains}</td>
            <td className="num">{rate(s.dmarc_adopted_domains, s.observed_domains)}</td>
            <td className="num">{rate(s.dmarc_enforced_domains, s.observed_domains)}</td>
            <td className="num">{rate(s.enforced_reject_domains, s.observed_domains)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function GoldView() {
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState<string | null>(null);
  const [data, setData] = useState<GoldMonth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .goldMonths()
      .then((r) => {
        setMonths(r.months);
        setMonth((cur) => cur ?? r.months[0] ?? null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!month) return;
    setError(null);
    api
      .goldMonth(month)
      .then(setData)
      .catch((e) => {
        setData(null);
        setError(String(e));
      });
  }, [month]);

  if (!months.length) {
    return (
      <p className="muted">
        gold がまだありません。画面2 で p7_aggregate を実行してください。
      </p>
    );
  }

  return (
    <div className="gold">
      <div className="toolbar">
        <label>
          月{" "}
          <select value={month ?? ""} onChange={(e) => setMonth(e.target.value)}>
            {months.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          表示は P7 の出力そのまま。コンソールで再計算していない
        </span>
      </div>

      {error && <p className="error">{error}</p>}

      {data?.overall.map((stats) => (
        <OverallCard key={stats.population_id} stats={stats} />
      ))}

      <section>
        <h2>業種別（共通12分類）</h2>
        <p className="muted small">
          n&lt;5 のセルは「その他（秘匿）」に束ねている。秘匿セルが1つだけになると合計から
          逆算できるため、その場合は次に小さいセルも巻き込む。
          {!!data?.suppressed_sectors.length && " このほか秘匿したセルがある。"}
        </p>
        <SectorTable sectors={data?.by_sector ?? []} />
      </section>
    </div>
  );
}
