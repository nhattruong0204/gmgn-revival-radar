import type {
  Alert,
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
        mint_renounced: true,
        freeze_renounced: i % 3 === 0 ? null : true,
        top10_ratio: 0.12 + i * 0.01,
        dangerous: false,
        flags: [],
      },
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
      blockers: i === 7 ? ["Insufficient liquidity for current strategy"] : [],
      missing_fields: i % 3 === 0 ? ["freeze_authority"] : [],
      warnings: i % 3 === 0 ? ["Freeze authority evidence unavailable"] : [],
      preset: "Balanced",
      revision: 4,
      token,
      signal: {
        reasons: [
          "Returning 5m volume above observed baseline",
          "Established base after a deep ATH drawdown",
          "Higher lows in cached closed candles",
        ],
        components: { base: 24, volume_5m: 18, transactions: 12, drawdown: 15 },
        acceleration: {
          volume_acceleration_5m: 3.4 - i * 0.12,
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
const alerts: Alert[] = demoObservations
  .filter((_, i) => i < 5)
  .map((o, i) => ({
    id: i + 1,
    timestamp: now - i * 3700,
    chain: o.chain,
    contract_address: o.contract_address,
    symbol: o.symbol,
    score: o.score,
    preset: o.preset ?? undefined,
    revision: 4,
    observation: o,
  }));
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
      outcomes: [
        { horizon_hours: 1, return_pct: 4.2, observed_at: now - 3600 },
        { horizon_hours: 6, return_pct: 9.8, observed_at: now - 1800 },
      ],
    };
  }
  if (url.pathname.endsWith("/alerts")) return list(alerts, q);
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
