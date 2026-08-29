/**
 * Types mirroring the backend response schemas.
 *
 * Hand-written rather than generated from OpenAPI: the surface is small, and a
 * generator would add a build step and a whole category of "regenerate and
 * everything breaks" failures for little benefit at this size. At a larger API
 * surface, generating from `/openapi.json` would be the right call.
 */

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type RiskSource = "rule" | "ai" | "human";
export type CandidateStatus =
  | "offer_accepted"
  | "engaged"
  | "at_risk"
  | "joined"
  | "dropped_out";

export type SignalType =
  | "relocation_concern"
  | "competing_offer"
  | "compensation_concern"
  | "notice_period_issue"
  | "low_enthusiasm"
  | "positive_intent";

export type NextAction =
  | "CALL_CANDIDATE"
  | "SEND_RELOCATION_SUPPORT"
  | "SEND_REMINDER"
  | "MANAGER_INTRODUCTION"
  | "SCHEDULE_CONVERSATION"
  | "ESCALATE"
  | "NO_ACTION";

export interface Signal {
  type: SignalType;
  evidence: string;
}

/** Risk is never a bare label: provenance and evidence always travel with it. */
export interface RiskView {
  level: RiskLevel;
  confidence: number;
  source: RiskSource;
  rationale: string;
  factors: string[];
  signals: Signal[];
  override_reason: string | null;
  overridden_by: string | null;
  overridden_at: string | null;
  last_analyzed_at: string | null;
}

export interface JourneyProgress {
  completed: number;
  total: number;
  current_stage: string | null;
  overdue_stages: number;
}

export interface CandidateSummary {
  id: string;
  name: string;
  email: string;
  role_title: string;
  location: string;
  joining_date: string;
  days_to_joining: number;
  status: CandidateStatus;
  recruiter_id: string;
  recruiter_name: string | null;
  last_interaction_at: string | null;
  days_since_interaction: number | null;
  risk: RiskView;
  next_action: NextAction;
  next_action_label: string;
  why: string[];
  journey: JourneyProgress;
}

export interface Stage {
  key: string;
  label: string;
  sequence: number;
  status: "pending" | "completed" | "skipped";
  due_date: string | null;
  completed_at: string | null;
  is_overdue: boolean;
}

export interface Interaction {
  id: string;
  channel: string;
  direction: "inbound" | "outbound";
  content: string;
  occurred_at: string;
}

export interface CandidateDetail extends CandidateSummary {
  phone: string | null;
  offer_date: string;
  notes: string | null;
  ai_summary: string | null;
  recommended_follow_up: string | null;
  /** 'mock' in Demo Mode, 'gemini' in Live Mode. Surfaced so the UI can label it. */
  analysis_provider: string | null;
  analysis_model: string | null;
  stages: Stage[];
  interactions: Interaction[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AttentionEntry {
  candidate_id: string;
  name: string;
  role_title: string;
  recruiter_name: string | null;
  joining_date: string;
  days_to_joining: number;
  risk_level: RiskLevel;
  risk_confidence: number;
  priority: number;
  reasons: string[];
  recommended_action: NextAction;
  recommended_action_label: string;
}

export interface AttentionQueue {
  items: AttentionEntry[];
  total_active: number;
  generated_for: string;
}

export interface AnalysisResponse {
  id: string;
  candidate_id: string;
  summary: string;
  risk_level: RiskLevel;
  risk_confidence: number;
  model_confidence: number | null;
  model_risk_level: RiskLevel | null;
  engine_agreed_with_model: boolean | null;
  risk_rationale: string;
  signals: Signal[];
  next_action: NextAction;
  next_action_label: string;
  recommended_follow_up: string;
  provider: string;
  mode: "demo" | "live";
  model: string | null;
  prompt_version: string;
  analysis_status: "valid" | "repaired" | "failed";
  dropped_signals: number;
  latency_ms: number | null;
  from_cache: boolean;
  created_at: string;
}

export interface GeneratedMessage {
  id: string;
  candidate_id: string;
  channel: string;
  subject: string | null;
  body: string;
  tone: string;
  status: "draft" | "approved" | "sent_simulated";
  provider: string;
  mode: string;
  warnings?: string[];
  created_at: string;
}

export interface AIStatus {
  provider: string;
  mode: "demo" | "live";
  model: string | null;
  prompt_version: string;
  description: string;
  /** Which providers this deployment can actually honour. */
  available: Record<string, boolean>;
  /** Model name per provider, so the toggle can label each button. */
  models: Record<string, string>;
}

export interface FollowUp {
  id: string;
  candidate_id: string;
  rule_key: string | null;
  title: string;
  reason: string;
  recommended_action: NextAction;
  recommended_action_label: string;
  due_date: string | null;
  status: "open" | "done" | "dismissed";
  created_at: string;
}

export interface AnalyticsOverview {
  generated_for: string;
  totals: {
    total_offered: number;
    active: number;
    joined: number;
    dropped_out: number;
    pending_outcome: number;
  };
  conversion: {
    joined: number;
    dropped_out: number;
    resolved: number;
    /** null means nothing has resolved yet - render differently from 0. */
    resolved_rate: number | null;
    pending_outcome: number;
  };
  joining_windows: {
    next_7_days: number;
    next_15_days: number;
    next_30_days: number;
    overdue: number;
  };
  risk: {
    high: number;
    medium: number;
    low: number;
    high_risk_joining_within_7_days: number;
    human_overridden: number;
    ai_assessed: number;
  };
  engagement: {
    avg_interactions_per_candidate: number;
    avg_interactions_per_week: number;
    candidates_never_contacted: number;
    candidates_silent_over_7_days: number;
    total_interactions: number;
  };
  stages: {
    key: string;
    label: string;
    sequence: number;
    completed: number;
    pending: number;
    overdue: number;
    completion_rate: number;
    drop_off_from_previous: number;
  }[];
  recruiters: {
    recruiter_id: string;
    recruiter_name: string;
    total_candidates: number;
    joined: number;
    dropped_out: number;
    resolved: number;
    conversion_rate: number | null;
    high_risk_active: number;
    avg_days_since_interaction: number | null;
  }[];
  ai_operations: {
    total_analyses: number;
    provider: string;
    mode: string;
    valid: number;
    repaired: number;
    failed: number;
    dropped_signals: number;
    model_engine_disagreements: number;
    avg_latency_ms: number | null;
    total_tokens_in: number;
    total_tokens_out: number;
  };
}

export interface Recruiter {
  id: string;
  name: string;
}
