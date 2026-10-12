// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Admin } from "./Admin";

let revision: number;
let previewExpiry: number;
let confirmStatus: string;
let submissions: Record<string, unknown>[];
beforeEach(() => {
  revision = 5;
  previewExpiry = Date.now() / 1000 + 600;
  confirmStatus = "succeeded";
  submissions = [];
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      let data: unknown;
      if (path.endsWith("/session"))
        data = { authenticated: true, csrf_token: "fixture-csrf" };
      else if (path.endsWith("/settings"))
        data = {
          settings: {
            min_holders: 100,
            alerts_paused: false,
            enabled_chains: ["sol"],
          },
          revision,
          active_revision: 4,
          presets: { strict: {}, balanced: {}, broad: {} },
        };
      else if (path.endsWith("/status"))
        data = {
          worker: {
            available: false,
            heartbeat_at: Date.now() / 1000 - 100,
            schedule: { running: true },
          },
          scan_history: [
            { id: 1, started: 1000, finished: null, processed: 10 },
          ],
          health: {
            scans: {},
            discovery: {},
            evaluations: {},
            alerts: { failed: 2, unknown: 1 },
            best_candidates: [
              {
                id: 2,
                symbol: "NEAR",
                chain: "sol",
                score: 74,
                rejection_reasons: ["Low liquidity"],
                missing_fields: ["holders"],
              },
            ],
          },
        };
      else if (options?.method === "POST") {
        const body = JSON.parse(String(options.body));
        submissions.push(body);
        data = {
          id: String(submissions.length),
          action: body.action,
          status: body.action === "preview" ? "succeeded" : confirmStatus,
          result:
            body.action === "preview"
              ? {
                  preview_id: "persisted-preview",
                  revision: 5,
                  expires_at: previewExpiry,
                  before: { min_holders: 100 },
                  after: { min_holders: 120 },
                  changes: { min_holders: 120 },
                }
              : {
                  message:
                    confirmStatus === "failed"
                      ? "Settings changed. Request a new preview."
                      : "Scan already running. Wait until it finishes.",
                },
        };
      } else
        data = {
          items: [
            {
              id: "pending",
              action: "confirm",
              status: "queued",
              created_at: 1000,
            },
            {
              id: "uncertain",
              action: "confirm",
              status: "unknown",
              result: { message: "Delivery uncertain" },
            },
          ],
        };
      return new Response(JSON.stringify(data));
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function openScan() {
  render(<Admin section="actions" />);
  await screen.findByRole("heading", { name: "Scanner controls" });
  fireEvent.click(screen.getAllByRole("button", { name: /Preview action/ })[0]);
  return await screen.findByRole("dialog");
}
it("renders private diagnostics, stale worker and unfinished scan without claiming it is online", async () => {
  render(<Admin section="overview" />);
  await screen.findByText("NEAR · sol");
  expect(screen.getAllByText("Unavailable")[0]).toBeTruthy();
  expect(screen.getByText("Unfinished record")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "Private delivery results" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "Provider and data coverage" }),
  ).toBeTruthy();
  expect(screen.getByText(/pending activation/)).toBeTruthy();
  expect(screen.getByText("queued")).toBeTruthy();
  expect(screen.getByText("unknown")).toBeTruthy();
});
it("shows before/after values and cancellation sends no confirmation", async () => {
  const dialog = await openScan();
  expect(
    within(dialog).getByRole("columnheader", { name: "Before" }),
  ).toBeTruthy();
  expect(within(dialog).getByText("100")).toBeTruthy();
  expect(within(dialog).getByText("120")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(submissions).toHaveLength(1);
});
it("confirms only the persisted preview ID and reviewed revision, and shows the actual result", async () => {
  const dialog = await openScan();
  fireEvent.click(
    within(dialog).getByRole("button", { name: /Confirm action/ }),
  );
  await screen.findByText(/Scan already running/);
  expect(submissions[1]).toEqual({
    id: expect.any(String),
    action: "confirm",
    preview_id: "persisted-preview",
    expected_revision: 5,
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("disables an expired preview and tells the owner to request a fresh one", async () => {
  previewExpiry = Date.now() / 1000 - 1;
  const dialog = await openScan();
  await within(dialog).findByText(/This preview expired/);
  expect(
    (
      within(dialog).getByRole("button", {
        name: /Confirm action/,
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(submissions).toHaveLength(1);
});
it("detects a concurrent Telegram revision before allowing confirmation", async () => {
  const dialog = await openScan();
  revision = 6;
  fireEvent(document, new Event("visibilitychange"));
  await within(dialog).findByText(/Settings changed since this preview/);
  expect(
    (
      within(dialog).getByRole("button", {
        name: /Confirm action/,
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(submissions).toHaveLength(1);
});
it("shows failures and uncertain results without optimistic success", async () => {
  confirmStatus = "unknown";
  const dialog = await openScan();
  fireEvent.click(
    within(dialog).getByRole("button", { name: /Confirm action/ }),
  );
  await screen.findByText(/outcome is uncertain/);
  expect(screen.queryByText("Confirm: succeeded")).toBeNull();
});
it("shows worker validation failures and removes the consumed preview", async () => {
  confirmStatus = "failed";
  const dialog = await openScan();
  fireEvent.click(
    within(dialog).getByRole("button", { name: /Confirm action/ }),
  );
  await screen.findByText("Settings changed. Request a new preview.");
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("preserves unsaved form changes when polling returns unchanged settings", async () => {
  render(<Admin section="settings" />);
  const field = await screen.findByLabelText(/Min holders/);
  fireEvent.change(field, { target: { value: "123" } });
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.filter(([p]) => String(p).endsWith("/settings")).length,
    ).toBeGreaterThan(1),
  );
  expect((field as HTMLInputElement).value).toBe("123");
});

it("traps keyboard focus in the preview and allows Escape cancellation", async () => {
  const dialog = await openScan();
  const close = within(dialog).getByRole("button", { name: "Close preview" });
  const confirm = within(dialog).getByRole("button", {
    name: /Confirm action/,
  });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(confirm);
  fireEvent.keyDown(confirm, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(submissions).toHaveLength(1);
});
