// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Alerts } from "./Publications";
import { demoRequest } from "./demo";
import type { Alert, ListResponse } from "./types";

const urls: string[] = [];
function serve(map?: (body: unknown) => unknown) {
  urls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      urls.push(input);
      const body = demoRequest(input);
      return new Response(JSON.stringify(map ? map(body) : body), {
        status: 200,
      });
    }),
  );
}
beforeEach(() => {
  window.history.replaceState(null, "", "/alerts");
  vi.stubGlobal("scrollTo", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const callIds = (root: HTMLElement) =>
  [...root.querySelectorAll(".call-card")].map((c) =>
    Number(/call (\d+)$/.exec(c.getAttribute("aria-label") ?? "")?.[1]),
  );

it("groups by token without dropping or merging any call", async () => {
  window.history.replaceState(null, "", "/alerts?group=token");
  serve();
  const { container } = render(<Alerts />);
  await screen.findAllByRole("heading", { level: 2, name: /\$WIF/ });
  const flat = demoRequest(
    "/api/v1/alerts?page_size=100",
  ) as ListResponse<Alert>;
  expect(callIds(container).sort((a, b) => a - b)).toEqual(
    flat.items.map((a) => a.id).sort((a, b) => a - b),
  );
  const group = container.querySelector(
    `section.signal-group[aria-labelledby="group-sol-Demo${"1".repeat(38)}"]`,
  ) as HTMLElement;
  expect(within(group).getByText("3 calls")).toBeTruthy();
  expect(group.textContent).toContain("latest");
  // Newest first, each call states its position on the token.
  const badges = [...group.querySelectorAll(".call-card-badge")].map(
    (b) => b.textContent,
  );
  expect(badges).toEqual([
    "Call 3 of 3 on this token",
    "Call 2 of 3 on this token",
    "Call 1 of 3 on this token",
  ]);
  expect(urls[0]).toContain("group=token");
});

it("keeps the grouping toggle in the URL and refetches in the right mode", async () => {
  serve();
  const { container } = render(<Alerts />);
  const toggle = await screen.findByRole("switch", { name: "Group by token" });
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(container.querySelector(".signal-group")).toBeNull();
  await userEvent.click(toggle);
  expect(window.location.search).toBe("?group=token&page=1");
  await screen.findAllByRole("heading", { level: 2, name: /\$WIF/ });
  expect(
    screen
      .getByRole("switch", { name: "Group by token" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(urls.at(-1)).toContain("group=token");
  expect(urls.at(-1)).toContain("page_size=10");
  await userEvent.click(screen.getByRole("switch", { name: "Group by token" }));
  expect(window.location.search).toBe("?page=1");
  await vi.waitFor(() => expect(urls.at(-1)).not.toContain("group=token"));
  expect(urls.at(-1)).toContain("page_size=25");
});

it("collapses reasons, risks and actions behind a disclosure on every compact card", async () => {
  serve();
  const { container } = render(<Alerts />);
  await screen.findAllByText(/since call/);
  const cards = [...container.querySelectorAll(".call-card-compact")];
  expect(cards.length).toBeGreaterThan(3);
  for (const card of cards) {
    const more = card.querySelector(
      "details.call-card-more",
    ) as HTMLDetailsElement;
    expect(more.open).toBe(false);
    expect(more.textContent).toContain("Why it was called");
    expect(more.textContent).toContain("Risks to weigh");
    // The key numbers sit outside the disclosure.
    const outside = [...card.children]
      .filter((c) => c !== more)
      .map((c) => c.textContent)
      .join(" ");
    expect(outside).toMatch(/Market cap/);
    expect(outside).toMatch(/since call|Change since call unknown/);
    expect(outside).toContain("Since the call");
    expect(outside).toContain("Latest observation");
    expect(outside).not.toContain("Why it was called");
  }
});

it("states page-wide caveats once, not on every card", async () => {
  serve((body) => {
    const page = body as ListResponse<Alert>;
    return {
      ...page,
      items: page.items.map((a) => ({
        ...a,
        observation: a.observation && { ...a.observation, origin: "unknown" },
      })),
    };
  });
  render(<Alerts />);
  await screen.findAllByText(/since call/);
  expect(screen.getAllByText(/Origin unclassified/)).toHaveLength(1);
  expect(screen.getAllByText(/Legacy calls:/)).toHaveLength(1);
});

it("shows the signal detail without duplicate reason/risk panels but with contract, security and times", async () => {
  serve();
  const { container } = render(<Alerts id="1" />);
  await screen.findByRole("button", { name: /^Copy contract Demo/ });
  expect(
    screen.queryByRole("heading", { name: /Why it.s on the radar/ }),
  ).toBeNull();
  expect(
    screen.queryByRole("heading", { name: "Risk & data coverage" }),
  ).toBeNull();
  expect(container.querySelector(".stats-grid")).toBeNull();
  expect(screen.queryByText("Alert-time score")).toBeNull();
  const record = container.querySelector(".call-record") as HTMLElement;
  for (const text of [
    "Mint authority renounced",
    "Freeze authority renounced",
    "Top 10 concentration",
    "Dangerous flag",
    "Alert observation",
    "Capture recorded",
    "Delivery confirmed",
    "Liquidity at call",
    "Origin unclassified",
  ])
    expect(record.textContent).toContain(text);
  // Score breakdown and market structure remain.
  expect(screen.getByText("Score breakdown and components")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "Market structure" }),
  ).toBeTruthy();
});
