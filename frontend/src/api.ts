// Thin client for the FastAPI backend. Same origin, so the session cookie
// (HttpOnly, SameSite=Strict) rides along on every request.

export type JobState = { state: "running" | "done" | "error"; message: string };

export type TractRow = {
  rank: number;
  entry_xyz_mm: number[];
  target_xyz_mm: number[];
  target_group: string;
  target_source: string;
  score: number | null;
  reachable_fraction: number;
  flexible_reachable_fraction: number | null;
  tract_length_mm: number | null;
  parenchymal_depth_mm: number | null;
  parenchymal_thickness_mm: number | null;
  infundibular_angle_deg: number | null;
  nearest_organ: string;
  nearest_organ_mm: number | null;
  colon_mm: number | null;
  pleura_mm: number | null;
  supracostal: boolean;
  collision: boolean;
  collision_organ: string | null;
};

export type StoneRow = {
  label: string;
  volume_mm3: string;
  mean_hu: string;
  max_hu: string;
  laterality: string;
};

export type CaseSummary = {
  id: string;
  case_id: string;
  shape: number[];
  spacing_mm: number[];
  has: { organs: boolean; stones: boolean; tracts_json: boolean; overlay: boolean };
  stones: StoneRow[];
  plan: null | {
    side: string;
    params: Record<string, unknown>;
    tracts: TractRow[];
    best_by_group: Record<string, TractRow>;
  };
  jobs: Partial<Record<"segment" | "plan", JobState>>;
};

export type ParamField = { name: string; default: unknown };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  session: () =>
    request<{ authenticated: boolean; segmentation_configured: boolean }>("/api/session"),
  login: (password: string) => request<{ ok: boolean }>("/api/login", json({ password })),
  logout: () => request<{ ok: boolean }>("/api/logout", { method: "POST" }),
  params: () => request<{ fields: ParamField[] }>("/api/params"),
  labels: () => request<{ display_groups: string[] }>("/api/labels"),
  createCase: (files: { ct?: File; organs?: File; stones?: File; bundle?: File }) => {
    const form = new FormData();
    for (const [key, file] of Object.entries(files)) if (file) form.append(key, file);
    return request<CaseSummary>("/api/cases", { method: "POST", body: form });
  },
  getCase: (id: string) => request<CaseSummary>(`/api/cases/${id}`),
  deleteCase: (id: string) =>
    request<{ ok: boolean }>(`/api/cases/${id}`, { method: "DELETE" }),
  segment: (id: string) => request<CaseSummary>(`/api/cases/${id}/segment`, { method: "POST" }),
  plan: (id: string, side: string, params: Record<string, unknown>) =>
    request<CaseSummary>(`/api/cases/${id}/plan`, json({ side, params })),
};

export const fileUrl = (id: string, key: string) => `/api/cases/${id}/files/${key}`;
export const bundleUrl = (id: string) => `/api/cases/${id}/bundle`;
