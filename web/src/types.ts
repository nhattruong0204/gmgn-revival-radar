export type JsonRecord = Record<string, unknown>;
export interface BriefClaim {
  key: string;
  text: string;
  source_id: string;
  field: string;
  value: number | boolean | string | null;
}
export interface ResearchBrief {
  id: string;
  brief_version: string;
  evaluation_id: number;
  scan_id: number;
  chain: string;
  contract_address: string;
  as_of: number;
  saved_at: number;
  origin: string;
  strategy: string | null;
  strategy_revision: number | null;
  score_version: string | null;
  outcome: "bull" | "bear" | "watch";
  support: BriefClaim[];
  counter_evidence: BriefClaim[];
  unresolved: BriefClaim[];
  scenarios: Array<{
    kind: string;
    text: string;
    source_id: string;
    field: string;
    level: number | null;
  }>;
  sources: Array<{
    id: string;
    kind: string;
    observed_at: number;
    fetched_at: number | null;
    expires_at: number | null;
    origin: string;
    coverage: string;
    values: Record<string, number | boolean | string | null>;
    candles: Candle[];
  }>;
}
export interface BriefPage {
  items: ResearchBrief[];
  total: number;
  page: number;
  page_size: number;
  as_of: number;
}
export interface Security {
  top10_ratio?: number | null;
  dev_ratio?: number | null;
  insider_ratio?: number | null;
  sniper_ratio?: number | null;
  bundler_ratio?: number | null;
  mint_renounced?: boolean | null;
  freeze_renounced?: boolean | null;
  dangerous?: boolean | null;
  flags?: string[];
}
export interface Token {
  timestamp: number;
  chain: string;
  contract_address: string;
  symbol: string;
  name?: string;
  price?: number | null;
  market_cap?: number | null;
  liquidity?: number | null;
  ath_market_cap?: number | null;
  drawdown_from_ath?: number | null;
  volume_5m?: number | null;
  volume_1h?: number | null;
  tx_5m?: number | null;
  holders?: number | null;
  price_change_1h?: number | null;
  security?: Security;
}
export interface Signal {
  returning_activity?: boolean | null;
  reasons?: string[];
  warnings?: string[];
  components?: Record<string, number>;
  acceleration?: Record<string, number | boolean | null>;
  structure?: {
    available?: boolean;
    base_detected?: boolean;
    base_duration_hours?: number;
    breakout_detected?: boolean;
    retest_detected?: boolean;
    higher_low_detected?: boolean;
    higher_high_detected?: boolean;
  };
}
export interface Observation extends Token {
  origin?: "live" | "synthetic" | "test" | "unknown";
  origin_confidence?: "recorded" | "unknown";
  id: number;
  score: number;
  status: string;
  eligible: boolean | null;
  setup_score?: number | null;
  trigger_score?: number | null;
  confirmation_score?: number | null;
  blockers: string[];
  missing_fields: string[];
  warnings: string[];
  preset?: string | null;
  revision?: number | null;
  token: Token | null;
  signal: Signal | null;
}
export interface ListResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  as_of: number;
  last_scan_at: number | null;
}
export interface Meta {
  active_chains?: Array<{ id: string; name: string }>;
  collection_status?: "current" | "stale" | "disabled" | "unknown";
  worker_heartbeat_at?: number | null;
  data_mode?: "saved" | "synthetic";
  chains: Array<{ id: string; name: string }>;
  telegram_url?: string | null;
  as_of: number;
  last_scan_at: number | null;
}
export interface TokenResponse {
  observation: Observation | null;
  latest_market: Token | null;
  as_of: number;
  last_scan_at: number | null;
  links?: Record<string, string | null>;
}
export interface Candle {
  volume_usd?: number | null;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
}
export interface History {
  candles_origin?: "live" | "synthetic" | "test" | "unknown";
  snapshots: Array<{
    origin?: "live" | "synthetic" | "test" | "unknown";
    timestamp: number;
    price: number | null;
    market_cap: number | null;
  }>;
  scores: Array<{
    origin?: "live" | "synthetic" | "test" | "unknown";
    timestamp: number;
    score: number;
  }>;
  candles: Candle[];
  candles_fetched_at: number | null;
  candles_expires_at: number | null;
  as_of: number;
  since: number;
  truncated: boolean;
}
export interface Alert {
  id: number;
  timestamp: number;
  chain: string;
  contract_address: string;
  symbol: string;
  score: number;
  preset?: string;
  revision?: number;
  observation: Observation | null;
}
export interface AlertDetail extends Alert {
  latest_observation?: Observation | null;
  outcomes?: Array<{
    horizon_hours?: number;
    hours?: number;
    return_pct?: number | null;
    observed_at?: number | null;
  }>;
}
export interface ReportEntry {
  observations?: number;
  chain?: string;
  contract_address?: string;
  symbol?: string;
  peak: Observation;
  latest: Observation;
  [key: string]: unknown;
}
export interface Report {
  since: number;
  until: number;
  timezone: string;
  hours: number;
  chain: string;
  evaluations: number;
  unique_tokens: number;
  completed_scans: number;
  incomplete_scans: number;
  first_observation: number | null;
  last_finished: number | null;
  multiple_configurations: boolean;
  entries: ReportEntry[];
}
export interface OutcomeStats {
  observed: number;
  returns_available: number;
  positive: number;
  median_return_pct: number | null;
  pending: number;
  missed: number;
}
export interface Outcomes {
  since: number;
  until: number;
  horizons: Record<string, OutcomeStats>;
}
export interface Session {
  authenticated: boolean;
  csrf_token?: string;
}
export interface AdminStatus {
  worker: JsonRecord | null;
  health: JsonRecord;
  [key: string]: unknown;
}
export interface AdminSettings {
  settings: JsonRecord;
  revision: number;
  active_revision: number | null;
  presets: Record<string, JsonRecord> | string[];
}
export interface Command {
  id: string;
  action: string;
  status: "queued" | "running" | "succeeded" | "failed" | "unknown";
  result?: JsonRecord | null;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
  [key: string]: unknown;
}
