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
