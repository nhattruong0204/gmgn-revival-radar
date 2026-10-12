// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Radar } from "./Radar";
import { TokenDetail, SignalEvidence } from "./TokenDetail";
import { Alerts } from "./Publications";
import { chartSegments } from "./chartData";
import { radarQuery } from "./format";
import { useData } from "./api";
import { ObservedChart } from "./Chart";
import type { Observation } from "./types";

const legacy: Observation = {
  id: 1,
  timestamp: 1800000000,
  chain: "sol",
  contract_address: "A".repeat(32),
  symbol: "LEGACY",
  score: 80,
  status: "WATCH",
  eligible: null,
  token: null,
  signal: null,
  blockers: [],
  missing_fields: ["saved_evidence"],
  warnings: ["Saved detail evidence unavailable"],
};
let paths: string[];
beforeEach(() => {
  paths = [];
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      paths.push(path);
      let body: unknown;
      if (path.includes("/history"))
        body = {
          snapshots: [],
          scores: [],
          candles: [],
          as_of: 1800000010,
          truncated: false,
        };
      else if (path.includes("/tokens/"))
        body = { observation: legacy, latest_market: null, links: {} };
      else if (path.includes("/alerts/"))
        body = {
          id: 1,
          symbol: "LEGACY",
          timestamp: legacy.timestamp,
          chain: "sol",
          contract_address: legacy.contract_address,
          observation: { ...legacy, status: "UNKNOWN" },
          outcomes: [],
        };
      else
        body = {
          items: [legacy],
          total: 1,
          page: 1,
          page_size: 25,
          last_scan_at: legacy.timestamp,
        };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("saved evidence contracts", () => {
  it("renders real API chain objects and preserves filter URLs", async () => {
    render(
      <Radar
        search=""
        meta={{
          chains: [{ id: "sol", name: "Solana" }],
          as_of: 1800000010,
          last_scan_at: legacy.timestamp,
        }}
      />,
    );
    await screen.findAllByText("LEGACY");
    expect(
      screen.getByRole("option", { name: "Solana" }).getAttribute("value"),
    ).toBe("sol");
    await userEvent.selectOptions(
      screen.getByLabelText("Filter stage"),
      "REVIVING",
    );
    expect(window.location.search).toContain("status=REVIVING");
    expect(paths[0]).toContain("min_score=40");
  });

  it("opens legacy details without fabricating missing token or signal evidence", async () => {
    render(<TokenDetail chain="sol" address={legacy.contract_address} />);
    await screen.findByRole("heading", { name: "LEGACY" });
    expect(screen.getByText("Saved detail evidence unavailable")).toBeTruthy();
    expect(screen.getByText(/Chart structure was unavailable/)).toBeTruthy();
    expect(screen.getByText(/No saved observed price history/i)).toBeTruthy();
    expect(screen.queryByText(/did not pass strategy filters/)).toBeNull();
  });

  it("keeps legacy alert stages unknown", async () => {
    render(<Alerts id="1" />);
    await screen.findByText("unknown");
    expect(screen.queryByText("Confirmed revival")).toBeNull();
  });

  it("escapes saved evidence and shows unknown security", () => {
    render(
      <SignalEvidence
        observation={{ ...legacy, warnings: ["<script>alert(1)</script>"] }}
      />,
    );
    expect(screen.getByText("<script>alert(1)</script>")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(1);
  });
});

it("bounds integer query values accepted by the API", () => {
  const q = radarQuery("?page=Infinity&min_score=42.9");
  expect(q.get("page")).toBe("10000");
  expect(q.get("min_score")).toBe("42");
  expect(radarQuery("?status=IGNORE").get("min_score")).toBe("0");
});

it("leaves null measurements and missing intervals unconnected", () => {
  const points = [
    { timestamp: 0, value: 1 },
    { timestamp: 300, value: null },
    { timestamp: 600, value: 2 },
    { timestamp: 900, value: 3 },
    { timestamp: 3600, value: 4 },
  ];
  expect(chartSegments(points).map((s) => s.map((p) => p.timestamp))).toEqual([
    [0],
    [600, 900],
    [3600],
  ]);
});

it("does not let an older visibility refresh replace newer data", async () => {
  const pending: Array<(value: Response) => void> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>((resolve) => pending.push(resolve))),
  );
  function View() {
    const state = useData<{ version: string }>("/api/v1/meta");
    return <div>{state.loading ? "loading" : state.data?.version}</div>;
  }
  render(<View />);
  await waitFor(() => expect(pending.length).toBe(1));
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() => expect(pending.length).toBe(2));
  await act(async () =>
    pending[1](new Response(JSON.stringify({ version: "newer" }))),
  );
  expect(screen.getByText("newer")).toBeTruthy();
  await act(async () =>
    pending[0](new Response(JSON.stringify({ version: "older" }))),
  );
  expect(screen.queryByText("older")).toBeNull();
  expect(screen.getByText("newer")).toBeTruthy();
});

it("includes low scores when selecting Below watch and shares mobile sorting", async () => {
  window.history.replaceState(null, "", "/");
  render(<Radar search="" meta={null} />);
  await userEvent.selectOptions(
    screen.getByLabelText("Filter stage"),
    "IGNORE",
  );
  expect(new URLSearchParams(window.location.search).get("min_score")).toBe(
    "0",
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Sort candidates"),
    "liquidity:asc",
  );
  expect(new URLSearchParams(window.location.search).get("sort")).toBe(
    "liquidity",
  );
  expect(new URLSearchParams(window.location.search).get("order")).toBe("asc");
});

it("exposes the exact contract and reports clipboard failures", async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("denied"),
  );
  render(<TokenDetail chain="sol" address={legacy.contract_address} />);
  await user.click(
    await screen.findByRole("button", {
      name: `Copy contract ${legacy.contract_address}`,
    }),
  );
  expect(screen.getByText(/Copy failed/)).toBeTruthy();
  expect(screen.getByText(legacy.contract_address)).toBeTruthy();
  expect(screen.getByText(/Saved evaluation observed/)).toBeTruthy();
});

it("lets keyboard users inspect actual chart points", () => {
  const { container } = render(
    <ObservedChart
      label="Saved score"
      score
      points={[
        { timestamp: 100, value: 20 },
        { timestamp: 400, value: 40 },
        { timestamp: 700, value: 70 },
      ]}
    />,
  );
  const chart = screen.getByRole("img");
  chart.focus();
  act(() =>
    chart.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Home", bubbles: true }),
    ),
  );
  expect(container.querySelector(".chart-legend strong")?.textContent).toBe(
    "20 / 100",
  );
  act(() =>
    chart.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
    ),
  );
  expect(container.querySelector(".chart-legend strong")?.textContent).toBe(
    "40 / 100",
  );
});

it("does not join sparse observations across a missing day", () => {
  expect(
    chartSegments([
      { timestamp: 0, value: 1 },
      { timestamp: 86400, value: 2 },
    ]),
  ).toHaveLength(2);
});

it("keeps high-score risk and saved metrics separate from a later snapshot", async () => {
  const observation: Observation = {
    ...legacy,
    score: 95,
    status: "CONFIRMED_REVIVAL",
    eligible: false,
    market_cap: 100,
    blockers: ["Active mint authority"],
    token: {
      ...legacy,
      market_cap: 100,
      security: {
        mint_renounced: false,
        freeze_renounced: null,
        dangerous: true,
      },
    },
    signal: {
      components: { risk_penalty: -5 },
      acceleration: { volume_acceleration_5m: 2 },
      returning_activity: true,
    },
  };
  const url = `https://gmgn.ai/sol/token/${legacy.contract_address}`;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify(
            path.includes("/history")
              ? { snapshots: [], scores: [], candles: [] }
              : {
                  observation,
                  latest_market: {
                    ...observation.token,
                    timestamp: legacy.timestamp + 60,
                    market_cap: 900,
                  },
                  links: { gmgn: url },
                },
          ),
        ),
    ),
  );
  const { container } = render(
    <TokenDetail chain="sol" address={legacy.contract_address} />,
  );
  await screen.findByText("Active mint authority");
  expect(screen.getByText("Present")).toBeTruthy();
  expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  expect(container.querySelector(".stats-grid")?.textContent).toContain("$100");
  expect(
    screen
      .getByRole("heading", { name: "Later market observation" })
      .closest("section")?.textContent,
  ).toContain("$900");
  expect(screen.getByRole("link", { name: "Gmgn" }).getAttribute("href")).toBe(
    url,
  );
  expect(screen.getByText("2×")).toBeTruthy();
  expect(screen.getByText("-5")).toBeTruthy();
});
