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

// 表示の語。**英語版でも同じ規約に従う**（断定しない、総合評価に見せない）。
// 列の見出しまで日本語のままだと、英語ページで表だけ読めない。
const L = {
  ja: {
    spf: "SPF レコードを公開している",
    dmarc: "DMARC レコードを公開している",
    enforced: "DMARC が強制ポリシー（quarantine / reject）",
    effective: "reject が実効している（pct 無し・t=n・rua 有）",
    dkim: "DKIM を既知セレクタで検出できた",
    mtasts: "MTA-STS を公開している",
    tlsrpt: "TLS-RPT を公開している",
    dnssec: "DNSSEC で署名されている",
    colIndicator: "指標", colDomains: "ドメイン", colRate: "割合",
    colNote: "補足", colEntities: "企業",
    rSpf: "SPF を公開している",
    rDkim: "DKIM を既知セレクタで検出できた",
    rDkimNote: "未検出は未設定の証明ではない",
    rDkimOther: (n) => `未検出は未設定の証明ではない（未検出のうち ${n.toLocaleString()} ドメインは _domainkey の配下に名前があり、試していないセレクタの鍵がありそう）`,
    rDkim2048: "うち DKIM の鍵がすべて 2048 ビット以上",
    rDkim2048Note: (n1024, nShort) =>
      `見つかった RSA 鍵のうち最も短いもので分けた。1024 ビットは ${n1024}、1024 ビット未満は ${nShort}`,
    rDmarc: "DMARC を公開している",
    rDmarcInvalid: (n) => `うち ${n} はレコードが無効（2つある・p= が無いか不正）`,
    rEnforced: "DMARC が強制ポリシー", rEnforcedNote: "quarantine または reject",
    rEffective: "reject が実効している", rEffectiveNote: "pct 無し・t=n・rua 有",
    rMtaSts: "MTA-STS を公開している",
    rMtaStsNote: "TXT の有無。強制しているとは限らない",
    rMtaStsEnforce: "MTA-STS のポリシーが enforce",
    rMtaStsEnforceNote: "ポリシーを取得して確認。testing・取得できないものを除く",
    rTlsRpt: "TLS-RPT を公開している",
    rBimi: "BIMI を公開している",
    rBimiNote: (n) => `うち ${n} は証明書（a=）の場所を書いている。証明書が有効かは見ていない`,
    rDnssec: "DNSSEC で署名されている",
    rDane: "DANE（TLSA）がある", rDaneNote: "うち DNSSEC 未確認は別掲",
    eSpf: "SPF を公開している企業",
    eDmarc: "DMARC を公開している企業",
    eEnforced: "DMARC が強制ポリシーの企業",
  },
  en: {
    spf: "Publishes an SPF record",
    dmarc: "Publishes a DMARC record",
    enforced: "DMARC policy is enforcing (quarantine / reject)",
    effective: "reject is in effect (no pct, t=n, rua present)",
    dkim: "DKIM found among known selectors",
    mtasts: "Publishes MTA-STS",
    tlsrpt: "Publishes TLS-RPT",
    dnssec: "Signed with DNSSEC",
    colIndicator: "Indicator", colDomains: "Domains", colRate: "Share",
    colNote: "Note", colEntities: "Companies",
    rSpf: "Publishes SPF",
    rDkim: "DKIM found among known selectors",
    rDkimNote: "not finding it is not proof it is unset",
    rDkimOther: (n) => `not finding it is not proof it is unset (of those not found, ${n.toLocaleString()} domains have names under _domainkey, likely keys under selectors not tried)`,
    rDkim2048: "of which every DKIM key is 2048 bits or longer",
    rDkim2048Note: (n1024, nShort) =>
      `by the shortest RSA key found: ${n1024} at 1024 bits, ${nShort} under 1024 bits`,
    rDmarc: "Publishes DMARC",
    rDmarcInvalid: (n) => `${n} of these have an invalid record (two records, or p= missing or invalid)`,
    rEnforced: "DMARC policy is enforcing", rEnforcedNote: "quarantine or reject",
    rEffective: "reject is in effect", rEffectiveNote: "no pct, t=n, rua present",
    rMtaSts: "Publishes MTA-STS",
    rMtaStsNote: "the TXT record only; not necessarily enforcing",
    rMtaStsEnforce: "MTA-STS policy is enforce",
    rMtaStsEnforceNote: "policy fetched and read; excludes testing and unreadable policies",
    rTlsRpt: "Publishes TLS-RPT",
    rBimi: "Publishes BIMI",
    rBimiNote: (n) => `${n} of these give a certificate location (a=); whether it is valid is not checked`,
    rDnssec: "Signed with DNSSEC",
    rDane: "Has DANE (TLSA)", rDaneNote: "those without confirmed DNSSEC are listed separately",
    eSpf: "Companies publishing SPF",
    eDmarc: "Companies publishing DMARC",
    eEnforced: "Companies with an enforcing DMARC policy",
  },
};

/** 達成した標準のチェックリスト。総合順位やグレードは付けない。 */
export function checklist(stats, lang = "ja") {
  const obs = stats.observed_domains;
  const t = L[lang] ?? L.ja;
  return [
    { label: t.spf, value: rate(stats.spf_adopted_domains, obs) },
    { label: t.dmarc, value: rate(stats.dmarc_adopted_domains, obs) },
    { label: t.enforced, value: rate(stats.dmarc_enforced_domains, obs) },
    { label: t.effective, value: rate(stats.enforced_reject_domains, obs) },
    { label: t.dkim, value: rate(stats.dkim_detected_domains, obs) },
    { label: t.mtasts, value: rate(stats.mta_sts_domains, obs) },
    { label: t.tlsrpt, value: rate(stats.tls_rpt_domains, obs) },
    { label: t.dnssec, value: rate(stats.dnssec_domains, obs) },
  ];
}

export function checklistClass(value) {
  if (value === null || value === undefined) return "unknown";
  if (value >= 0.6) return "met";
  if (value >= 0.2) return "partial";
  return "unmet";
}

/** 各標準の件数と割合。**チェックリストの棒だけでは数字が読めない。**
 *
 * 棒の長さは傾向を見るのに向くが、「SPF は何ドメインか」を知りたい読み手には
 * 答えない。件数と率を並べて、どちらの問いにも答えられるようにする。
 *
 * 分母は observed_domains で固定する（rate と同じ理由）。
 */
export function indicators(stats, lang = "ja") {
  const obs = stats.observed_domains;
  const t = L[lang] ?? L.ja;
  const row = (label, n, note = "") => ({
    [t.colIndicator]: label,
    [t.colDomains]: n ?? null,
    [t.colRate]: pct(rate(n, obs)),
    [t.colNote]: note,
  });
  return [
    row(t.rSpf, stats.spf_adopted_domains),
    row(
      t.rDkim,
      stats.dkim_detected_domains,
      Number.isFinite(stats.dkim_other_selector_domains)
        ? t.rDkimOther(stats.dkim_other_selector_domains)
        : t.rDkimNote
    ),
    // 鍵の長さを集計していない月は行ごと出さない（0 と読めてしまう）
    ...(Number.isFinite(stats.dkim_key_2048_domains)
      ? [row(t.rDkim2048, stats.dkim_key_2048_domains,
             t.rDkim2048Note(stats.dkim_key_1024_domains, stats.dkim_key_short_domains))]
      : []),
    // **無効なレコードも「公開している」に入っている。** 集計していない月は
    // 書かない（0 と読めてしまう）
    row(
      t.rDmarc,
      stats.dmarc_adopted_domains,
      Number.isFinite(stats.dmarc_invalid_domains) && stats.dmarc_invalid_domains > 0
        ? t.rDmarcInvalid(stats.dmarc_invalid_domains)
        : ""
    ),
    row(t.rEnforced, stats.dmarc_enforced_domains, t.rEnforcedNote),
    row(t.rEffective, stats.enforced_reject_domains, t.rEffectiveNote),
    row(t.rMtaSts, stats.mta_sts_domains, t.rMtaStsNote),
    // **ポリシーを取得していない月は行ごと出さない。** 0 と出すと「enforce が
    // 無い」と読める（gold では null。2026-11 の米国から取得している）
    ...(Number.isFinite(stats.mta_sts_enforce_domains)
      ? [row(t.rMtaStsEnforce, stats.mta_sts_enforce_domains, t.rMtaStsEnforceNote)]
      : []),
    row(t.rTlsRpt, stats.tls_rpt_domains),
    row(
      t.rBimi,
      stats.bimi_domains,
      Number.isFinite(stats.bimi_vmc_domains) && stats.bimi_domains > 0
        ? t.rBimiNote(stats.bimi_vmc_domains)
        : ""
    ),
    row(t.rDnssec, stats.dnssec_domains),
    row(t.rDane, stats.dane_domains, t.rDaneNote),
  ];
}

/** 企業数ベースの対比。ドメイン数だけだと多ドメイン企業の重みが大きくなる。 */
export function entityIndicators(stats, lang = "ja") {
  const n = stats.total_entities;
  const t = L[lang] ?? L.ja;
  const row = (label, v) => ({
    [t.colIndicator]: label,
    [t.colEntities]: v ?? null,
    [t.colRate]: pct(rate(v, n)),
  });
  return [
    row(t.eSpf, stats.spf_adopted_entities),
    row(t.eDmarc, stats.dmarc_adopted_entities),
    row(t.eEnforced, stats.dmarc_enforced_entities),
  ];
}

/** 件数の表記。**「0」と「まだ無い」を同じ見た目にしない**（原則5）。 */
export function num(value) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("ja-JP");
}
