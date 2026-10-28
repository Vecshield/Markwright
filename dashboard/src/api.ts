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
  key_ref?: string;
  calibration_ref?: string | null;
  detector_ref?: string | null;
  labels?: Record<string, string>;
  created_at?: string;
}

export interface ModelRow {
  model_id: string;
  display_name: string;
  created_at: string;
}

const TOKEN_KEY = "markwright_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "verifier-token";
}

export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t);
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  verifyText: (text: string, fpr_target?: number) =>
    call<VerificationResult>("POST", "/v1/verify", { text, fpr_target }),
  verifyIds: (token_ids: number[], fpr_target?: number) =>
    call<VerificationResult>("POST", "/v1/verify", { token_ids, fpr_target }),
  models: () => call<ModelRow[]>("GET", "/v1/models"),
  configs: (model?: string) =>
    call<Config[]>("GET", `/v1/configs${model ? `?model=${encodeURIComponent(model)}` : ""}`),
  policy: () => call<{ fpr_target: number; retention_days: number }>("GET", "/v1/policy"),
};
