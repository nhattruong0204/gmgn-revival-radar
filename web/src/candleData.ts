import type { Candle } from "./types";
export const HOUR = 3600;
export const BASELINE = 24;
// Defensive in addition to the server contract: ambiguous duplicates are not evidence.
export function closedCandles(bars: Candle[], asOf: number): Candle[] {
  const counts = new Map<number, number>();
  bars.forEach((bar) =>
    counts.set(bar.timestamp, (counts.get(bar.timestamp) ?? 0) + 1),
  );
  return bars
    .filter(
      (c) =>
        counts.get(c.timestamp) === 1 &&
        Number.isFinite(c.timestamp) &&
        c.timestamp + HOUR <= asOf &&
        [c.open, c.high, c.low, c.close].every(
          (v) => Number.isFinite(v) && v > 0,
        ) &&
        c.low <= Math.min(c.open, c.close) &&
        c.high >= Math.max(c.open, c.close) &&
        c.low <= c.high,
    )
    .map((bar) => ({
      ...bar,
      volume_usd:
        bar.volume_usd != null &&
        Number.isFinite(bar.volume_usd) &&
        bar.volume_usd >= 0
          ? bar.volume_usd
          : null,
    }))
    .sort((a, b) => a.timestamp - b.timestamp);
}
export function relativeVolume(
  bars: Candle[],
  timestamp: number,
): { ratio: number | null; samples: number } {
  const index = bars.findIndex((c) => c.timestamp === timestamp);
  const previous = bars.slice(Math.max(0, index - BASELINE), index);
  const usable = previous.filter(
    (c) =>
      c.volume_usd != null &&
      Number.isFinite(c.volume_usd) &&
      c.volume_usd >= 0,
  );
  const current = bars[index]?.volume_usd;
  const contiguous =
    previous.length === BASELINE &&
    previous.every((c, i) => c.timestamp === timestamp - (BASELINE - i) * HOUR);
  const mean = usable.reduce((sum, c) => sum + c.volume_usd!, 0) / BASELINE;
  return {
    samples: usable.length,
    ratio:
      contiguous &&
      usable.length === BASELINE &&
      mean > 0 &&
      current != null &&
      Number.isFinite(current) &&
      current >= 0
        ? current / mean
        : null,
  };
}
export function rangeEvidence(
  bars: Candle[],
  start: number,
  end: number,
  asOf: number,
  stale: boolean,
) {
  const visible = bars.filter(
    (c) => c.timestamp >= start && c.timestamp + HOUR <= end,
  );
  const expected = Math.floor((end - start) / HOUR);
  const complete =
    expected >= 12 &&
    visible.length === expected &&
    visible.every((c, i) => c.timestamp === start + i * HOUR);
  return {
    visible,
    expected,
    complete,
    usable: complete && !stale && end <= asOf,
    low: visible.length ? Math.min(...visible.map((c) => c.low)) : null,
    high: visible.length ? Math.max(...visible.map((c) => c.high)) : null,
  };
}
export function candleCsv(bars: Candle[]): string {
  return (
    "bar_start_utc,bar_end_utc,open,high,low,close,volume_usd\n" +
    bars
      .map((c) =>
        [
          new Date(c.timestamp * 1000).toISOString(),
          new Date((c.timestamp + HOUR) * 1000).toISOString(),
          c.open,
          c.high,
          c.low,
          c.close,
          c.volume_usd ?? "",
        ].join(","),
      )
      .join("\n")
  );
}
