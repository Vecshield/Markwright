// Typed client over the REST API. Hand-written against schemas/ for the POC;
// v1 generates this from the OpenAPI document.

export type Band =
  | "strong_evidence"
  | "moderate_evidence"
  | "weak_evidence"
  | "no_evidence"
  | "insufficient_evidence";

export interface CandidateResult {
  config_id: string;
  scheme_id: string;
  key_version: number;
  statistic: { name: string; value: number; extra?: Record<string, unknown> };
  p_value: number;
  p_value_adjusted: number;
  ci_95: [number, number] | null;
  min_detectable_tokens: number | null;
}

export interface VerificationResult {
  result_id: string;
  requested_at: string;
  token_count: number;
  scored_token_count: number;
  fpr_target: number;
  band: Band;
  band_reason: string;
  best_match: string | null;
  candidates: CandidateResult[];
  caveats: string[];
  engine: { markwright_version: string; mode: string };
}

export interface Config {
  config_id: string;
  model_id: string;
  model_version: string;
  config_version: number;
  scheme_id: string;
  status: "draft" | "active" | "deprecated" | "retired";
  verification_defaults: {
    fpr_target?: number;
    min_tokens?: number;
    min_detectable_tokens?: number | null;
    tokenizer_hash?: string | null;
  };
  // operator-only fields (absent for verifiers):
  scheme_params?: Record<string, unknown>;
  key_ref?: string;
  calibration_ref?: string | null;
  detector_ref?: string | null;
  labels?: Record<string, string>;
  tokenizer_ref?: string;
  created_at?: string;
}

export interface ModelRow {
  model_id: string;
  display_name: string;
  created_at: string;
}

export interface SchemeCatalogueEntry {
  scheme_id: string;
  title: string;
  citation: string;
  status: string;
  installed: boolean;
  scheme_version?: string;
  how_it_marks: string;
  how_it_detects: string;
  statistic: string;
  key_material: string;
  strengths: string[];
  limits: string[];
  params: Record<string, string>;
  defaults?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
}

export interface DemoSample {
  id: string;
  label: string;
  expect: string;
  kind: "text" | "token_ids";
  text: string | null;
  token_ids: number[];
  why: string;
}

export interface SchemeInfo {
  scheme_id: string;
  scheme_version: string;
  capabilities: Record<string, unknown>;
  defaults: Record<string, unknown>;
}

export interface CalibrationRun {
  run_id: string;
  config_id: string;
  status: string;
  quality: string;
  n_docs: number;
  resolution_floor: number;
  fpr_target: number;
  null_corpus_ids: string[];
  buckets: { length: number; n: number; has_tail: boolean }[];
  min_detectable_tokens: number | null;
  notes: string[];
}

export interface KeyRow {
  key_id: string;
  purpose: string;
  owner_model_id: string | null;
  backend: string;
  key_version: number;
  lineage_id: string;
  state: string;
  valid_from: string;
  generate_until: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  created_at: string;
}

export interface Policy {
  fpr_target: number;
  retention_days: number;
  custody_default: string;
}

export interface ResultRow {
  result_id: string;
  requested_at: string;
  requested_by: string | null;
  token_count: number;
  fpr_target: number;
  band: Band;
  best_match: string | null;
  model_id: string | null;
  model_version: string | null;
}

export interface AuditEntry {
  seq: number;
  ts: string;
  actor: { principal: string; role: string };
  action: string;
  target: { kind: string; id: string };
  outcome: string;
  entry_hash: string;
}

const TOKEN_KEY = "markwright_token";
const THEME_KEY = "markwright_theme";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "verifier-token";
}
export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t);
}
export function roleOf(token: string): string {
  return token.replace("-token", "");
}
export function getTheme(): string {
  return localStorage.getItem(THEME_KEY) ?? "midnight";
}
export function setTheme(t: string): void {
  localStorage.setItem(THEME_KEY, t);
  document.documentElement.setAttribute("data-theme", t);
}

/** "1 in 100" phrasing for a false-positive target; rounds the odds DOWN so
 * the display never overstates rarity. */
export function fmtFpr(fpr: number): string {
  const n = Math.floor(1 / fpr);
  return `1 in ${n.toLocaleString()}`;
}

function fetchOrThrow(method: string, path: string, body?: unknown): Promise<Response> {
  return fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetchOrThrow(method, path, body);
  } catch {
    // fetch() rejects with a bare TypeError when nothing answers; say what to do
    throw new ApiError(0,
      "Cannot reach the Markwright API. Start it with " +
      "`.venv/bin/uvicorn markwright.api.app:app` (the dashboard proxies to :8000).");
  }
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* raw text is fine */
    }
    throw new ApiError(res.status, `${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // verification
  verifyText: (text: string, fpr_target?: number) =>
    call<VerificationResult>("POST", "/v1/verify", { text, fpr_target }),
  verifyIds: (token_ids: number[], fpr_target?: number) =>
    call<VerificationResult>("POST", "/v1/verify", { token_ids, fpr_target }),
  results: () => call<ResultRow[]>("GET", "/v1/verify/results"),
  // registry
  models: () => call<ModelRow[]>("GET", "/v1/models"),
  createModel: (model_id: string, display_name = "") =>
    call<ModelRow>("POST", "/v1/models", { model_id, display_name }),
  addVersion: (model_id: string, model_version: string, tokenizer_ref: string) =>
    call<unknown>("POST", `/v1/models/${model_id}/versions`, { model_version, tokenizer_ref }),
  configs: (model?: string) =>
    call<Config[]>("GET", `/v1/configs${model ? `?model=${encodeURIComponent(model)}` : ""}`),
  createConfig: (model: string, version: string, scheme_id: string, scheme_params?: object) =>
    call<Config>("POST", `/v1/models/${model}/versions/${version}/configs`, { scheme_id, scheme_params }),
  transition: (config_id: string, action: "activate" | "deprecate" | "retire") =>
    call<Config>("POST", `/v1/configs/${config_id}:${action}`),
  publishDetector: (config_id: string) =>
    call<{ detector_ref: string; sha256: string; note: string }>(
      "POST", `/v1/configs/${config_id}/detector:publish`),
  schemes: () => call<SchemeInfo[]>("GET", "/v1/schemes"),
  catalogue: () => call<SchemeCatalogueEntry[]>("GET", "/v1/schemes/catalogue"),
  samples: () => call<{ samples: DemoSample[]; notes: string[] }>("GET", "/v1/samples"),
  // calibration
  corpora: () => call<string[]>("GET", "/v1/null-corpora"),
  calibrate: (config_id: string, null_corpus_ids: string[]) =>
    call<CalibrationRun>("POST", `/v1/configs/${config_id}/calibrations`, { null_corpus_ids }),
  acceptCalibration: (run_id: string) =>
    call<CalibrationRun>("POST", `/v1/calibrations/${run_id}:accept`),
  calibrations: (config_id: string) =>
    call<CalibrationRun[]>("GET", `/v1/configs/${config_id}/calibrations`),
  // keys
  keys: () => call<KeyRow[]>("GET", "/v1/keys"),
  rotateKey: (key_id: string) =>
    call<{ old_key: KeyRow; new_key: KeyRow; configs: { old: string; new: string }[] }>(
      "POST", `/v1/keys/${key_id}:rotate`),
  revokeKey: (key_id: string, reason: string) =>
    call<KeyRow>("POST", `/v1/keys/${key_id}:revoke`, { reason }),
  // admin
  policy: () => call<Policy>("GET", "/v1/policy"),
  putPolicy: (p: Partial<Policy>) => call<Policy>("PUT", "/v1/policy", p),
  audit: () => call<{ head: string; entries: AuditEntry[] }>("GET", "/v1/audit"),
  readyz: () =>
    call<{ ready: boolean; schemes: number; profile: string; calibrated_configs: number }>(
      "GET", "/readyz"),
};
