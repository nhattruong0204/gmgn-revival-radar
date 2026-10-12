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
