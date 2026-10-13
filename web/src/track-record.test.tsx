// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CheckpointTable, TrackRecordPage, trackQuery } from "./TrackRecord";
import type { CheckpointResearch, TrackRecordData } from "./types";

const row: CheckpointResearch = {
  alert_id: 7,
  timestamp: 1800000000,
  chain: "sol",
  contract_address: "A".repeat(32),
  symbol: "<img src=x onerror=alert(1)>",
  score: 90,
  stage: "REVIVING",
  preset: "Balanced",
  revision: 4,
  score_version: "v2",
  origin: "unknown",
  knowledge: "legacy_unknown",
  saved_at: null,
  sent_at: null,
  cohort_id: "a".repeat(16),
  horizon_hours: 1,
  due_at: 1800003600,
  window_end: 1800007200,
  observed_at: 1800004000,
  recorded_at: null,
  checkpoint_origin: "unknown",
  initial_price: 1,
  checkpoint_price: 0.5,
  checkpoint_market_cap: null,
  reported_return_pct: -50,
  return_pct: -50,
  state: "return",
  reason: "Observed checkpoint price change",
  brief_id: null,
  source: "alert:7/checkpoint:1",
};
const data: TrackRecordData = {
  methodology_version: "checkpoint-cohort-v1",
  since: 1799990000,
  as_of: 1800010000,
  filters: {},
  definition: "Saved original alerts only",
  exclusions: ["Missing returns excluded from rates"],
  limitations: ["Not realized P&L"],
  summary: {
    total: 3,
    observed: 1,
    returns_available: 1,
    positive: 0,
    negative: 1,
    zero: 0,
    missing_price: 0,
    unresolved: 0,
    pending: 1,
    missed: 1,
    median_return_pct: -50,
    positive_rate_pct: 0,
    small_sample: true,
    comparable: true,
    distribution: [{ label: "-50% to below 0%", count: 1 }],
  },
  cohorts: [],
  items: [row],
  page: 1,
  page_size: 25,
  total: 3,
};
beforeEach(() => {
  window.history.replaceState(null, "", "/outcomes?days=7&horizon=1");
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(data))),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("normalizes malformed URL filters without inventing an as-of time", () => {
  const query = trackQuery(
    "days=bad&horizon=2&page=-1&chain=unsafe&as_of=NaN&revision=10000000000",
  );
  expect(query.get("days")).toBe("7");
  expect(query.get("horizon")).toBe("24");
  expect(query.get("page")).toBe("1");
  expect(query.has("as_of")).toBe(false);
  expect(query.has("chain")).toBe(false);
  expect(query.has("revision")).toBe(false);
});
it("opens filters with focus isolation, resets pagination, preserves context and returns focus", async () => {
  window.history.replaceState(null, "", "/outcomes?days=7&horizon=1&page=3");
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByText("Computable returns");
  const trigger = screen.getByRole("button", { name: "Cohort filters" });
  await user.click(trigger);
  expect(screen.getByRole("dialog")).toBeTruthy();
  await user.selectOptions(
    screen.getByLabelText("Alert-time strategy"),
    "Balanced",
  );
  await waitFor(() =>
    expect(window.location.search).toContain("preset=Balanced"),
  );
  expect(window.location.search).toContain("page=1");
  expect(window.location.search).toContain("horizon=1");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
it("shows negatives and uncertainty, preserves original drill-down context and escapes hostile text", async () => {
  const { container } = render(<TrackRecordPage />);
  await screen.findByText(row.symbol);
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("Pending: 1 · Missed: 1");
  expect(container.textContent).toContain("Small sample: 1");
  const link = screen.getByRole("link", { name: "Alert evidence" });
  expect(link.getAttribute("href")).toContain(
    "return_to=%2Foutcomes%3Fdays%3D7%26horizon%3D1",
  );
  expect(
    container.querySelector(".checkpoint-table .danger")?.textContent,
  ).toContain("-50");
});
it("withholds mixed-cohort rates and applies a visible retrospective distribution filter", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            ...data,
            summary: {
              ...data.summary,
              comparable: false,
              median_return_pct: null,
              positive_rate_pct: null,
            },
          }),
        ),
    ),
  );
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByText(/Rates and medians are withheld/);
  await user.click(screen.getByRole("button", { name: /-50% to below 0%/ }));
  expect(window.location.search).toContain("bucket=0");
  expect(window.location.search).toContain("page=1");
});
it("announces export failure and leaves the saved cohort visible", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      String(path).includes("/export")
        ? new Response("unavailable", { status: 503 })
        : new Response(JSON.stringify(data)),
    ),
  );
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByText(row.symbol);
  await user.click(
    screen.getByRole("button", { name: "Export complete cohort (UTC CSV)" }),
  );
  await screen.findByRole("alert");
  expect(screen.getByText(row.symbol)).toBeTruthy();
  const exportCall = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).includes("/export"));
  expect(String(exportCall?.[0])).toContain("as_of=1800010000");
});

it("keeps a missing checkpoint distinct from an actual zero return", () => {
  render(
    <CheckpointTable
      items={[
        {
          ...row,
          return_pct: null,
          reported_return_pct: null,
          state: "missed",
          observed_at: null,
          checkpoint_price: null,
          reason: "No eligible checkpoint",
        },
        {
          ...row,
          alert_id: 8,
          return_pct: 0,
          reported_return_pct: 0,
          checkpoint_price: 1,
        },
      ]}
    />,
  );
  expect(screen.getByText("Missed")).toBeTruthy();
  expect(screen.getByText("0%", { exact: true })).toBeTruthy();
});
