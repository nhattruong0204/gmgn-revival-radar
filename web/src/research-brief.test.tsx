// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { briefDiff, ResearchBrief } from "./ResearchBrief";
import type { ResearchBrief as Brief } from "./types";

const brief: Brief = {
  id: "version-one",
  brief_version: "brief-v1",
  evaluation_id: 1,
  scan_id: 1,
  chain: "sol",
  contract_address: "A".repeat(32),
  as_of: 1800000000,
  saved_at: 1800000001,
  origin: "live",
  strategy: "Balanced",
  strategy_revision: 4,
  score_version: "saved-v1",
  outcome: "watch",
  support: [],
  counter_evidence: [
    {
      key: "blocker",
      text: "Mint authority present",
      source_id: "evaluation:1",
      field: "blocker",
      value: "Mint authority present",
    },
  ],
  unresolved: [
    {
      key: "volume",
      text: "Volume unavailable",
      source_id: "evaluation:1",
      field: "volume",
      value: null,
    },
  ],
  scenarios: [
    {
      kind: "watch",
      text: "Wait for complete evidence",
      source_id: "evaluation:1",
      field: "volume",
      level: null,
    },
  ],
  sources: [
    {
      id: "evaluation:1",
      kind: "evaluation",
      observed_at: 1800000000,
      fetched_at: null,
      expires_at: null,
      origin: "live",
      coverage: "Saved evaluation",
      values: { volume: null, blocker: "Mint authority present" },
      candles: [],
    },
  ],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("compares recorded facts and missingness without replacing either version", () => {
  const newer = {
    ...brief,
    id: "version-two",
    strategy_revision: 5,
    unresolved: [],
  };
  const before = JSON.stringify(brief);
  const changes = briefDiff(brief, newer);
  expect(
    changes.some(
      (d) => d.key === "Strategy revision" && d.before === 4 && d.after === 5,
    ),
  ).toBe(true);
  expect(
    changes.some(
      (d) => d.key === "unresolved: volume" && d.after === "Not recorded",
    ),
  ).toBe(true);
  expect(JSON.stringify(brief)).toBe(before);
});

it("shows contradictory and missing evidence together and opens keyboard-accessible sources", async () => {
  const newer = { ...brief, id: "version-two", strategy_revision: 5 };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            items: [newer, brief],
            total: 2,
            page: 1,
            page_size: 10,
            as_of: 1800000002,
          }),
        ),
    ),
  );
  const user = userEvent.setup();
  render(<ResearchBrief chain="sol" address={brief.contract_address} />);
  await screen.findByText("Mint authority present");
  expect(screen.getByText("Volume unavailable")).toBeTruthy();
  await user.selectOptions(screen.getByLabelText("Compare with"), brief.id);
  expect(screen.getByRole("region", { name: "Version changes" })).toBeTruthy();
  await user.click(screen.getByRole("link", { name: "Source: blocker" }));
  expect(document.activeElement?.id).toContain("evaluation-1");
  expect(document.activeElement?.closest("details")?.open).toBe(true);
});

it("distinguishes legacy absence from a failed saved-data request", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            items: [],
            total: 0,
            page: 1,
            page_size: 10,
            as_of: 1800000000,
          }),
        ),
    ),
  );
  const first = render(
    <ResearchBrief chain="sol" address={brief.contract_address} />,
  );
  await screen.findByText("No immutable brief saved yet");
  first.unmount();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: "Saved briefs temporarily unavailable" }),
          { status: 503 },
        ),
    ),
  );
  render(<ResearchBrief chain="sol" address={brief.contract_address} />);
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Saved briefs temporarily unavailable",
    ),
  );
  expect(screen.getByRole("button", { name: "Retry briefs" })).toBeTruthy();
  expect(screen.queryByText("No immutable brief saved yet")).toBeNull();
});

it("compares stable versions across archive pages without losing the selected case", async () => {
  const newest = { ...brief, id: "version-newest", strategy_revision: 6 };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify({
            items: path.includes("page=2") ? [brief] : [newest],
            total: 11,
            page: path.includes("page=2") ? 2 : 1,
            page_size: 10,
            as_of: 1800000002,
          }),
        ),
    ),
  );
  const user = userEvent.setup();
  render(<ResearchBrief chain="sol" address={brief.contract_address} />);
  await screen.findByText("Mint authority present");
  await user.selectOptions(screen.getByLabelText("Saved version"), newest.id);
  await user.click(screen.getByRole("button", { name: "Older versions" }));
  await waitFor(() =>
    expect(screen.getByText("Page 2 · 11 versions")).toBeTruthy(),
  );
  await user.selectOptions(screen.getByLabelText("Compare with"), brief.id);
  expect(
    (screen.getByLabelText("Saved version") as HTMLSelectElement).value,
  ).toBe(newest.id);
  expect(screen.getByRole("region", { name: "Version changes" })).toBeTruthy();
});
