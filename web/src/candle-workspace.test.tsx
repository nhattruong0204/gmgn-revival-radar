// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CandleWorkspace } from "./CandleWorkspace";
import {
  BASELINE,
  HOUR,
  candleCsv,
  closedCandles,
  rangeEvidence,
  relativeVolume,
} from "./candleData";
import type { Candle, History } from "./types";
afterEach(cleanup);
const end = 1800000000;
const bars: Candle[] = Array.from({ length: 72 }, (_, i) => ({
  timestamp: end - (72 - i) * HOUR,
  open: 0.001,
  high: 0.003,
  low: 0.0005,
  close: 0.002,
  volume_usd: i === 71 ? 200 : 100,
}));
const history: History = {
  candles: bars,
  snapshots: [],
  scores: [],
  since: end - 168 * HOUR,
  as_of: end,
  candles_fetched_at: end,
  candles_expires_at: 9999999999,
  truncated: false,
  candles_origin: "test",
};
it("excludes open/future bars and ambiguous duplicate timestamps", () => {
  expect(
    closedCandles(
      [...bars, { ...bars[0] }, { ...bars[0], timestamp: end }],
      end,
    ),
  ).toHaveLength(71);
  expect(closedCandles([{ ...bars[0], low: 0.1 }], end)).toHaveLength(0);
});
it("uses only previous consecutive hourly USD volumes; never zero-fills", () => {
  expect(relativeVolume(bars, bars.at(-1)!.timestamp)).toEqual({
    ratio: 2,
    samples: BASELINE,
  });
  expect(relativeVolume(bars.slice(1), bars[24].timestamp).ratio).toBeNull();
  expect(
    relativeVolume(
      bars.filter((_, i) => i !== 60),
      bars[71].timestamp,
    ).ratio,
  ).toBeNull();
  expect(
    relativeVolume(
      bars.map((c) => ({ ...c, volume_usd: 0 })),
      bars[71].timestamp,
    ).ratio,
  ).toBeNull();
  expect(
    relativeVolume(
      bars.map((c, i) => ({
        ...c,
        volume_usd: i === 60 ? null : c.volume_usd,
      })),
      bars[71].timestamp,
    ),
  ).toEqual({ ratio: null, samples: 23 });
});
it("range extrema require full requested coverage, minimum size and fresh closed evidence", () => {
  expect(rangeEvidence(bars, end - 24 * HOUR, end, end, false).usable).toBe(
    true,
  );
  for (const data of [bars.slice(0, -1), bars.filter((_, i) => i !== 60)])
    expect(rangeEvidence(data, end - 24 * HOUR, end, end, false).usable).toBe(
      false,
    );
  expect(rangeEvidence(bars, end - 24 * HOUR, end, end, true).usable).toBe(
    false,
  );
  expect(rangeEvidence(bars, end - 4 * HOUR, end, end, false).usable).toBe(
    false,
  );
});
it("shares inspection across price and volume, pans/zooms/resets via buttons", () => {
  render(<CandleWorkspace history={history} />);
  expect(screen.getByText(/2×/)).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("img"), { key: "Home" });
  expect(screen.getByText(/1×/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText(/Coverage 12\/12/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
  expect(
    screen.getByRole("button", { name: "Later" }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Reset range" }));
  expect(screen.getByText(/Coverage 24\/24/)).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "7d" }).hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(screen.getByText("Saved OHLCV values"));
  expect(screen.getAllByRole("row")).toHaveLength(25);
  fireEvent.click(screen.getByRole("button", { name: "All saved" }));
  expect(screen.getAllByRole("row")).toHaveLength(26);
  fireEvent.click(screen.getByRole("button", { name: "Next rows" }));
  expect(screen.getByText("Rows 26–50 of 72")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next rows" }));
  expect(screen.getAllByRole("row")).toHaveLength(23);
});
it("stale and sparse caches disable conclusions and relative volume without hiding evidence", () => {
  render(<CandleWorkspace history={{ ...history, candles_expires_at: 0 }} />);
  expect(screen.getByText(/Relative volume unknown/)).toBeTruthy();
  expect(screen.getByText(/Range boundaries unavailable/)).toBeTruthy();
  expect(screen.getByRole("img")).toBeTruthy();
});
it("CSV preserves exact OHLCV, UTC boundaries and missing versus zero volume", () => {
  const csv = candleCsv([
    { ...bars[0], volume_usd: null },
    { ...bars[1], volume_usd: 0 },
  ]);
  expect(csv).toContain(new Date(bars[0].timestamp * 1000).toISOString());
  expect(csv.split("\n")[1]).toMatch(/,$/);
  expect(csv.split("\n")[2]).toMatch(/,0$/);
});

it("attaches responsive measurement when an empty cache gains candles", () => {
  const observe = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = observe;
      disconnect() {}
    },
  );
  const { rerender } = render(
    <CandleWorkspace history={{ ...history, candles: [] }} />,
  );
  expect(observe).not.toHaveBeenCalled();
  rerender(<CandleWorkspace history={history} />);
  expect(observe).toHaveBeenCalledOnce();
  vi.unstubAllGlobals();
});
