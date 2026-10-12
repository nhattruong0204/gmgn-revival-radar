// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Alerts, Reports, OutcomePage, Methodology } from "./Publications";
import { App } from "./App";
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
beforeEach(() =>
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  }),
);
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

it("retains original alert metrics and labels the newer evaluation separately", async () => {
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
  });
  const { container } = render(<Alerts id="1" />);
  await screen.findByText("Peak risk");
  expect(container.querySelector(".stats-grid")?.textContent).toContain("$100");
  expect(
    screen
      .getByRole("heading", { name: "Latest saved evaluation" })
      .closest("section")?.textContent,
  ).toContain("$900");
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
    since: 100,
    until: 200,
    horizons: Object.fromEntries(
      [1, 6, 24, 72].map((h) => [
        String(h),
        {
          observed: 3,
          returns_available: 2,
          positive: 1,
          median_return_pct: null,
          pending: 4,
          missed: 5,
        },
      ]),
    ),
  });
  const { container } = render(<OutcomePage />);
  await screen.findAllByText("50%");
  expect(screen.getAllByText("(1 / 2)")).toHaveLength(4);
  expect(container.querySelector(".outcome-value span")?.className).toBe(
    "muted",
  );
  expect(screen.getAllByText("5")).toHaveLength(4);
});
it("does not invent a zero denominator on a failed outcome request", async () => {
  respond({ detail: "Saved observations temporarily unavailable" }, 503);
  render(<OutcomePage />);
  await screen.findByRole("heading", { name: "Outcome data unavailable" });
  expect(screen.queryByText("(0 / 0)")).toBeNull();
  expect(screen.getAllByText("(— / —)")).toHaveLength(4);
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
