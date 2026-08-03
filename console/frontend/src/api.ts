// バックエンド（FastAPI）との通信。ローカル専用なので認証は無い。

export type PhaseStatus = "success" | "partial" | "failed" | "not_run";

export interface Counts {
  input: number;
  success: number;
  failed: number;
  skipped: number;
}

export interface WarningItem {
  code: string;
  count: number;
  sample: string[];
  message: string | null;
}

export interface OutputRef {
  path: string;
  records: number | null;
  bytes: number | null;
}

export interface PhaseView {
  phase: string;
  label: string;
  output: string;
  status: PhaseStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_sec: number | null;
  counts: Counts | null;
  failure_breakdown: Record<string, number>;
  warnings: WarningItem[];
  outputs: OutputRef[];
  breakdown: Record<string, unknown>;
  error: string | null;
  attribution: string[];
}

export interface RunView {
  run_id: string;
  phases: PhaseView[];
}

export interface Population {
  id: string;
  label: string;
  country: string;
  enabled: boolean;
  implemented: boolean;
  blocked_by: string | null;
  path: string;
}

export interface Job {
  id: string;
  command: string;
  run_id: string;
  phases: string[];
  status: "running" | "success" | "failed" | "cancelled";
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  line_count: number;
  lines?: string[];
}

export interface ViewDef {
  id: string;
  label: string;
  description: string | null;
  default_group_by: string;
  requires_segment: boolean;
}

export interface ViewGroup {
  code: string;
  label: string;
  n: number;
  share: number;
}

export interface ViewResult {
  view_id: string;
  label: string;
  group_by: string;
  total: number;
  groups: ViewGroup[];
  segment_data_available: boolean;
  warnings: string[];
  coverage: {
    entities_in_run: number;
    entities_in_view: number;
    with_official_domain: number;
    with_common12: number;
    with_segment: number;
  };
}

export interface UnknownHost {
  registered_domain: string;
  count: number;
  examples: string[];
}

export interface UnknownHostsResult {
  run_id: string;
  hosts: UnknownHost[];
  domains_with_no_inference: number | null;
  no_inference_rate: number | null;
  fingerprint_version: string | null;
}

export interface GoldStats {
  measured_month: string;
  population_id: string;
  total_entities: number;
  total_domains: number;
  observed_domains: number;
  spf_adopted_entities: number;
  spf_adopted_domains: number;
  dmarc_adopted_entities: number;
  dmarc_adopted_domains: number;
  dmarc_enforced_entities: number;
  dmarc_enforced_domains: number;
  nominal_reject_domains: number;
  enforced_reject_domains: number;
  blind_reject_domains: number;
  dkim_detected_domains: number;
  dkim_not_found_domains: number;
  mta_sts_domains: number;
  tls_rpt_domains: number;
  bimi_domains: number;
  dnssec_domains: number;
  maturity_stage_dist: Record<string, number> | null;
  sending_domains: number;
  sending_enforced: number;
  parked_domains: number;
  parked_hardened: number;
  parked_defended: number;
  parked_intentional: number;
  parked_neglected: number;
  park_defense_rate: number | null;
  dane_domains: number;
  dane_dnssec_valid: number;
  dane_orphan: number;
  delta_prev_month: GoldDelta | null;
  diff_prev_month: Record<string, number> | null;
  previous_month: string | null;
}

export interface GoldDelta {
  entities_new: number;
  entities_removed: number;
  domains_new: number;
  domains_disappeared: number;
  policy_upgraded: number;
  policy_downgraded: number;
  domains_unobserved_this_month: number;
  skipped: boolean;
  notes: string[];
}

export interface GoldSector extends GoldStats {
  common12_code: string;
  common12_label: string;
  n_entities: number;
  suppressed: boolean;
}

export interface GoldMonth {
  month: string;
  previous_month: string;
  overall: GoldStats[];
  by_sector: GoldSector[];
  suppressed_sectors: string[];
}

export interface DictionaryFile {
  file: string;
  editable: boolean;
  category: string | null;
  version: string | null;
  rule_count: number;
  jp_rule_count: number;
}

export interface FingerprintsResult {
  dictionaries: DictionaryFile[];
  total_rules: number;
  version: string;
  record_types: string[];
  undetectable_by_dns: { vendor: string; product: string | null }[];
}

export interface NewRule {
  file: string;
  id: string;
  vendor: string;
  record: string;
  pattern: string;
  product?: string | null;
  confidence?: string;
  region?: string | null;
  note?: string | null;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${path}: ${detail}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => get<{ status: string; version: string; data_root: string }>("/api/health"),
  runs: () => get<{ runs: string[] }>("/api/runs"),
  run: (runId: string) => get<RunView>(`/api/runs/${runId}`),
  populations: () => get<{ populations: Population[] }>("/api/populations"),
  jobs: () => get<{ jobs: Job[] }>("/api/jobs"),
  views: () => get<{ views: ViewDef[]; group_by_options: string[] }>("/api/views"),
  runView: (runId: string, view: string, by: string) =>
    get<ViewResult>(
      `/api/runs/${runId}/view?view=${encodeURIComponent(view)}&by=${encodeURIComponent(by)}`,
    ),
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  fingerprints: () => get<FingerprintsResult>("/api/dict/fingerprints"),
  goldMonths: () => get<{ months: string[] }>("/api/gold/months"),
  goldMonth: (month: string) => get<GoldMonth>(`/api/gold/${encodeURIComponent(month)}`),
  unknownHosts: (runId: string) =>
    get<UnknownHostsResult>(`/api/dict/unknown-hosts?run=${encodeURIComponent(runId)}`),

  async addRule(body: NewRule): Promise<{ rule_id: string; rule_count: number }> {
    const res = await fetch("/api/dict/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      // detail に理由が入る。壊れた正規表現や id 重複をそのまま見せる
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `${res.status}`);
    }
    return await res.json();
  },

  async createJob(body: {
    phases: string[];
    run_id?: string;
    config?: string;
    limit?: number | null;
    dry_run?: boolean;
    source_file?: string | null;
  }): Promise<Job> {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return (await res.json()) as Job;
  },

  stopJob: async (id: string): Promise<Job> => {
    const res = await fetch(`/api/jobs/${id}/stop`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as Job;
  },
};

/** 実行ログの逐次受信。戻り値を呼ぶと購読を止める。 */
export function streamJob(
  id: string,
  onLine: (line: string) => void,
  onDone: (status: string) => void,
): () => void {
  const source = new EventSource(`/api/jobs/${id}/stream`);
  source.onmessage = (ev) => onLine(ev.data);
  source.addEventListener("done", (ev) => {
    onDone((ev as MessageEvent).data);
    source.close();
  });
  source.onerror = () => source.close();
  return () => source.close();
}
