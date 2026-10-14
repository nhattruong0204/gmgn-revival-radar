// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { demoObservations, demoRequest } from "./demo";
import { ObservedChart } from "./Chart";
import { TokenCalls } from "./TokenCalls";
import { TokenDetail } from "./TokenDetail";
import type { Alert, ListResponse } from "./types";

const three = demoObservations[0].contract_address;
const none = demoObservations[7].contract_address;
let urls: string[];
beforeEach(() => {
  urls = [];
  window.history.replaceState(null, "", "/tokens/sol/x");
  vi.stubGlobal("scrollTo", vi.fn());
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      urls.push(path);
      try {
        return new Response(JSON.stringify(demoRequest(path)));
      } catch {
        return new Response(JSON.stringify({ detail: "missing" }), {
          status: 404,
        });
      }
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("demo API filters calls to one token, newest first, with three on the first token", () => {
  const result = demoRequest(
    `/api/v1/alerts?chain=sol&address=${three}`,
  ) as ListResponse<Alert>;
  expect(result.total).toBe(3);
  expect(result.items.map((a) => a.id)).toEqual([8, 1, 7]);
  expect(
    result.items.map((a) => a.performance?.token_call_index).sort(),
  ).toEqual([1, 2, 3]);
});

it("lists this token's calls as compact cards, newest first, from the filtered endpoint", async () => {
  render(<TokenCalls chain="sol" address={three} />);
  const section = await screen.findByRole("region", {
    name: "Calls on this token",
  });
  const cards = await within(section).findAllByRole("article");
  expect(cards).toHaveLength(3);
  expect(cards[0].getAttribute("aria-label")).toContain("call 8");
  expect(cards[2].getAttribute("aria-label")).toContain("call 7");
  expect(cards[0].className).toContain("call-card-compact");
  expect(urls[0]).toContain(`/api/v1/alerts?chain=sol&address=${three}`);
  expect(screen.queryByRole("navigation", { name: "Calls pages" })).toBeNull();
});

it("shows an explicit empty state when the token has no calls", async () => {
  render(<TokenCalls chain="sol" address={none} />);
  expect(
    await screen.findByText("No published calls for this token"),
  ).toBeTruthy();
  expect(screen.queryAllByRole("article")).toHaveLength(0);
});

it("paginates server-side without losing the list", async () => {
  const items = Array.from({ length: 7 }, (_, i) => ({
    ...(demoRequest("/api/v1/alerts") as ListResponse<Alert>).items[0],
    id: 100 - i,
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const q = new URL(path, "https://x").searchParams;
      const page = Number(q.get("page")),
        size = Number(q.get("page_size"));
      urls.push(path);
      return new Response(
        JSON.stringify({
          items: items.slice((page - 1) * size, page * size),
          total: 7,
          page,
          page_size: size,
          as_of: 1,
          last_scan_at: 1,
        }),
      );
    }),
  );
  render(<TokenCalls chain="sol" address={three} />);
  expect(await screen.findAllByRole("article")).toHaveLength(5);
  expect(screen.getByText(/7 calls · page 1 of 2/)).toBeTruthy();
  const older = screen.getByRole("button", { name: /Older/ });
  expect(
    (screen.getByRole("button", { name: /Newer/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await userEvent.click(older);
  expect(await screen.findByText(/page 2 of 2/)).toBeTruthy();
  const second = await screen.findAllByRole("article");
  expect(second).toHaveLength(2);
  expect(second[0].getAttribute("aria-label")).toContain("call 95");
  expect(urls.some((u) => u.includes("page=2&page_size=5"))).toBe(true);
});

it("marks calls on the chart with named, keyboard-inspectable markers", async () => {
  render(<TokenDetail chain="sol" address={three} />);
  const chart = await screen.findByRole("img", { name: /Observed price/ });
  const markers = await within(
    chart.closest("svg")!.parentElement!,
  ).findAllByRole("button", { name: /^Call \d of 3/ });
  expect(markers).toHaveLength(3);
  const names = markers.map((m) => m.getAttribute("aria-label")!);
  expect(names.some((n) => /Call 2 of 3, alert #1,/.test(n))).toBe(true);
  expect(names.every((n) => /market cap at call \$[\d.]+[MK]/.test(n))).toBe(
    true,
  );
  expect(markers.every((m) => m.getAttribute("tabindex") === "0")).toBe(true);
  markers[0].focus();
  const readout = await screen.findByText(names[0]);
  expect(readout.closest(".chart-marker-readout")).toBeTruthy();
  await userEvent.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(markers[1]);
  await userEvent.keyboard("{Escape}");
  expect(document.querySelector(".chart-marker-readout")).toBeNull();
});

it("names markers without a saved call market cap as unknown and ignores out-of-window calls", () => {
  const points = [
    { timestamp: 1000, value: 1 },
    { timestamp: 2000, value: 2 },
  ];
  render(
    <ObservedChart
      label="Observed price"
      points={points}
      markers={[
        {
          id: 1,
          timestamp: 1500,
          label: "Call #1, market cap at call unknown",
        },
        { id: 2, timestamp: 50, label: "Call #2, early" },
      ]}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Call #1, market cap at call unknown" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: /early/ })).toBeNull();
  expect(screen.getByText(/1 call outside this window/)).toBeTruthy();
});
