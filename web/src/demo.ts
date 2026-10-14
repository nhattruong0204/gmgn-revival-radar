import type {
  Alert,
  CallPerformance,
  History,
  ListResponse,
  Meta,
  Observation,
  Outcomes,
  Report,
  TokenResponse,
} from "./types";
const now = Math.floor(Date.now() / 1000);
const names = [
  ["WIF", "dogwifhat"],
  ["POPCAT", "Popcat"],
  ["BOME", "Book of Meme"],
  ["MEW", "cat in a dogs world"],
  ["BONK", "Bonk"],
  ["SLERF", "Slerf"],
  ["MYRO", "Myro"],
  ["MUMU", "Mumu the Bull"],
  ["PONKE", "Ponke"],
  ["SAMO", "Samoyedcoin"],
  ["FWOG", "Fwog"],
  ["MOODENG", "Moo Deng"],
];
const stages = [
  "CONFIRMED_REVIVAL",
  "EARLY_REVIVAL",
  "STRONG_REVIVAL",
  "WATCH",
  "EARLY_REVIVAL",
  "WATCH",
];
export const demoObservations: Observation[] = names.map(
  ([symbol, name], i) => {
    const token = {
      timestamp: now - 140 - i * 13,
      chain: "sol",
      contract_address: `Demo${String(i + 1).padStart(38, "1")}`,
      symbol,
      name,
      price: 0.023 * (13 - i),
      market_cap: 4250000 / (i + 1),
      liquidity: 340000 / (i + 1),
      drawdown_from_ath: 0.67 + i * 0.021,
      volume_5m: 93000 / (i + 1),
      volume_1h: 840000 / (i + 1),
      tx_5m: 432 - i * 24,
      holders: 8240 - i * 383,
      price_change_1h: 24 - i * 3.2,
      security: {
        bundler_ratio: 0.12,
        mint_renounced: true,
        freeze_renounced: i % 3 === 0 ? null : true,
        top10_ratio: 0.12 + i * 0.01,
        dangerous: false,
        flags: [],
      },
    };
    const components: Record<string, number> = {
      drawdown: 10,
      liquidity: 10,
      holders: 5,
      base: 8,
      volume_5m: 15,
      volume_1h: 10,
      transactions: 10,
      higher_low: 5,
      higher_high: 5,
      bundler_penalty: -5,
    };
    return {
      ...token,
      id: i + 1,
      score: 92 - i * 4,
      status: stages[i % 6],
      eligible: i !== 7,
      setup_score: 88 - i * 2,
      trigger_score: 91 - i * 4,
      confirmation_score: i % 3 === 0 ? 78 : null,
      blockers:
        i === 7
          ? ["no_base", "no_returning_activity", "score_below_threshold"]
          : [],
      missing_fields:
        i % 3 === 0 ? ["security.dev_ratio", "baseline_history"] : [],
      warnings: i % 3 === 0 ? ["Freeze authority evidence unavailable"] : [],
      preset: "Balanced",
      revision: 4,
      token,
      signal: {
        // Matches the current scorer: component strings only, no narrative.
        reasons: Object.entries(components).map(
          ([k, v]) => `${k}: ${v >= 0 ? "+" : ""}${v}`,
        ),
        components,
        acceleration: {
          volume_acceleration_5m: 3.4 - i * 0.12,
          volume_ratio_5m: 4.1 - i * 0.15,
          volume_acceleration_1h: 2.2 - i * 0.05,
          tx_acceleration_5m: 2.8 - i * 0.1,
        },
        structure: {
          available: true,
          base_detected: true,
          base_duration_hours: 72 - i * 2,
          breakout_detected: i % 3 === 0,
          retest_detected: false,
        },
      },
    };
  },
);
function list<T>(items: T[], query: URLSearchParams): ListResponse<T> {
  const page = Number(query.get("page") ?? 1),
    pageSize = Number(query.get("page_size") ?? 25);
  return {
    items: items.slice((page - 1) * pageSize, page * pageSize),
    total: items.length,
    page,
    page_size: pageSize,
    as_of: now,
    last_scan_at: now - 140,
  };
}
function history(observation: Observation): History {
  const snapshots = Array.from({ length: 72 }, (_, i) => ({
    ...observation.token!,
    timestamp: now - (72 - i) * 3600,
    price:
      (observation.price ?? 1) * (0.47 + i * 0.006 + Math.sin(i * 0.6) * 0.025),
    market_cap:
      (observation.market_cap ?? 1) *
      (0.47 + i * 0.006 + Math.sin(i * 0.6) * 0.025),
  }));
  return {
    snapshots,
    scores: snapshots.map((t, i) => ({
      ...observation,
      ...t,
      token: { ...observation.token!, ...t },
      score: Math.min(99, 34 + Math.floor(i * 0.65 + Math.sin(i * 0.4) * 5)),
    })),
    candles: snapshots.map((t, i) => ({
      timestamp: t.timestamp,
      open: (t.price ?? 1) * 0.98,
      close: t.price ?? 1,
      high: (t.price ?? 1) * 1.04,
      low: (t.price ?? 1) * 0.96,
      volume_usd: 3000 + i * 100,
    })),
    candles_fetched_at: now - 150,
    candles_expires_at: now + 3600,
    as_of: now,
    since: now - 604800,
    truncated: false,
  };
}
const H = 3600;
type Spec = {
  age: number;
  perf: Omit<
    CallPerformance,
    "elapsed_seconds" | "latest_observed_at" | "token_call_index"
  > & { latest_age: number | null; index: number | null };
  outcomes: Array<{ horizon_hours: number; return_pct: number }>;
};
const blank = {
  call_price: null,
  latest_price: null,
  max_gap_seconds: null,
  token_call_count: null,
  index: null,
};
// Synthetic call performance: positive, negative, legacy-unknown, stale,
// no-later-observation and zero. Values are fixtures, not market data.
const specs: Spec[] = [
  {
    age: 30 * H,
    perf: {
      ...blank,
      call_market_cap: 3000000,
      latest_market_cap: 4200000,
      latest_age: 240,
      freshness: "fresh",
      change_since_call_pct: 40,
      change_reason: null,
      mfe_pct: 52,
      mae_pct: -6.5,
      observations: 61,
      max_gap_seconds: 1800,
      token_call_count: 3,
      index: 2,
    },
    outcomes: [
      { horizon_hours: 1, return_pct: 4.2 },
      { horizon_hours: 6, return_pct: 18.5 },
      { horizon_hours: 24, return_pct: 31 },
    ],
  },
  {
    age: 7 * H,
    perf: {
      ...blank,
      call_market_cap: 1200000,
      latest_market_cap: 900000,
      latest_age: 420,
      freshness: "fresh",
      change_since_call_pct: -25,
      change_reason: null,
      mfe_pct: -3.1,
      mae_pct: -31,
      observations: 24,
      max_gap_seconds: 900,
    },
    outcomes: [
      { horizon_hours: 1, return_pct: -3.1 },
      { horizon_hours: 6, return_pct: -22 },
    ],
  },
  {
    age: 50 * 60,
    perf: {
      ...blank,
      call_market_cap: null,
      latest_market_cap: 2100000,
      latest_age: 300,
      freshness: "fresh",
      change_since_call_pct: null,
      change_reason: "no_call_mc",
      mfe_pct: null,
      mae_pct: null,
      observations: 5,
      max_gap_seconds: 600,
    },
    outcomes: [],
  },
  {
    age: 5 * 86400,
    perf: {
      ...blank,
      call_market_cap: 800000,
      latest_market_cap: 640000,
      latest_age: 9 * H,
      freshness: "stale",
      change_since_call_pct: -20,
      change_reason: null,
      mfe_pct: 12,
      mae_pct: -33,
      observations: 18,
      max_gap_seconds: 21600,
    },
    outcomes: [
      { horizon_hours: 1, return_pct: 2 },
      { horizon_hours: 6, return_pct: -8 },
    ],
  },
  {
    age: 2 * H,
    perf: {
      ...blank,
      call_market_cap: 5000000,
      latest_market_cap: null,
      latest_age: null,
      freshness: "none",
      change_since_call_pct: null,
      change_reason: "no_later_observation",
      mfe_pct: null,
      mae_pct: null,
      observations: 0,
    },
    outcomes: [],
  },
  {
    age: 3 * H,
    perf: {
      ...blank,
      call_market_cap: 700000,
      latest_market_cap: 700000,
      latest_age: 600,
      freshness: "fresh",
      change_since_call_pct: 0,
      change_reason: null,
      mfe_pct: 0,
      mae_pct: 0,
      observations: 1,
      max_gap_seconds: 10800,
    },
    outcomes: [],
  },
];
const alertOutcomes = new Map<number, Spec["outcomes"]>();
const alerts: Alert[] = demoObservations
  .filter((_, i) => i < specs.length)
  .map((o, i) => {
    const { perf, age } = specs[i];
    const { latest_age, index, ...rest } = perf;
    alertOutcomes.set(i + 1, specs[i].outcomes);
    return {
      id: i + 1,
      timestamp: now - age,
      chain: o.chain,
      contract_address: o.contract_address,
      symbol: o.symbol,
      score: o.score,
      preset: o.preset ?? undefined,
      revision: 4,
      observation: o,
      performance: {
        ...rest,
        latest_observed_at: latest_age == null ? null : now - latest_age,
        elapsed_seconds: age,
        token_call_index: index,
      },
    };
  });
// A token with three calls (#67): the grouped Signals view needs a repeat caller.
// The existing first call is "2 of 3"; these are the first and the third.
{
  const base = alerts[0];
  const extra = (
    id: number,
    age: number,
    perf: Partial<CallPerformance>,
  ): Alert => ({
    ...base,
    id,
    timestamp: now - age,
    score: base.score - (id === 7 ? 8 : 0),
    performance: {
      ...base.performance!,
      elapsed_seconds: age,
      ...perf,
    },
  });
  alerts.push(
    extra(7, 80 * H, {
      call_market_cap: 2500000,
      change_since_call_pct: 68,
      mfe_pct: 80,
      mae_pct: -4,
      observations: 120,
      token_call_index: 1,
    }),
    extra(8, 4 * H, {
      call_market_cap: 4000000,
      change_since_call_pct: 5,
      mfe_pct: 7,
      mae_pct: -1.5,
      observations: 9,
      token_call_index: 3,
    }),
  );
  alertOutcomes.set(7, [
    { horizon_hours: 1, return_pct: 6 },
    { horizon_hours: 6, return_pct: 21 },
    { horizon_hours: 24, return_pct: 44 },
    { horizon_hours: 72, return_pct: 61 },
  ]);
  alertOutcomes.set(8, []);
}
const newestFirst = [...alerts].sort(
  (a, b) => b.timestamp - a.timestamp || b.id - a.id,
);
function groupedAlerts(q: URLSearchParams): ListResponse<Alert> {
  const order: string[] = [];
  const by = new Map<string, Alert[]>();
  for (const a of newestFirst) {
    const key = `${a.chain}:${a.contract_address}`;
    if (!by.has(key)) {
      by.set(key, []);
      order.push(key);
    }
    by.get(key)!.push(a);
  }
  const page = Number(q.get("page") ?? 1),
    size = Number(q.get("page_size") ?? 10);
  const keys = order.slice((page - 1) * size, page * size);
  return {
    items: keys.flatMap((k) => by.get(k)!),
    groups: keys.map((k) => {
      const calls = by.get(k)!;
      return {
        chain: calls[0].chain,
        contract_address: calls[0].contract_address,
        symbol: calls[0].symbol,
        call_count: calls.length,
        latest_call_at: calls[0].timestamp,
        alert_ids: calls.map((a) => a.id),
        truncated: false,
      };
    }),
    total: order.length,
    page,
    page_size: size,
    as_of: now,
    last_scan_at: now - 140,
  };
}
function readyRequest(path: string): unknown {
  const url = new URL(path, "https://demo.local"),
    q = url.searchParams;
  if (url.pathname.endsWith("/meta"))
    return {
      chains: [{ id: "sol", name: "Solana" }],
      telegram_url: null,
      as_of: now,
      last_scan_at: now - 140,
    } satisfies Meta;
  if (url.pathname.endsWith("/radar")) {
    let items = demoObservations.filter(
      (o) =>
        (!q.get("search") ||
          `${o.symbol} ${o.name} ${o.contract_address}`
            .toLowerCase()
            .includes(q.get("search")!.toLowerCase())) &&
        (!q.get("chain") || o.chain === q.get("chain")) &&
        (!q.get("status") || o.status === q.get("status")) &&
        o.score >= Number(q.get("min_score") ?? 0),
    );
    const sort = q.get("sort") ?? "score";
    items = items.sort(
      (a, b) =>
        (Number(a[sort as keyof Observation] ?? 0) -
          Number(b[sort as keyof Observation] ?? 0)) *
        (q.get("order") === "asc" ? 1 : -1),
    );
    return list(items, q);
  }
  if (url.pathname.endsWith("/track-record"))
    return {
      methodology_version: "checkpoint-cohort-v1",
      since: now - 7 * 86400,
      as_of: now,
      filters: {},
      definition:
        "Synthetic preview has no confirmed sent alerts; no returns are fabricated.",
      exclusions: ["No real observations in this fixture preview"],
      limitations: ["Not trading performance"],
      summary: {
        total: 0,
        observed: 0,
        returns_available: 0,
        positive: 0,
        negative: 0,
        zero: 0,
        missing_price: 0,
        unresolved: 0,
        pending: 0,
        missed: 0,
        median_return_pct: null,
        positive_rate_pct: null,
        small_sample: true,
        comparable: true,
        distribution: [],
      },
      cohorts: [],
      items: [],
      page: 1,
      page_size: 25,
      total: 0,
    };
  if (url.pathname.endsWith("/briefs"))
    return { items: [], total: 0, page: 1, page_size: 10, as_of: now };
  if (url.pathname.includes("/tokens/")) {
    const address = url.pathname.split("/")[5];
    const o = demoObservations.find((v) => v.contract_address === address);
    if (!o) throw new Error("Token not found in the synthetic demo.");
    if (url.pathname.endsWith("/history")) return history(o);
    return {
      observation: o,
      latest_market: o.token,
      as_of: now,
      last_scan_at: now - 140,
      links: {},
    } satisfies TokenResponse;
  }
  if (url.pathname.includes("/alerts/")) {
    const a = alerts.find(
      (v) => v.id === Number(url.pathname.split("/").at(-1)),
    );
    if (!a) throw new Error("Alert not found in the synthetic demo.");
    return {
      ...a,
      latest_observation: a?.observation,
      outcomes: (alertOutcomes.get(a.id) ?? []).map((v) => ({
        ...v,
        observed_at: a.timestamp + v.horizon_hours * H,
      })),
    };
  }
  if (url.pathname.endsWith("/alerts"))
    return q.get("group") === "token" ? groupedAlerts(q) : list(newestFirst, q);
  if (url.pathname.endsWith("/reports"))
    return {
      since: now - Number(q.get("hours") ?? 24) * 3600,
      until: now,
      timezone: "UTC",
      hours: Number(q.get("hours") ?? 24),
      chain: "sol",
      evaluations: 812,
      unique_tokens: 148,
      completed_scans: 24,
      incomplete_scans: 0,
      first_observation: now - 86400,
      last_finished: now - 140,
      multiple_configurations: false,
      market_context: {
        scan_finished: now - 140,
        chain: "sol",
        sources: ["hot_search"],
        universe_size: 20,
        median_price_change_1h: -2.1,
        median_price_change_24h: -14.8,
        share_positive_1h: 0.3,
        eligible_count: 1,
        coverage: { price_change_1h: 20, price_change_24h: 19 },
        minimum_sample: 5,
      },
      entries: demoObservations.slice(0, 10).map((o) => ({
        symbol: o.symbol,
        observations: 6,
        peak: o,
        latest: { ...o, score: o.score - 3 },
      })),
    } satisfies Report;
  if (url.pathname.endsWith("/outcomes"))
    return {
      since: now - 604800,
      until: now,
      horizons: Object.fromEntries(
        [1, 6, 24, 72].map((h, i) => [
          String(h),
          {
            observed: 32 - i * 5,
            returns_available: 32 - i * 5,
            positive: 20 - i * 3,
            median_return_pct: 4.2 + i * 3.1,
            pending: i * 3,
            missed: 2 + i,
          },
        ]),
      ),
    } satisfies Outcomes;
  throw new Error("This endpoint is unavailable in the synthetic demo.");
}

export const previewStates = [
  "ready",
  "loading",
  "empty",
  "partial",
  "stale",
  "error",
] as const;
export type PreviewState = (typeof previewStates)[number];
export function previewState(value: string | null): PreviewState {
  return previewStates.includes(value as PreviewState)
    ? (value as PreviewState)
    : "ready";
}
export function demoRequest(
  path: string,
  state: PreviewState = "ready",
): unknown {
  if (state === "error")
    throw new Error("Synthetic preview: the data service is unavailable.");
  const data = structuredClone(readyRequest(path));
  if (state === "empty") {
    if (path.includes("/tokens/")) {
      if (path.includes("/history"))
        return { ...(data as History), snapshots: [], scores: [], candles: [] };
      throw new Error("Synthetic preview: no saved evaluation for this token.");
    }
    if (path.includes("/alerts/"))
      throw new Error("Synthetic preview: no public alert is available.");
    if (path.includes("/reports"))
      return {
        ...(data as Report),
        market_context: null,
        entries: [],
        evaluations: 0,
        unique_tokens: 0,
        completed_scans: 0,
        incomplete_scans: 0,
        last_finished: null,
      };
    if (path.includes("/outcomes"))
      return {
        ...(data as Outcomes),
        horizons: Object.fromEntries(
          [1, 6, 24, 72].map((h) => [
            String(h),
            {
              observed: 0,
              returns_available: 0,
              positive: 0,
              median_return_pct: null,
              pending: 0,
              missed: 0,
            },
          ]),
        ),
      };
    if (path.includes("/radar") || path.includes("/alerts"))
      return { ...(data as ListResponse<Observation>), items: [], total: 0 };
  }
  if (state === "partial" && path.includes("/reports")) {
    // Undersampled regime: unknown values stay unknown, never zero.
    const report = data as Report;
    return {
      ...report,
      market_context: report.market_context && {
        ...report.market_context,
        universe_size: 3,
        median_price_change_1h: null,
        median_price_change_1h_reason: "insufficient_sample",
        median_price_change_24h: null,
        median_price_change_24h_reason: "insufficient_sample",
        share_positive_1h: null,
        share_positive_1h_reason: "insufficient_sample",
        eligible_count: null,
        coverage: { price_change_1h: 3, price_change_24h: 2 },
      },
    };
  }
  if (state === "partial" && path.includes("/history")) {
    return { ...(data as History), candles: [] };
  }
  const transform = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(transform);
    if (!value || typeof value !== "object") return value;
    const record = value as Record<string, unknown>;
    if (state === "partial" && "blockers" in record && "token" in record) {
      const observation = record as unknown as Observation;
      observation.eligible = false;
      observation.liquidity = null;
      observation.confirmation_score = null;
      observation.blockers = ["Known active mint authority blocks eligibility"];
      observation.missing_fields = [
        "liquidity",
        "freeze_authority",
        "closed_candles",
      ];
      observation.warnings = ["Security coverage is incomplete"];
      if (observation.token)
        observation.token = {
          ...observation.token,
          liquidity: null,
          security: {
            mint_renounced: false,
            freeze_renounced: null,
            dangerous: true,
          },
        };
      if (observation.signal)
        observation.signal = {
          ...observation.signal,
          structure: { available: false },
        };
    }
    return Object.fromEntries(
      Object.entries(record).map(([key, item]) => [
        key,
        state === "stale" &&
        ["timestamp", "last_scan_at"].includes(key) &&
        typeof item === "number"
          ? item - 7200
          : transform(item),
      ]),
    );
  };
  return transform(data);
}
