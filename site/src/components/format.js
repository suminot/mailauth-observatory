// 表示の共通処理。
//
// 率の分母は必ず observed_domains にする。total_domains には SERVFAIL 等で
// 何も取れなかったドメインが含まれており、それを分母にすると
// 「取れなかった」が「未対応」として提示されてしまう。

export function rate(numerator, denominator) {
  if (!denominator) return null;
  return numerator / denominator;
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** 3段階の配色。合格＝緑、要改善＝橙、未対応＝グレー寄りの赤。 */
export function band(value) {
  if (value === null || value === undefined) return "unknown";
  if (value >= 0.6) return "pass";
  if (value >= 0.2) return "attention";
  return "absent";
}

export const BAND_COLORS = {
  pass: "var(--pass)",
  attention: "var(--attention)",
  absent: "var(--absent)",
  unknown: "var(--neutral)",
};

/** 標準準拠の事実記述。断定的な語彙を使わない（DESIGN.md P8）。 */
export function describePolicy(policy, hasRua = true) {
  let text;
  if (policy === "reject") text = "p=reject。認証に失敗したメールの拒否を要求している";
  else if (policy === "quarantine") text = "p=quarantine。認証に失敗したメールの隔離を要求している";
  else if (policy === "none") text = "p=none のため spoofing 抑止効果は限定的。観測のみの段階にある";
  else if (policy === null || policy === undefined)
    text = "DMARC レコードを観測できなかった。レコードが無いこととは別";
  else text = `p=${policy}`;
  if ((policy === "reject" || policy === "quarantine") && !hasRua) {
    text += "。ただし rua が無く、何が拒否されているかを運用者が確認できない";
  }
  return text;
}

/** 達成した標準のチェックリスト。総合順位やグレードは付けない。 */
export function checklist(stats) {
  const obs = stats.observed_domains;
  return [
    { label: "SPF レコードを公開している", value: rate(stats.spf_adopted_domains, obs) },
    { label: "DMARC レコードを公開している", value: rate(stats.dmarc_adopted_domains, obs) },
    {
      label: "DMARC が強制ポリシー（quarantine / reject）",
      value: rate(stats.dmarc_enforced_domains, obs),
    },
    {
      label: "reject が実効している（pct 無し・t=n・rua 有）",
      value: rate(stats.enforced_reject_domains, obs),
    },
    { label: "DKIM を既知セレクタで検出できた", value: rate(stats.dkim_detected_domains, obs) },
    { label: "MTA-STS を公開している", value: rate(stats.mta_sts_domains, obs) },
    { label: "TLS-RPT を公開している", value: rate(stats.tls_rpt_domains, obs) },
    { label: "DNSSEC で署名されている", value: rate(stats.dnssec_domains, obs) },
  ];
}

export function checklistClass(value) {
  if (value === null || value === undefined) return "unknown";
  if (value >= 0.6) return "met";
  if (value >= 0.2) return "partial";
  return "unmet";
}
