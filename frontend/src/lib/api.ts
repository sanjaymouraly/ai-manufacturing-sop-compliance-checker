/**
 * Typed API client.
 *
 * All requests go to same-origin `/api/*`, which the dev server and the production
 * reverse proxy forward to the backend. The browser never talks to a hard-coded
 * host, so the app works unchanged behind a sandbox preview proxy.
 */

export type Role = 'administrator' | 'auditor' | 'reviewer' | 'viewer';

export type Severity = 'critical' | 'major' | 'minor' | 'info';

export type Classification =
  | 'confirmed_deviation'
  | 'possible_deviation'
  | 'insufficient_evidence'
  | 'sop_conflict'
  | 'no_detected_deviation';

export type AuditOutcome =
  | 'no_detected_deviation'
  | 'deviations_found'
  | 'human_review_required'
  | 'blocked_unresolved_authority';

export interface User {
  id: number;
  username: string;
  full_name: string | null;
  email: string | null;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface RevisionSummary {
  id: number;
  document_id: number;
  revision_code: string;
  title: string | null;
  status: string;
  effective_date: string | null;
  expiry_date: string | null;
  approved_by: string | null;
  approval_date: string | null;
  supersedes_revision_code: string | null;
  superseded_by_revision_code: string | null;
  applicability: Record<string, unknown>;
  file_name: string;
  file_hash: string;
  file_size: number;
  source_format: string | null;
  page_count: number;
  extraction_confidence: number;
  ocr_used: boolean;
  extraction_warnings: string[];
  injection_flags: Array<Record<string, unknown>>;
  created_at: string;
  requirement_count: number;
}

export interface SectionOut {
  id: number;
  section_path: string;
  heading: string | null;
  level: number;
  kind: string;
  page: number;
  order_index: number;
  char_start: number;
  char_end: number;
  text: string;
}

export interface SopDocumentSummary {
  id: number;
  sop_code: string;
  title: string;
  process_area: string | null;
  owner: string | null;
  description: string | null;
  is_archived: boolean;
  created_at: string;
  revisions: RevisionSummary[];
  revision_count: number;
  latest_revision_code: string | null;
  approved_revision_code: string | null;
  has_open_conflicts: boolean;
}

export type SopDocumentDetail = SopDocumentSummary;

export interface RequirementOut {
  id: number;
  req_key: string;
  seq: number;
  step_number: string | null;
  text: string;
  obligation: string;
  action_verb: string | null;
  condition_text: string | null;
  requires_record: boolean;
  parameter_name: string | null;
  comparator: string | null;
  value_min: number | null;
  value_max: number | null;
  tolerance: number | null;
  unit: string | null;
  section_path: string;
  page: number;
  char_start: number;
  char_end: number;
  confidence: number;
  extraction_method: string;
  ambiguity_notes: string[];
}

export interface RevisionDetail extends RevisionSummary {
  raw_text: string;
  sections: SectionOut[];
  requirements: RequirementOut[];
}

export interface ReportEventOut {
  id: number;
  seq: number;
  step_number: string | null;
  text: string;
  status: string;
  parameter_name: string | null;
  value_num: number | null;
  value_text: string | null;
  unit: string | null;
  operator: string | null;
  timestamp_text: string | null;
  char_start: number;
  char_end: number;
}

export interface ProcessReportSummary {
  id: number;
  report_code: string;
  title: string | null;
  batch_id: string | null;
  line_id: string | null;
  product_code: string | null;
  site: string | null;
  operator: string | null;
  executed_at: string | null;
  declared_sop_code: string | null;
  declared_revision_code: string | null;
  file_name: string;
  source_format: string;
  event_count: number;
  extraction_confidence: number;
  injection_flags: Array<Record<string, unknown>>;
  created_at: string;
}

export interface ProcessReportDetail extends ProcessReportSummary {
  raw_text: string;
  events: ReportEventOut[];
  extraction_warnings: string[];
}

export interface CitationOut {
  id: number;
  source_type: 'sop' | 'report';
  document_label: string;
  quote: string;
  char_start: number;
  char_end: number;
  page: number | null;
  section_path: string | null;
  verified: boolean;
  verification_note: string | null;
  revision_id: number | null;
  report_id: number | null;
}

export interface FindingOut {
  id: number;
  finding_code: string;
  classification: Classification;
  category: string;
  severity: Severity;
  title: string;
  expected: string | null;
  observed: string | null;
  explanation: string;
  suggested_action: string | null;
  evidence_sufficiency: string;
  confidence: number;
  detector: string;
  verification_status: string;
  verification_notes: string[];
  uncertainty_notes: string[];
  requirement_key: string | null;
  sop_code: string | null;
  revision_code: string | null;
  review_status: string;
  reviewer_comment: string | null;
  reviewed_at: string | null;
  citations: CitationOut[];
  metrics: Record<string, unknown>;
  audit_id: number;
}

export interface AuditSummary {
  id: number;
  audit_code: string;
  report_id: number;
  report_code: string | null;
  resolved_revision_id: number | null;
  sop_code: string | null;
  revision_code: string | null;
  status: string;
  outcome: AuditOutcome;
  mode: string;
  provider_used: string | null;
  model_used: string | null;
  requires_human_review: boolean;
  review_status: string;
  duration_ms: number | null;
  finding_count: number;
  created_at: string;
}

export interface VersionCandidate {
  revision_id: number;
  revision_code: string;
  status: string;
  effective_date: string | null;
  expiry_date: string | null;
  applicable: boolean;
  in_force_at_execution: boolean | null;
  authoritative: boolean;
  reasons: string[];
}

export interface VersionResolution {
  resolved_revision_id: number | null;
  resolved_revision_code: string | null;
  authority_basis: string;
  blocked: boolean;
  requires_human_review: boolean;
  declared_revision_code: string | null;
  declared_revision_id: number | null;
  declared_matches_resolved: boolean | null;
  candidates: VersionCandidate[];
  notes: string[];
}

export interface PipelineStage {
  stage: string;
  duration_ms: number;
  at: string;
  [key: string]: unknown;
}

export interface AuditDetail extends AuditSummary {
  findings: FindingOut[];
  version_resolution: VersionResolution;
  pipeline_log: PipelineStage[];
  summary: Record<string, unknown>;
  token_usage: Record<string, number> | null;
  cost_usd: number | null;
  engine_version: string;
}

export interface ConflictOut {
  id: number;
  conflict_code: string;
  conflict_type: string;
  severity: Severity;
  status: string;
  document_id: number | null;
  revision_a_id: number | null;
  revision_b_id: number | null;
  title: string;
  description: string;
  evidence_a: Record<string, unknown>;
  evidence_b: Record<string, unknown>;
  applicable_context: Record<string, unknown>;
  blocks_conclusion: boolean;
  resolution_rationale: string | null;
  resolution_choice: string | null;
  resolved_by_id: number | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  sop_code: string | null;
  revision_a_code: string | null;
  revision_b_code: string | null;
}

export interface ReviewActionOut {
  id: number;
  entity_type: string;
  entity_id: number;
  action: string;
  from_state: string | null;
  to_state: string | null;
  reason: string;
  payload: Record<string, unknown>;
  actor_id: number | null;
  actor_name: string | null;
  created_at: string;
}

export interface DashboardData {
  caveat: string;
  totals: {
    sop_documents: number;
    sop_revisions: number;
    process_reports: number;
    audits: number;
    findings: number;
    open_conflicts: number;
    audits_requiring_review: number;
    open_findings: number;
  };
  audits_by_outcome: Record<string, number>;
  findings_by_severity: Record<string, number>;
  findings_by_category: Record<string, number>;
  findings_by_classification: Record<string, number>;
  findings_by_review_status: Record<string, number>;
  conflicts_by_status: Record<string, number>;
  conflicts_by_type: Record<string, number>;
  evidence_verification: {
    citations_total: number;
    citations_verified: number;
    validity_rate: number | null;
  };
  top_missing_steps: Array<{ requirement_key: string; count: number }>;
  recurring_conflicts: Array<{ title: string; type: string; count: number }>;
  trend: Array<{ date: string; audits: number; with_deviations: number; requiring_review: number }>;
  review_turnaround_hours_avg: number | null;
  mode_performance: Array<{ mode: string; audits: number; avg_duration_ms: number }>;
}

export interface ExportArtifactOut {
  id: number;
  audit_id: number;
  audit_code: string | null;
  format: string;
  version: number;
  file_name: string;
  file_size: number;
  sha256: string;
  created_at: string;
  created_by: string | null;
  download_url: string;
}

export interface ProviderConfigOut {
  id: number;
  name: string;
  provider_type: string;
  base_url: string | null;
  model: string | null;
  /** Last four characters only. The key itself is never returned. */
  api_key_last4: string | null;
  timeout_seconds: number;
  max_tokens: number;
  temperature: number;
  allow_paid_requests: boolean;
  request_budget_usd: number;
  spent_usd: number;
  /** Cumulative token ceiling; 0 means no ceiling. Enforceable without pricing. */
  token_budget: number;
  spent_tokens: number;
  is_active: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
  capabilities: Record<string, unknown>;
  created_at: string;
}

/** One entry from a provider's model catalogue. Prices are null when the
 *  provider publishes none, which is the normal case for gateways and
 *  self-hosted endpoints. */
export interface ProviderModel {
  id: string;
  name: string | null;
  context_length: number | null;
  prompt_price_per_1k: number | null;
  completion_price_per_1k: number | null;
  supports_json: boolean | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const TOKEN_KEY = 'sop-auditor-token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode); the session simply will not persist */
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  formData?: FormData;
  raw?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, formData, raw } = options;
  const url = new URL(`/api${path}`, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(url.toString(), {
    method,
    headers,
    body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (response.status === 401 && !path.startsWith('/auth/login')) {
    setToken(null);
    window.dispatchEvent(new CustomEvent('sop-auth-expired'));
  }

  if (!response.ok) {
    let detail: unknown;
    let message = `${response.status} ${response.statusText}`;
    try {
      detail = await response.json();
      const d = detail as { detail?: unknown };
      if (typeof d.detail === 'string') message = d.detail;
      else if (Array.isArray(d.detail)) {
        message = d.detail
          .map((item: { msg?: string; loc?: unknown[] }) =>
            item.loc ? `${item.loc.slice(1).join('.')}: ${item.msg}` : item.msg,
          )
          .join('; ');
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, message, detail);
  }

  if (raw) return (await response.blob()) as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string; user: User }>('/auth/login', {
      method: 'POST',
      body: { username, password },
    }),
  me: () => request<User>('/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    request<{ message: string }>('/auth/change-password', {
      method: 'POST',
      body: { current_password, new_password },
    }),
  health: () => request<Record<string, unknown>>('/health'),

  users: () => request<User[]>('/users'),
  createUser: (payload: Record<string, unknown>) =>
    request<User>('/users', { method: 'POST', body: payload }),
  updateUser: (id: number, payload: Record<string, unknown>) =>
    request<User>(`/users/${id}`, { method: 'PATCH', body: payload }),

  documents: (params?: Record<string, string | number | undefined>) =>
    request<Page<SopDocumentSummary>>('/documents', { params }),
  document: (id: number) => request<SopDocumentDetail>(`/documents/${id}`),
  revision: (id: number) => request<RevisionDetail>(`/documents/revisions/${id}`),
  uploadDocument: (formData: FormData) =>
    request<RevisionSummary>('/documents/upload', { method: 'POST', formData }),
  setRevisionStatus: (id: number, status: string, reason: string) =>
    request<RevisionSummary>(`/documents/revisions/${id}/status`, {
      method: 'PATCH',
      body: { status, reason },
    }),
  diff: (left: number, right: number) =>
    request<{
      left_revision_id: number;
      right_revision_id: number;
      left_label: string;
      right_label: string;
      summary: Record<string, number>;
      deltas: Array<Record<string, unknown>>;
    }>(`/documents/revisions/${left}/diff/${right}`),
  scanConflicts: () => request<{ message: string }>('/documents/scan-conflicts', { method: 'POST' }),

  reports: (params?: Record<string, string | number | undefined>) =>
    request<Page<ProcessReportSummary>>('/reports', { params }),
  report: (id: number) => request<ProcessReportDetail>(`/reports/${id}`),
  uploadReport: (formData: FormData) =>
    request<ProcessReportSummary>('/reports/upload', { method: 'POST', formData }),

  audits: (params?: Record<string, string | number | undefined>) =>
    request<Page<AuditSummary>>('/audits', { params }),
  audit: (id: number) => request<AuditDetail>(`/audits/${id}`),
  runAudit: (report_id: number, mode?: string) =>
    request<AuditDetail>('/audits', { method: 'POST', body: { report_id, mode } }),
  auditHistory: (id: number) => request<ReviewActionOut[]>(`/audits/${id}/history`),
  reviewAudit: (id: number, action: string, reason: string) =>
    request<AuditSummary>(`/audits/${id}/review`, { method: 'POST', body: { action, reason } }),

  findings: (params?: Record<string, string | number | undefined>) =>
    request<Page<FindingOut>>('/findings', { params }),
  finding: (id: number) => request<FindingOut>(`/findings/${id}`),
  reviewFinding: (id: number, action: string, reason: string) =>
    request<FindingOut>(`/findings/${id}/review`, { method: 'POST', body: { action, reason } }),
  findingHistory: (id: number) => request<ReviewActionOut[]>(`/findings/${id}/history`),
  queueSummary: () => request<Record<string, Record<string, number>>>('/findings/queue/summary'),

  conflicts: (params?: Record<string, string | number | undefined>) =>
    request<Page<ConflictOut>>('/conflicts', { params }),
  conflict: (id: number) => request<ConflictOut>(`/conflicts/${id}`),
  resolveConflict: (id: number, choice: string, rationale: string, accept_risk = false) =>
    request<ConflictOut>(`/conflicts/${id}/resolve`, {
      method: 'POST',
      body: { choice, rationale, accept_risk },
    }),
  conflictHistory: (id: number) => request<ReviewActionOut[]>(`/conflicts/${id}/history`),

  dashboard: (days = 90) => request<DashboardData>('/analytics/dashboard', { params: { days } }),
  trends: (days = 90) => request<Record<string, unknown>>('/analytics/trends', { params: { days } }),
  reviewers: () => request<Record<string, unknown>>('/analytics/reviewers'),

  exports: (params?: Record<string, string | number | undefined>) =>
    request<Page<ExportArtifactOut>>('/exports', { params }),
  createExport: (auditId: number, format: string) =>
    request<ExportArtifactOut>(`/exports/audits/${auditId}/${format}`, { method: 'POST' }),

  providers: () => request<ProviderConfigOut[]>('/providers'),
  providerCatalogue: () =>
    request<{
      providers: Array<{
        provider_type: string;
        requires_api_key: boolean;
        is_paid: boolean;
        default_base_url: string;
        description: string;
      }>;
      env_default: Record<string, unknown>;
      notes: string[];
    }>('/providers/catalogue'),
  saveProvider: (payload: Record<string, unknown>) =>
    request<ProviderConfigOut>('/providers', { method: 'PUT', body: payload }),
  testProvider: (payload: Record<string, unknown>) =>
    request<Record<string, unknown>>('/providers/test', { method: 'POST', body: payload }),
  // POST, not GET: discovery must work before the configuration is saved, and
  // the API key must not travel in a query string.
  providerModels: (body: {
    provider_type?: string;
    config_id?: number;
    base_url?: string;
    model?: string;
    api_key?: string;
  }) =>
    request<{ models: ProviderModel[]; source: string; count?: number }>('/providers/models', {
      method: 'POST',
      body,
    }),
  deleteProvider: (id: number) =>
    request<{ message: string }>(`/providers/${id}`, { method: 'DELETE' }),

  events: (params?: Record<string, string | number | undefined>) =>
    request<Page<Record<string, unknown>>>('/events', { params }),
  settings: () => request<Record<string, unknown>>('/settings'),
};

/** Download a binary export through the authenticated API. */
export async function downloadExport(auditId: number, format: string, filename: string) {
  const blob = await request<Blob>(`/exports/audits/${auditId}/${format}/inline`, { raw: true });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
