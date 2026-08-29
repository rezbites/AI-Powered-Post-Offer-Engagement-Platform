/**
 * Typed API client.
 *
 * Calls are made from the browser rather than from React Server Components.
 * That is a deliberate trade: server-side fetching would need one URL for the
 * compose network (`http://api:8000`) and another for the browser
 * (`http://localhost:8000`), and getting that wrong produces confusing
 * "fetch failed" errors during a demo. A single origin keeps the setup
 * honest and reliable, at the cost of first-paint speed - which for an
 * internal HR tool behind a login is the right way round.
 */

import type {
  AIStatus,
  AnalysisResponse,
  AnalyticsOverview,
  AttentionQueue,
  CandidateDetail,
  CandidateSummary,
  FollowUp,
  GeneratedMessage,
  Interaction,
  Page,
  Recruiter,
  RiskLevel,
} from "@/types/api";

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** The error envelope every backend failure uses. */
interface ApiErrorBody {
  error: { code: string; message: string; request_id: string };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    /** Correlates a user-reported failure with a server log line. */
    readonly requestId: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!res.ok) {
    // Every backend failure uses one envelope, so there is a single error
    // path here rather than one per endpoint.
    let body: ApiErrorBody | null = null;
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      /* non-JSON error (proxy, gateway) falls through to the generic message */
    }
    throw new ApiError(
      body?.error?.message ?? `Request failed with status ${res.status}`,
      body?.error?.code ?? "unknown",
      res.status,
      body?.error?.request_id ?? "-",
    );
  }

  return (await res.json()) as T;
}

/** Shape accepted by POST /candidates. */
export interface NewCandidate {
  name: string;
  email: string;
  phone: string | null;
  role_title: string;
  location: string;
  offer_date: string;
  joining_date: string;
  recruiter_id: string;
  notes: string | null;
}

export interface CandidateFilters {
  joining_month?: string;
  recruiter_id?: string;
  role_title?: string;
  risk_level?: RiskLevel;
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

function queryString(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    // Empty strings are how "no filter selected" arrives from a <select>;
    // forwarding them would filter on the empty string and return nothing.
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  attentionQueue: (limit = 10) =>
    request<AttentionQueue>(`/attention-queue?limit=${limit}`),

  candidates: (filters: CandidateFilters = {}) =>
    request<Page<CandidateSummary>>(`/candidates${queryString(filters)}`),

  candidate: (id: string) => request<CandidateDetail>(`/candidates/${id}`),

  roles: () => request<string[]>(`/candidates/roles`),

  recruiters: () => request<Recruiter[]>(`/recruiters`),

  createCandidate: (payload: NewCandidate) =>
    request<CandidateDetail>(`/candidates`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  analyze: (id: string, force = false, provider?: string) =>
    request<AnalysisResponse>(
      `/candidates/${id}/ai/analyze${queryString({ force, provider })}`,
      { method: "POST" },
    ),

  overrideRisk: (
    id: string,
    risk_level: RiskLevel,
    reason: string,
    confidence: number,
  ) =>
    request<CandidateDetail>(`/candidates/${id}/risk/override`, {
      method: "POST",
      body: JSON.stringify({ risk_level, reason, confidence }),
    }),

  revertRisk: (id: string) =>
    request<CandidateDetail>(`/candidates/${id}/risk/revert`, {
      method: "POST",
    }),

  draftMessage: (id: string, channel: "email" | "whatsapp", provider?: string) =>
    request<GeneratedMessage>(
      `/candidates/${id}/ai/message${queryString({ channel, provider })}`,
      { method: "POST" },
    ),

  messages: (id: string) =>
    request<GeneratedMessage[]>(`/candidates/${id}/ai/messages`),

  editMessage: (messageId: string, subject: string | null, body: string) =>
    request<GeneratedMessage>(`/ai/messages/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({ subject, body }),
    }),

  approveMessage: (messageId: string) =>
    request<GeneratedMessage>(`/ai/messages/${messageId}/approve`, {
      method: "POST",
    }),

  addInteraction: (
    id: string,
    payload: {
      channel: string;
      direction: string;
      content: string;
      occurred_at?: string;
    },
  ) =>
    request<Interaction>(`/candidates/${id}/interactions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  completeStage: (id: string, stageKey: string, status: string) =>
    request<unknown>(`/candidates/${id}/stages/${stageKey}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  analytics: () => request<AnalyticsOverview>(`/analytics/overview`),

  aiStatus: () => request<AIStatus>(`/ai/status`),

  followUps: (candidateId?: string) =>
    request<FollowUp[]>(`/follow-ups${queryString({ candidate_id: candidateId })}`),

  resolveFollowUp: (id: string, status: "done" | "dismissed") =>
    request<FollowUp>(`/follow-ups/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  runAutomation: () =>
    request<unknown[]>(`/automation/run`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
};
