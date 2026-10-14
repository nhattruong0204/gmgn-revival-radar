// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Alerts, Reports, OutcomePage, Methodology } from "./Publications";
import { App } from "./App";
import { explainText } from "./explain";
import { demoObservations, demoRequest } from "./demo";
import type { Report } from "./types";

const peak = {
  ...demoObservations[0],
  score: 90,
  market_cap: 100,
  blockers: ["Peak risk"],
  missing_fields: ["Peak liquidity"],
};
const latest = {
  ...peak,
  id: 2,
  score: 20,
  market_cap: 900,
  timestamp: peak.timestamp + 30,
  blockers: ["Latest risk"],
  missing_fields: ["Latest candles"],
};
beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("scrollTo", vi.fn());
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function respond(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

it("shows call performance at the top and keeps the original evidence", async () => {
  respond({
    id: 1,
    symbol: "SAMPLE",
    score: 90,
    timestamp: peak.timestamp,
    chain: "sol",
    contract_address: peak.contract_address,
    preset: "Balanced",
    revision: 4,
    observation: peak,
    latest_observation: latest,
    outcomes: [],
    performance: {
      call_market_cap: 100,
      call_price: null,
      latest_market_cap: 900,
      latest_price: null,
      latest_observed_at: latest.timestamp,
      freshness: "stale",
      change_since_call_pct: 800,
      change_reason: null,
      mfe_pct: 900,
      mae_pct: -3,
      observations: 4,
      max_gap_seconds: 60,
      elapsed_seconds: 7200,
      token_call_index: null,
      token_call_count: null,
    },
  });
  const { container } = render(<Alerts id="1" />);
  await screen.findAllByText("Peak risk");
  const card = container.querySelector(".call-card");
  expect(card?.textContent).toContain("$100");
  expect(card?.textContent).toContain("$900");
  expect(card?.textContent).toContain("+800.0%");
  // The separate alert-time market cap stat and latest-evaluation panel are gone.
  expect(screen.queryByText("Alert-time market cap")).toBeNull();
  expect(
    screen.queryByRole("heading", { name: "Latest saved evaluation" }),
  ).toBeNull();
  expect(container.querySelector(".stats-grid")?.textContent).toContain(
    "Alert-time liquidity",
  );
});
it("renders the Signals list as compact call cards with performance", async () => {
  respond(demoRequest("/api/v1/alerts"));
  const { container } = render(<Alerts />);
  await screen.findByRole("heading", { name: "Sent signals" });
  await screen.findAllByText(/since call/);
  expect(
    container.querySelectorAll(".call-card-compact").length,
  ).toBeGreaterThan(3);
  expect(container.querySelector(".call-card-checkpoints")).toBeNull();
});
it("shows an unavailable alert rather than silently falling back to the list", async () => {
  respond({ detail: "Saved record not found" }, 404);
  render(<Alerts id="12" />);
  await screen.findByRole("heading", { name: "Alert evidence unavailable" });
  expect(screen.queryByRole("heading", { name: "Sent signals" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(1);
});
it("opens distinct peak/latest risks and preserves report windows and coverage", async () => {
  const report = demoRequest("/api/v1/reports") as Report;
  respond({
    ...report,
    multiple_configurations: true,
    incomplete_scans: 2,
    entries: [
      {
        chain: "sol",
        contract_address: peak.contract_address,
        symbol: "SAMPLE",
        peak,
        latest,
        observations: 2,
      },
    ],
  });
  render(<Reports />);
  await screen.findByText(/multiple strategy configurations/);
  await userEvent.click(screen.getByText(/Peak and latest evidence/));
  expect(screen.getByText("Peak risk")).toBeTruthy();
  expect(screen.getByText("Latest risk")).toBeTruthy();
  expect(screen.getByText("Peak liquidity")).toBeTruthy();
  expect(screen.getByText("Latest candles")).toBeTruthy();
  expect(screen.getByText(/Sparse sample/)).toBeTruthy();
  expect(screen.getByText("2 incomplete scans excluded")).toBeTruthy();
  for (const window of ["1h", "4h", "12h", "24h", "72h", "7d"]) {
    await userEvent.click(screen.getByRole("button", { name: window }));
  }
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) => String(url).includes("hours=168")),
  ).toBe(true);
});
it("uses the known-return denominator and keeps null medians unknown", async () => {
  respond({
    methodology_version: "checkpoint-cohort-v1",
    since: 100,
    as_of: 200,
    filters: {},
    definition: "Saved delivered alerts",
    exclusions: [],
    limitations: [],
    cohorts: [],
    items: [],
    page: 2,
    page_size: 25,
    total: 12,
    summary: {
      total: 12,
      observed: 3,
      returns_available: 2,
      positive: 1,
      negative: 1,
      zero: 0,
      missing_price: 1,
      unresolved: 0,
      pending: 4,
      missed: 5,
      median_return_pct: null,
      positive_rate_pct: 50,
      small_sample: true,
      comparable: true,
      distribution: [],
    },
  });
  const { container } = render(<OutcomePage />);
  await screen.findByText(/Positive 1 \/ 2/);
  expect(container.textContent).toContain("Positive-return rate: 50%");
  expect(container.textContent).toContain("Pending: 4 · Missed: 5");
  expect(container.textContent).not.toContain("+0.00%");
});
it("does not invent a zero denominator on a failed outcome request", async () => {
  respond({ detail: "Saved observations temporarily unavailable" }, 503);
  render(<OutcomePage />);
  await screen.findByRole("heading", { name: "Outcome data unavailable" });
  expect(screen.queryByText("(0 / 0)")).toBeNull();
  expect(screen.queryByText("(— / —)")).toBeNull();
  expect(screen.getByText(/No returns have been assumed/)).toBeTruthy();
});
it("explains all stages and the heuristic meaning of scores", () => {
  render(<Methodology />);
  for (const label of [
    "Below watch",
    "Watch",
    "Early revival",
    "Reviving",
    "Strong revival",
    "Confirmed revival",
  ])
    expect(screen.getByText(label, { exact: true })).toBeTruthy();
  expect(screen.getByText(/not calibrated probabilities/)).toBeTruthy();
});

it("keeps legacy alert detail unknown while preserving the original score", async () => {
  respond({
    id: 1,
    symbol: "LEGACY",
    score: 90,
    timestamp: peak.timestamp,
    chain: "sol",
    contract_address: peak.contract_address,
    observation: { ...peak, token: null, signal: null, market_cap: null },
    latest_observation: null,
    outcomes: [],
  });
  render(<Alerts id="1" />);
  await screen.findByText(/Original detail evidence is unavailable/);
  expect(screen.getByText("Original confirmed signal")).toBeTruthy();
  expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
});
it("uses the configured public Telegram destination in navigation", async () => {
  window.history.replaceState(null, "", "/how-it-works");
  respond({
    chains: [{ id: "sol", name: "Solana" }],
    as_of: 100,
    last_scan_at: 90,
    telegram_url: "https://t.me/example_public",
  });
  render(<App />);
  const link = await screen.findByRole("link", { name: "Open Telegram" });
  expect(link.getAttribute("href")).toBe("https://t.me/example_public");
  expect(link.getAttribute("rel")).toContain("noopener");
});
it("lists the risk warnings saved with an alert in the Risk panel", async () => {
  const saved = [
    "Insider holdings unavailable",
    "Token-2022 extension risks not assessed: metadataPointer",
    "bundler penalty",
  ];
  respond({
    id: 1,
    symbol: "SAMPLE",
    score: 58,
    timestamp: peak.timestamp,
    chain: "sol",
    contract_address: peak.contract_address,
    preset: "Custom",
    revision: 10,
    observation: { ...peak, blockers: [], warnings: saved },
    latest_observation: null,
    outcomes: [],
  });
  render(<Alerts id="1" />);
  const panel = (
    await screen.findByRole("heading", { name: "Risk & data coverage" })
  ).closest("section");
  // Plain-language rendering (#68): "bundler penalty" is replaced by the
  // sentence built from the matching scorer component, not shown raw.
  const text = panel?.textContent ?? "";
  expect(text).toContain("Bundled wallets hold 12% of supply");
  expect(text).not.toContain("bundler penalty");
  for (const warning of saved.filter((w) => w !== "bundler penalty"))
    expect(text).toContain(explainText(warning));
  expect(text).toContain("Insider holdings unavailable");
  expect(text).toContain(
    "Token-2022 extension risks not assessed: metadataPointer",
  );
});
