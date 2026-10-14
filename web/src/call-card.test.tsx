// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { CallPerformanceCard, duration, signed } from "./CallCard";
import { demoRequest } from "./demo";
import type { Alert, AlertDetail } from "./types";

afterEach(cleanup);
// The feed is newest first; fixtures below are addressed by their stable ids.
const alerts = (demoRequest("/api/v1/alerts") as { items: Alert[] }).items
  .slice()
  .sort((a, b) => a.id - b.id);
const text = (c: Element | null) => c?.textContent ?? "";

it("shows a positive change with sign and the word up", () => {
  const { container } = render(<CallPerformanceCard alert={alerts[0]} />);
  expect(text(container)).toContain("+40.0%");
  expect(text(container)).toMatch(/up since call/);
  expect(text(container)).toContain("$3M");
  expect(text(container)).toContain("$4.2M");
});
it("shows a negative change with a minus sign and the word down", () => {
  const { container } = render(
    <CallPerformanceCard alert={alerts[1]} variant="compact" />,
  );
  expect(text(container)).toContain("−25.0%");
  expect(text(container)).toMatch(/down since call/);
});
it("shows zero change as unchanged, not as unknown", () => {
  const { container } = render(
    <CallPerformanceCard alert={alerts[5]} variant="compact" />,
  );
  expect(text(container)).toContain("0.0%");
  expect(text(container)).toMatch(/unchanged since call/);
  expect(text(container)).not.toContain("Change since call unknown");
});
it("labels a legacy call without market cap as unknown, never 0%", () => {
  const { container } = render(<CallPerformanceCard alert={alerts[2]} />);
  expect(text(container)).toContain("Market cap at call unknown");
  expect(text(container)).toContain("Change since call unknown");
  expect(text(container)).not.toMatch(/(^|[^\d.])0(\.0)?%/);
  expect(text(container)).toContain("Unknown");
});
it("explains a call with no later observation", () => {
  const { container } = render(<CallPerformanceCard alert={alerts[4]} />);
  expect(text(container)).toContain("No later observation has been saved yet");
  expect(text(container)).toContain("None saved after the call");
  expect(text(container)).not.toMatch(/(^|[^\d.])0(\.0)?%/);
});
it("flags a stale latest observation and prints the exact time", () => {
  const { container } = render(<CallPerformanceCard alert={alerts[3]} />);
  expect(text(container)).toMatch(/Stale · 9h ago/);
  expect(container.querySelector("time")?.getAttribute("datetime")).toMatch(
    /^\d{4}-/,
  );
});
it("labels best and worst observed and allows a negative best", () => {
  const { container } = render(
    <CallPerformanceCard alert={alerts[1]} variant="compact" />,
  );
  const dl = container.querySelector(".call-card-extremes") as HTMLElement;
  expect(within(dl).getByText("Best observed")).toBeTruthy();
  expect(within(dl).getByText("Worst observed")).toBeTruthy();
  expect(text(dl)).toContain("−3.1%");
  expect(text(dl)).not.toContain("+3.1%");
});
it("shows observed, pending, unavailable and missed checkpoints", () => {
  const detail: AlertDetail = {
    ...alerts[0],
    outcomes: [{ horizon_hours: 1, return_pct: 4.2, observed_at: 1 }],
    performance: { ...alerts[0].performance!, elapsed_seconds: 3 * 3600 },
  };
  const { container } = render(<CallPerformanceCard alert={detail} />);
  const strip = container.querySelector(".call-card-checkpoints");
  expect(strip?.querySelectorAll("li")).toHaveLength(4);
  expect(text(strip)).toContain("+4.2%");
  expect(text(strip)).toContain("Pending");
  const old = {
    ...detail,
    performance: { ...detail.performance!, elapsed_seconds: 100 * 3600 },
  };
  cleanup();
  expect(text(render(<CallPerformanceCard alert={old} />).container)).toContain(
    "Unavailable",
  );
  const missed = {
    ...old,
    checkpoint_research: [
      { horizon_hours: 6, state: "missed", return_pct: null },
    ],
  } as unknown as AlertDetail;
  cleanup();
  expect(
    text(render(<CallPerformanceCard alert={missed} />).container),
  ).toContain("Missed");
});
it("shows the call position on a token called more than once", () => {
  render(<CallPerformanceCard alert={alerts[0]} />);
  expect(screen.getByText("Call 2 of 3 on this token")).toBeTruthy();
  cleanup();
  render(<CallPerformanceCard alert={alerts[1]} />);
  expect(screen.queryByText(/on this token/)).toBeNull();
});
it("never uses the check icon for risks", () => {
  const { container } = render(<CallPerformanceCard alert={alerts[0]} />);
  const risks = container.querySelector('[aria-label="Top risks"]')!;
  const reasons = container.querySelector('[aria-label="Top reasons"]')!;
  const items = risks.querySelectorAll("li");
  expect(items.length).toBeGreaterThan(0);
  expect(items.length).toBeLessThanOrEqual(2);
  expect(reasons.querySelectorAll("li").length).toBeLessThanOrEqual(2);
  const check = reasons.querySelector("svg")!.innerHTML;
  for (const li of items)
    expect(li.querySelector("svg")!.innerHTML).not.toBe(check);
  expect(risks.textContent).toContain("Risk:");
});
it("states that missing risks are not proof of safety", () => {
  const o = alerts[0].observation!;
  const { container } = render(
    <CallPerformanceCard
      alert={{
        ...alerts[0],
        observation: {
          ...o,
          blockers: [],
          warnings: [],
          signal: { ...o.signal!, components: { volume_usd: 5 } },
        },
      }}
    />,
  );
  expect(text(container)).toContain("does not mean the token is safe");
});
it("offers token, GMGN and (compact) original-call actions in order", () => {
  const { container } = render(
    <CallPerformanceCard alert={alerts[0]} variant="compact" />,
  );
  const links = [...container.querySelectorAll("nav a")];
  expect(links.map((a) => a.textContent)).toEqual([
    "View token",
    "Open GMGN",
    "Original call",
  ]);
  expect(links[1].getAttribute("href")).toMatch(
    /^https:\/\/gmgn\.ai\/sol\/token\//,
  );
  expect(links[1].getAttribute("rel")).toContain("noopener");
});
it("handles an alert with no performance object without inventing values", () => {
  const { container } = render(
    <CallPerformanceCard alert={{ ...alerts[0], performance: undefined }} />,
  );
  expect(text(container)).toContain(
    "Performance since the call is unavailable",
  );
  expect(container.querySelector(".call-card-hero")).toBeNull();
  expect(container.querySelector("dl")).toBeNull();
});
it("formats helpers", () => {
  expect(signed(null)).toBe("Unknown");
  expect(signed(0)).toBe("0.0%");
  expect(duration(59)).toBe("under 1 minute");
  expect(duration(3 * 86400 + 5 * 3600)).toBe("3d 5h");
});
it("EvidenceList risks use a warning icon, never the check icon", async () => {
  const { EvidenceList } = await import("./components");
  const { container: neutral } = render(<EvidenceList items={["Good"]} />);
  const { container: risk } = render(
    <EvidenceList items={["Bad"]} tone="warning-text" />,
  );
  expect(neutral.querySelector("svg path")?.getAttribute("d")).not.toBe(
    risk.querySelector("svg path")?.getAttribute("d"),
  );
});
it("hides View token on request but keeps the other actions", () => {
  const { container } = render(
    <CallPerformanceCard alert={alerts[0]} variant="compact" hideTokenLink />,
  );
  expect(within(container as HTMLElement).queryByText("View token")).toBeNull();
  expect(text(container)).toContain("Open GMGN");
  expect(text(container)).toContain("Original call");
});
