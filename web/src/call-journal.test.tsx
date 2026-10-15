// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { journalApiQuery, journalQuery } from "./CallJournal";
import { TrackRecordPage } from "./TrackRecord";
import { demoRequest } from "./demo";
import type { CallJournalData } from "./types";

let requests: string[] = [];
function journal(search = "") {
  return demoRequest(`/api/v1/track-record/calls?${search}`) as CallJournalData;
}
function respond(build: (path: string) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      requests.push(path);
      return new Response(JSON.stringify(build(path)));
    }),
  );
}
beforeEach(() => {
  requests = [];
  window.history.replaceState(null, "", "/outcomes");
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal("scrollTo", vi.fn());
  respond((path) => demoRequest(path));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("opens on the call journal with denominators for every count", async () => {
  render(<TrackRecordPage />);
  const summary = await screen.findByRole("region", { name: "Call summary" });
  const data = journal("days=7");
  expect(data.summary.calls).toBeGreaterThanOrEqual(10);
  const text = summary.textContent ?? "";
  expect(text).toContain(`${data.summary.measurable} / ${data.summary.calls}`);
  expect(text).toContain(
    `Up${data.summary.positive} / ${data.summary.measurable}`,
  );
  expect(text).toContain(
    `Down${data.summary.negative} / ${data.summary.measurable}`,
  );
  expect(text).toContain(
    `Unknown${data.summary.unknown} / ${data.summary.calls}`,
  );
  expect(text).toContain("Median change since call");
  expect(text).not.toContain("Withheld");
  const tab = screen.getByRole("link", { name: "Call journal" });
  expect(tab.getAttribute("aria-current")).toBe("page");
  expect(requests[0]).toContain("/api/v1/track-record/calls?");
  expect(requests[0]).toContain("page_size=25");
});

it("explains unknown outcomes with reasons and never shows them as zero", async () => {
  render(<TrackRecordPage />);
  const summary = await screen.findByRole("region", { name: "Call summary" });
  expect(summary.textContent).toContain("market cap at call not saved");
  expect(summary.textContent).toContain("no later observation saved");
  const unknown = screen
    .getAllByText("Change unknown")
    .map((node) => node.closest("article")!);
  expect(unknown.length).toBe(journal("days=7").summary.unknown);
  for (const row of unknown) expect(row.textContent).not.toMatch(/0\.0%/);
});

it("pools revisions as mixed configurations instead of withholding", async () => {
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  const notice = screen.getByText(/Mixed configurations: revisions 4, 3/);
  expect(notice.textContent).toContain("not a comparison of strategies");
  expect(screen.queryByText(/withheld/i)).toBeNull();
});

it("warns on a small sample below 20 measurable calls", async () => {
  render(<TrackRecordPage />);
  expect(await screen.findByText(/Small sample:/)).toBeTruthy();
  expect(screen.getByText(/below 20/)).toBeTruthy();
});

it("keeps failures as visible as winners with a worst-first sort", async () => {
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  await user.selectOptions(screen.getByLabelText("Sort"), "worst");
  await waitFor(() => expect(window.location.search).toContain("sort=worst"));
  await waitFor(() => expect(requests.at(-1)).toContain("sort=worst"));
  const first = (await screen.findAllByRole("article"))[0];
  expect(first.textContent).toContain("−62.0%");
  expect(first.textContent).toContain("down since call");
  const distribution = screen.getByRole("list", {
    name: /Change since call, calls per bin/,
  });
  expect(within(distribution).getByText("Below -50%")).toBeTruthy();
});

it("groups by token while keeping every call visible", async () => {
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  const before = screen.getAllByRole("article").length;
  await user.click(screen.getByLabelText("Group by token"));
  await waitFor(() => expect(window.location.search).toContain("group=token"));
  await waitFor(() => expect(requests.at(-1)).toContain("page_size=100"));
  const heading = await screen.findByRole("heading", {
    name: /\$WIF.*3 calls/,
  });
  expect(heading).toBeTruthy();
  await waitFor(() =>
    expect(screen.getAllByRole("article").length).toBe(before),
  );
});

it("filters through the URL, resets to page 1 and shows an empty state", async () => {
  window.history.replaceState(null, "", "/outcomes?page=3");
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  await user.selectOptions(screen.getByLabelText("Chain"), "bsc");
  await waitFor(() => expect(window.location.search).toContain("chain=bsc"));
  expect(window.location.search).toContain("page=1");
  expect(await screen.findByText("No calls match these filters")).toBeTruthy();
  expect(screen.getByText(/Nothing has been estimated/)).toBeTruthy();
});

it("restricts to one revision for an exact cohort", async () => {
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  await user.selectOptions(screen.getByLabelText("Strategy revision"), "3");
  await waitFor(() => expect(requests.at(-1)).toContain("revision=3"));
  await waitFor(() =>
    expect(screen.queryByText(/Mixed configurations/)).toBeNull(),
  );
});

it("offers a dense table on desktop and keeps checkpoint detail behind a tab", async () => {
  const user = userEvent.setup();
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  await user.click(screen.getByRole("button", { name: "Dense table" }));
  await waitFor(() => expect(window.location.search).toContain("layout=table"));
  const table = await screen.findByRole("region", {
    name: "Calls, dense table",
  });
  expect(within(table).getAllByRole("row").length).toBeGreaterThan(10);
  expect(within(table).getAllByText("Unknown").length).toBeGreaterThan(0);
  await user.click(screen.getByRole("link", { name: "Checkpoint detail" }));
  await waitFor(() =>
    expect(window.location.search).toContain("view=checkpoints"),
  );
  expect(
    screen
      .getByRole("link", { name: "Checkpoint detail" })
      .getAttribute("aria-current"),
  ).toBe("page");
});

it("shows an error with retry and no invented figures when the service fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "Saved calls unavailable" }), {
          status: 503,
        }),
    ),
  );
  render(<TrackRecordPage />);
  expect(await screen.findByText("Saved calls unavailable")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "Call journal unavailable" }),
  ).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Call summary" })).toBeNull();
});

it("validates URL state and maps call age to API bounds", () => {
  const ui = journalQuery(
    "days=3&chain=x&sort=random&outcome=negative&age=1to7d&min_score=80&page=-2&revision=7&group=token",
  );
  expect(ui.get("days")).toBe("7");
  expect(ui.has("chain")).toBe(false);
  expect(ui.get("sort")).toBe("newest");
  expect(ui.get("page")).toBe("1");
  const api = journalApiQuery(ui);
  expect(api.get("min_age_hours")).toBe("24");
  expect(api.get("max_age_hours")).toBe("168");
  expect(api.get("outcome")).toBe("negative");
  expect(api.get("page_size")).toBe("100");
  const exported = journalApiQuery(ui, true);
  expect(exported.has("page")).toBe(false);
  expect(exported.has("page_size")).toBe(false);
});

it("shows best and worst observed on every row without opening a disclosure", async () => {
  render(<TrackRecordPage />);
  await screen.findByRole("region", { name: "Call summary" });
  const rows = document.querySelectorAll("article.call-row");
  expect(rows.length).toBeGreaterThanOrEqual(10);
  for (const row of rows) {
    expect(row.querySelector("details")).toBeNull();
    const terms = Array.from(row.querySelectorAll("dt")).map(
      (t) => t.textContent,
    );
    expect(terms).toContain("Best");
    expect(terms).toContain("Worst");
  }
});
