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
  source_case: string | null;
};

export type SeriesInfo = {
  series_uid: string;
  series_number: number;
  description: string;
  modality: string;
  kind: "ct" | "radiograph" | "other";
  n_images: number;
  slice_thickness_mm: number | null;
  orientation: string;
  usable: boolean;
};

export type ScanResult = {
  staging: string;
  n_files: number;
  series: SeriesInfo[];
  recommended_ct: string | null;
  recommended_radiograph: string | null;
};

export type LibraryCase = {
  case_id: string;
  laterality: string;
  tracts: string[];
  stone_free: boolean;
  postop_imaging: string;
  burned_in_suspected: boolean;
  postop_burned_in_suspected: boolean;
  segmented: boolean;
  planned: boolean;
};

export type IngestStatus =
  | { enabled: false }
  | {
      enabled: true;
      next_case_id: string;
      cases: LibraryCase[];
      stone_locations: string[];
      workbook_linked: boolean;
    };

export type Provider = "modal" | "remote" | "local" | "none";

/** Admin settings as the server shows them: secrets appear only as has_*. */
export type AdminSettings = {
  segmentation_provider: Provider;
  modal_app_name: string;
  modal_function_name: string;
  remote_url: string;
  local_device: "cpu" | "mps";
  workbook_path: string;
  has_modal_token_id: boolean;
  has_modal_token_secret: boolean;
  has_remote_token: boolean;
};

export type AdminView = {
  settings: AdminSettings;
  paths: { data_dir: string; key_file: string; settings_file: string };
  segmentation_configured: boolean;
  segmenter_overridden: boolean;
};

export type CheckResult = { ok: boolean; detail: string };

export type SegTest = {
  state: "idle" | "running" | "done" | "error";
  provider?: string;
  source?: string;
  seconds?: number;
  detail?: string;
  worker_seconds?: string;
  peak_gpu_gb?: string;
};

export type IngestResult = {
  case_id: string;
  notes: string[];
  metadata: Record<string, unknown>;
  cases: LibraryCase[];
  next_case_id: string;
};

/** The clinical part of POST /api/ingest/cases. Values from a workbook row can
 * be missing (null) or wrong (a string where a number belongs); the form shows
 * what is left to fix. */
export type Clinical = {
  ct: { position: string | null; site: string | null };
  operative: {
    laterality: string | null;
    or_position: string | null;
    tracts: { calyx: string; approach: string }[];
    sheath_fr: number | string | null;
    guidance: string | null;
  };
  stone: {
    location: string[];
    staghorn: string | null;
    largest_mm: number | string | null;
    manual_volume_mm3: number | string | null;
  };
  postop: { stone_free: boolean | null; residual_mm: number | string | null };
  complications: {
    pleural_injury: boolean | null;
    colon_injury: boolean | null;
    transfusion: boolean | null;
  };
  demographics: { age: number | string | null; sex: string | null; bmi: number | string | null };
};

export type WorkbookRow = {
  row: number;
  case_id: string;
  mrn: string;
  clinical: Clinical;
  errors: string[];
  postop_imaging: string | null;
  status: "ready" | "incomplete" | "ingested" | "conflict";
};

export type WorkbookResult = { rows: WorkbookRow[]; problems: string[] };

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
  ingestStatus: () => request<IngestStatus>("/api/ingest"),
  ingestCase: (body: unknown) => request<IngestResult>("/api/ingest/cases", json(body)),
  openLibraryCase: (caseId: string) =>
    request<CaseSummary>(`/api/ingest/cases/${caseId}/open`, { method: "POST" }),
  admin: () => request<AdminView>("/api/admin"),
  saveAdmin: (patch: Record<string, unknown>) =>
    request<AdminView>("/api/admin", { ...json(patch), method: "PUT" }),
  checkSegmentation: () =>
    request<CheckResult>("/api/admin/segmentation/check", { method: "POST" }),
  startSegTest: () => request<SegTest>("/api/admin/segmentation/test", { method: "POST" }),
  segTest: () => request<SegTest>("/api/admin/segmentation/test"),
  linkedWorkbook: () =>
    request<WorkbookResult & { path: string }>("/api/ingest/workbook/linked"),
  importWorkbook: (file: File) => {
    const form = new FormData();
    form.append("file", file, "workbook.xlsx");
    return request<WorkbookResult>("/api/ingest/workbook", { method: "POST", body: form });
  },
  saveToLibrary: (id: string) =>
    request<{ written: string[] }>(`/api/cases/${id}/save-to-library`, { method: "POST" }),
};

/** Upload a DICOM folder for scanning, reporting upload progress (0 to 1). */
export function scanFolder(files: File[], onProgress: (f: number) => void): Promise<ScanResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    // Browser file names are not sent: they can carry MRNs.
    files.forEach((file, i) => form.append("files", file, `f${i}`));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/ingest/scan");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: string } & Partial<ScanResult> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // keep the status text
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as ScanResult);
      else reject(new ApiError(xhr.status, body.detail ?? xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed"));
    xhr.send(form);
  });
}

export const manualCsvUrl = "/api/ingest/manual.csv";
export const workbookTemplateUrl = "/api/ingest/workbook.xlsx";

export const fileUrl = (id: string, key: string) => `/api/cases/${id}/files/${key}`;
export const bundleUrl = (id: string) => `/api/cases/${id}/bundle`;
