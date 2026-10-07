/** Shapes of the FraudLens API responses the console reads. */

export type Tier = "allow" | "warn" | "step_up" | "hold";
export type Role = "analyst" | "supervisor" | "admin" | "service";
export type Lang = "en" | "bn";
export type Text2 = { en: string; bn: string };

export interface Me {
  id: number;
  username: string;
  display_name: string;
  role: Role;
}

export interface Alert {
  txn_id: number;
  ts: string;
  type: string;
  amount: number;
  sender_id: string;
  receiver_id: string;
  district: string;
  status: string;
  status_reason: string | null;
  tier: Tier;
  risk_score: number;
  risk_band: string;
  mode: string;
  decided_by: string;
  scenario: string | null;
  case_id: number | null;
  customer_response: string | null;
  headline: Text2;
}

export interface Fact {
  en: string;
  bn: string;
  value: unknown;
  feature: string;
}

export interface Reason {
  code: string;
  // A reason from a policy rule has no facts, share or weight: a rule is not a matter of degree.
  facts?: Fact[];
  share?: number | null;
  source: string;
  weight?: number;
  title_en: string;
  title_bn: string;
  detail_en: string;
  detail_bn: string;
  direction: "raises" | "lowers";
}

export interface RuleTrace {
  id: string;
  hard: boolean;
  tier: Tier | null;
  effect: string;
  inputs: Record<string, unknown>;
  status: string;
  description: string;
}

export interface SimilarCase {
  case_id: number;
  typology: string;
  similarity: number;
  loss: number;
  amount: number;
  n_txn: number;
  day: number;
}

export interface Txn {
  txn_id: number;
  ts: string;
  type: string;
  sender_id: string;
  receiver_id: string;
  amount: number;
  sender_balance_before?: number;
  channel?: string;
  district?: string;
  status: string;
  status_reason?: string | null;
  source?: string;
  tier?: Tier;
  risk_score?: number;
  case_id?: number | null;
}

export interface Decision {
  transaction: Txn;
  tier: Tier;
  action: string;
  requires_review: boolean;
  mode: string;
  decided_by: string;
  model_tier: Tier;
  risk_score: number;
  risk_band: string;
  scores: Record<string, number | null>;
  model_version: string;
  policy_version: string;
  scenario: string | null;
  case_id: number | null;
  customer_response: string | null;
  responded_at: string | null;
  decided_at: string;
  latency_ms: number;
  rule_trace: RuleTrace[];
  fallback_signals: string[];
  reasons: Reason[];
  customer_message: Text2 | null;
  recommended_actions: { id: string; en: string; bn: string; needs_second_approver: boolean }[];
  similar_cases: SimilarCase[];
  headline: Text2;
  fraud_categories: NamedCategory[];
}

/** A fraud category put on an alert, a report or a checked message, and what it rests on. */
export interface NamedCategory {
  id: string;
  number: number;
  name: Text2;
  basis?: string[];
  probability?: number | null;
  source?: "model" | "link";
}

export type CheckLevel = "none" | "caution" | "high";

export interface MessageCheck {
  level: CheckLevel;
  risk: number | null;
  categories: NamedCategory[];
  signals: { id: string; label: Text2 }[];
  links: { host: string; flags: string[]; level: CheckLevel }[];
  advice: Text2 | null;
  model_version: string | null;
}

export interface PaymentProof {
  status: "verified" | "mismatch" | "not_found";
  checks: Record<string, boolean>;
  transaction: { txn_id: number; ts: string; type: string; amount: number; status: string; sender_id: string } | null;
  claimed: { txn_id: number | null; amount: number | null };
  message: Text2;
  text: MessageCheck | null;
}

/** A claim the demo has ready for the payment-proof check, and what the check answers for it. */
export interface PaymentClaim {
  id: string;
  wallet_id: string;
  txn_id: string | null;
  amount: number | null;
  message: string;
  expects: PaymentProof["status"];
}

export interface PaymentClaims {
  wallet_id: string | null;
  claims: PaymentClaim[];
}

export interface FraudCategory {
  id: string;
  number: number;
  name: Text2;
  summary: string;
  bangladesh: string;
  examples: string[];
  detectors: string[];
  signals: string[];
  advice: Text2;
}

interface TextRate {
  recall: number;
  false_positive_rate: number;
  false_positive_rate_hard?: number;
}

export interface TextSplit {
  messages: number;
  scam: number;
  harmless: number;
  auc: number;
  caution: TextRate;
  high: TextRate;
  with_link_check: TextRate;
  categories: Record<string, { support: number; precision: number | null; recall: number | null }>;
  languages: Record<string, { messages: number; recall: number; false_positive_rate: number }>;
  families?: Record<string, { scam: boolean; messages: number; flagged: number }>;
}

export interface FraudTaxonomy {
  version: string;
  categories: FraudCategory[];
  general_advice: Text2;
  model: { serving: boolean; version: string | null; thresholds: Record<string, number> | null };
  report: {
    model: string;
    corpus: { families: number; held_out_families: number; templates: number; messages: Record<string, number> };
    test: TextSplit;
    unseen: TextSplit;
    limits: string[];
  } | null;
}

export interface Narrative {
  text: string;
  source: "template" | "llm";
  lang: Lang;
  rejected: string[];
}

export interface CaseRow {
  id: number;
  subject_id: string;
  status: "open" | "in_review" | "escalated" | "closed";
  priority: Tier;
  source: string;
  assigned_to: number | null;
  assignee: string | null;
  opened_at: string;
  sla_due_at: string | null;
  overdue: boolean;
  verdict: string | null;
  closed_at: string | null;
  closed_by: number | null;
  closer?: string | null;
  alerts: number;
  held: number;
  held_amount: number;
}

export interface WalletRisk {
  confirmed_fraud: boolean;
  mule_score: number | null;
  mule_threshold: number;
  mule_alert: boolean;
  highest_mule_score_seen: number | null;
  features: Record<string, number | null>;
  summary: Record<string, number>;
  shares_handset_with: string[];
}

export interface CaseEvent {
  id: number;
  kind: string;
  actor: string | null;
  body: string | null;
  data: Record<string, unknown>;
  at: string;
}

export interface Freeze {
  id: number;
  wallet_id: string;
  case_id: number | null;
  reason: string;
  status: "pending" | "approved" | "rejected";
  requested_by: number;
  requester: string | null;
  decided_by: number | null;
  decider: string | null;
  decision_note: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface CustomerReport {
  id: number;
  reporter_id: string;
  reported_wallet_id: string;
  txn_id: number | null;
  category: string;
  description: string | null;
  fraud_categories: NamedCategory[];
  reported_at: string;
}

export interface CaseDetail extends Omit<CaseRow, "alerts"> {
  subject: WalletRisk;
  alerts: Alert[];
  timeline: CaseEvent[];
  freeze_requests: Freeze[];
  customer_reports: CustomerReport[];
}

export interface Wallet {
  wallet_id: string;
  registered: boolean;
  created_at: string | null;
  district: string | null;
  area_type: string | null;
  channel: string | null;
  segment: string | null;
  status: string | null;
  frozen_at: string | null;
  flag: { source: string; reason: string; case_id: number | null; flagged_at: string } | null;
  risk: WalletRisk;
  recent_transactions: Txn[];
  recent_alerts: Txn[];
  cases: { id: number; status: string; priority: Tier; source: string; opened_at: string; verdict: string | null }[];
  freeze_requests: Freeze[];
}

export interface NetNode {
  id: string;
  kind: "wallet" | "agent";
  flagged?: boolean;
  frozen?: boolean;
  mule_score?: number | null;
  role?: string;
}

export interface NetEdge {
  source: string;
  target: string;
  kind: string;
  count?: number;
}

export interface Network {
  wallet_id: string;
  nodes: NetNode[];
  edges: NetEdge[];
  truncated: boolean;
}

export interface Ring {
  ring_id: string;
  wallets: string[];
  size: number;
  confirmed: string[];
  linked_only: string[];
  takeover_victims: string[];
  shared_devices: string[];
  transfer_links: number;
  cash_out_agents: { agent_id: string; cash_outs: number }[];
  received_total: number;
  max_score: number | null;
  edges: { a: string; b: string; kind: "transfer" | "device"; device?: string | null }[];
  frozen: string[];
}

export interface Agent {
  rank: number;
  agent_id: string;
  district: string;
  risk: number | null;
  eligible: boolean;
  review_suggested: boolean;
  n_cashouts: number;
  n_cashins: number;
  n_customers: number;
  cashout_value: number;
  metrics: Record<string, { value: number | null; z: number | null }>;
  reasons: { metric: string; text: string; z: number }[];
  registered?: boolean;
  confirmed_fraud_customers?: string[];
  top_customers?: { wallet_id: string; transactions: number }[];
  recent_cash_outs?: Txn[];
}

export interface AuditRow {
  id: number;
  at: string;
  actor: string | null;
  role: string | null;
  action: string;
  object_type: string | null;
  object_id: string | null;
  detail: Record<string, unknown>;
  request_id: string | null;
  ip: string | null;
}

export interface Summary {
  as_of: string;
  decisions: { by_tier: Partial<Record<Tier, number>>; by_mode: Record<string, number>; total: number };
  alert_outcomes: Record<string, { count: number; amount: number }>;
  waiting: { held: { count: number; amount: number } };
  cases: { by_status: Record<string, number>; overdue: number; verdicts: Record<string, number> };
  freeze_requests_pending: number;
  wallets_frozen: number;
  wallets_confirmed_fraud: number;
  decision_latency_ms: { p50: number | null; p95: number | null; p99: number | null };
  stream: { worker_running: boolean; processed: number; dead_lettered: number; failures: number; backlog: number };
}

export interface DailyRow {
  date: string;
  decisions: Partial<Record<Tier, number>>;
  amount: Partial<Record<Tier, number>>;
  alerts: Record<string, { count: number; amount: number }>;
}

export interface Operating {
  threshold?: number;
  alerts: number;
  alert_rate: number;
  alerts_per_day: number;
  false_alerts_per_day: number;
  precision: number;
  loss_txn_recall: number;
  case_recall: number;
  taka_recall: number;
  taka_recall_with_exit_holds: number;
  taka_at_risk: number;
  taka_stopped: number;
  by_typology?: Record<string, Record<string, number>>;
}

export interface ImpactPoint {
  threshold: number;
  alerts_per_day: number;
  false_alerts_per_day: number;
  alert_rate: number;
  precision: number;
  case_recall: number;
  loss_txn_recall: number;
  taka_recall: number;
  taka_recall_with_exit_holds: number;
  taka_stopped: number;
  legit_customers_alerted: number;
}

export interface FairRow {
  group: string;
  transactions: number;
  legitimate: number;
  false_alert_rate: number | null;
  false_hold_rate: number | null;
  false_alerts: number;
  ratio_to_overall: number | null;
  victim_transfers: number;
  victim_transfers_alerted: number;
  too_small: boolean;
}

export interface Insights {
  thresholds: Record<"warn" | "step_up" | "hold", number>;
  rows: number;
  days: number;
  at_thresholds: Record<"warn" | "step_up" | "hold", Operating>;
  impact: ImpactPoint[];
  daily: {
    day: number; date: string; scored: number; allow: number; warn: number; step_up: number; hold: number;
    fraud: number; true_alerts: number; false_alerts: number; victim_taka: number; taka_stopped: number;
    taka_held: number;
  }[];
  drift: {
    reference: { features: string; score: string };
    limits: { watch: number; shifted: number };
    periods: string[];
    feature_status: Record<string, number>;
    features: { feature: string; psi: Record<string, number>; status: string }[];
    score: { period: string; psi: number; rows: number; alert_rate: number; hold_rate: number; fraud_rate: number }[];
    validation_alert_rate: number;
  };
  fairness: {
    overall: { false_alert_rate: number; false_hold_rate: number };
    min_group_rows: number;
    sender: Record<string, FairRow[]>;
    receiver: Record<string, FairRow[]>;
    sender_largest_ratio: number;
    receiver_largest_ratio: number;
  };
}

/** The evaluation report is large and versioned; the parts the console charts are typed, the rest is read loosely. */
export interface Report {
  model_version: string;
  model: {
    data: {
      rows: Record<string, number>; fraud_chain_txns: Record<string, number>;
      victim_loss_txns: Record<string, number>; excluded_ambiguous_rows: number;
    };
    selection_on_val_b: Record<string, unknown>;
    test: {
      risk_score: Record<string, { pr_auc: number; roc_auc: number; positives: number; rows: number; base_rate: number }>;
      "ablation_at_1.00%": Record<string, Record<string, number | null>>;
      at_alert_budgets: Record<string, Operating>;
      at_fixed_thresholds: Record<string, Operating>;
      calibration: {
        brier: number; mean_predicted: number; observed_rate: number;
        reliability: { predicted: number; observed: number; rows: number }[];
      };
      mule_wallets: {
        pr_auc: number; roc_auc: number; positives: number; rows: number; wallets_alerted: number; precision: number; recall: number;
        timing: {
          mule_wallets: number; detected: number; detected_before_any_victim_paid: number;
          later_reported_by_victims: number; detected_before_the_report: number; median_hours_ahead_of_report: number | null;
        };
      };
      agents: { pr_auc: number; positives: number; rows: number; review_list_size: number; precision_at_k: number };
      rings: { rings: number; wallets_in_rings: number; true_mule_share: number };
    };
    top_features: Record<string, { feature: string; gain_share: number }[]>;
  };
  policy: {
    tier_counts: Record<string, Record<string, number>>;
    decided_by: Record<string, number>;
    rules: Record<string, {
      effect: string; fired: number; fired_precision: number | null; decisive: number;
      decisive_precision: number | null; decisive_victim_transfers: number;
    }>;
    single_decisions: { decisions: number; alerts: number; decide_ms: { p50: number; p95: number; max: number } } & Record<string, unknown>;
  } & Record<string, unknown>;
  insights: Insights | null;
  /** Young-wallet segment thresholds (policy v3) against the base policy; absent until `make mitigation` has run. */
  mitigation?: Mitigation | null;
}

type MitigationScales = Record<string, { warn: number; hold: number }>;

export interface MitigationPoint {
  scales: MitigationScales;
  admissible: boolean;
  meets_constraints: boolean;
  chosen: boolean;
  fit: Record<string, number | null>;
  test: Record<string, number | null>;
}

export interface MitigationMetric {
  key: string;
  label: string;
  before: number | null;
  after: number | null;
  difference: number | null;
  before_ci: [number | null, number | null];
  after_ci: [number | null, number | null];
  difference_ci: [number | null, number | null];
}

export type IntersectionRow = FairRow & {
  false_alert_rate_after: number | null;
  false_hold_rate_after: number | null;
  ratio_to_overall_after: number | null;
  victim_transfers_alerted_after: number | null;
};

export interface Mitigation {
  model_version: string;
  base_policy: string;
  policy_version: string;
  fitted_on: string[];
  evaluated_on: string;
  rows: { fit: number; test: number };
  days: number;
  objective: {
    minimise: string;
    subject_to: Record<string, number | string>;
    ties: string;
    warn_scales: number[];
    hold_scales: number[];
  };
  segments: {
    id: string; description: string; applies_to: string[];
    scale: Record<"warn" | "step_up" | "hold", number>;
    thresholds: Record<"warn" | "step_up" | "hold", number>;
    rows_test: number;
  }[];
  fit: {
    candidates: number; admissible: number; meeting_constraints: number;
    base: MitigationPoint; chosen: MitigationPoint;
    frontier: MitigationPoint[]; path_warn: MitigationPoint[]; path_hold: MitigationPoint[];
  };
  policy_matches_fit: boolean;
  engine_matches_grid: boolean;
  tier_counts: { before: Record<string, number>; after: Record<string, number> };
  tier_changes: { from: string; to: string; label: string; count: number }[];
  before_after: { reps: number; resampled: string; metrics: MitigationMetric[] };
  intersectional: { sender: IntersectionRow[]; receiver: IntersectionRow[] };
}

export interface ModelInfo {
  mode: string;
  model: { version: string; created_at: string; features: number; risk_source: string; mule_wallet_threshold: number };
  policy: {
    version: string;
    description: string;
    thresholds: Record<"warn" | "step_up" | "hold", number>;
    tiers: Record<Tier, { action: string; human_review: boolean; cooling_off_minutes: number; review_sla_minutes: number | null }>;
    rules: { id: string; description: string; applies_to: string[]; effect: string; tier: Tier; hard: boolean }[];
    fallback: { signals: string[]; points: Record<string, number>; max_tier: Tier };
  };
}

export interface ModelVersion {
  version: string;
  created_at: string;
  parent: string | null;
  risk_source: string;
  trees: Record<string, number>;
  thresholds: Record<string, number>;
  /** Older versions were evaluated with fewer measures, so any of these can be missing. */
  headline: Partial<Record<"pr_auc" | "precision" | "case_recall" | "loss_txn_recall" | "taka_recall" | "taka_recall_with_exit_holds", number>> | null;
  feedback: {
    rows: number; fraud: number; legitimate: number; last_label_at: string; test_rows_after_last_label: number;
    comparison_after_last_label: Record<"parent" | "retrained", Record<string, number>> | null;
  } | null;
  serving: boolean;
  promoted: boolean;
  shadow: boolean;
}

export interface Registry {
  serving: string;
  promoted: string;
  shadow: string | null;
  restart_needed: boolean;
  versions: ModelVersion[];
}

export interface Shadow {
  status: "ok" | "no_challenger" | "no_scores";
  available: string[];
  /** The challenger the running API is scoring alongside the served model, if any. */
  live?: string | null;
  model_version?: string;
  served_version?: string;
  decisions?: number;
  agreement?: number;
  matrix?: Record<string, Record<string, number>>;
  served?: { tiers: Record<string, number>; alert_rate: number; hold_rate: number };
  challenger?: { tiers: Record<string, number>; alert_rate: number; hold_rate: number };
  challenger_only_alerts?: number;
  served_only_alerts?: number;
  reviewed?: {
    confirmed_fraud: { alerts: number; challenger_also_alerts: number; challenger_holds: number };
    false_positive: { alerts: number; challenger_would_allow: number };
  };
  latency?: { scored_live: number; challenger_mean_ms: number | null; challenger_p95_ms: number | null; served_mean_ms: number | null };
}

export interface Drift {
  status: string;
  model_version: string;
  reference: { features: string; score: string };
  from: string;
  to: string;
  rows: number;
  limits: { watch: number; shifted: number };
  feature_status: Record<string, number>;
  features: { feature: string; psi: number; status: string; missing: number }[];
  score: {
    psi: number; status: string; alert_rate: number; hold_rate: number;
    validation_alert_rate: number; validation_hold_rate: number;
  };
  daily: { date: string; rows: number; psi: number; status: string; alert_rate: number }[];
}

export interface Feedback {
  cases: Record<string, number>;
  labels: { fraud: number; legitimate: number };
  last_verdict_at: string | null;
  versions_trained_on_feedback: { version: string; created_at: string; rows: number; last_label_at: string }[];
}

export interface PayResult {
  txn_id: number;
  status: string;
  status_reason: string | null;
  scored: boolean;
  duplicate: boolean;
  decision: {
    tier: Tier; action: string; requires_review: boolean; risk_score: number; risk_band: string; mode: string;
    model_version: string; policy_version: string; customer_message: Text2 | null; cooling_off_minutes: number;
    review_sla_minutes: number | null; case_id: number | null; latency_ms: number;
  } | null;
}

export interface Scenario {
  id: string;
  expected_tier: Tier;
  payment: { sender_id: string; receiver_id: string; amount: number; type: string; district: string; sender_balance_before: number };
}
