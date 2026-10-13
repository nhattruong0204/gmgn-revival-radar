import { useEffect, useRef, useState } from "react";
import type { History } from "./types";
import {
  closedCandles,
  rangeEvidence,
  relativeVolume,
  candleCsv,
  HOUR,
} from "./candleData";
import { compact, exactTime, number, price } from "./format";
import { Empty } from "./components";

const quoteFormatter = new Intl.NumberFormat("en-US", {
  maximumSignificantDigits: 10,
});
const quote = (value: number) => quoteFormatter.format(value);

export function CandleWorkspace({ history }: { history: History }) {
  const bars = closedCandles(history.candles, history.as_of);
  const latestEnd =
    bars.at(-1)?.timestamp != null
      ? bars.at(-1)!.timestamp + HOUR
      : history.as_of;
  const earliest = bars[0]?.timestamp ?? latestEnd - HOUR;
  const [window, setWindow] = useState<{ start: number; end: number } | null>(
    null,
  );
  const [tablePage, setTablePage] = useState(0);
  const [active, setActive] = useState<number | null>(null);
  const [width, setWidth] = useState(960);
  const hasBars = bars.length > 0;
  const host = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; start: number; end: number } | null>(null);
  useEffect(() => {
    if (!host.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(260, entry.contentRect.width)),
    );
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [hasBars]);
  if (!bars.length)
    return (
      <Empty title="No cached closed candles">
        Saved 1h candles are unavailable. Opening this page never fetches
        provider data.
      </Empty>
    );
  const span = Math.min(
    latestEnd - earliest,
    window ? Math.max(HOUR, window.end - window.start) : 24 * HOUR,
  );
  const start = Math.max(
    earliest,
    Math.min(latestEnd - span, window?.start ?? latestEnd - span),
  );
  const end = start + span;
  const stale =
    history.candles_expires_at == null ||
    history.candles_expires_at <= Date.now() / 1000 ||
    history.as_of - latestEnd >= 2 * HOUR;
  const evidence = rangeEvidence(bars, start, end, history.as_of, stale);
  const visible = evidence.visible;
  const page = Math.min(
    tablePage,
    Math.max(0, Math.ceil(visible.length / 25) - 1),
  );
  const tableBars = visible.slice(page * 25, (page + 1) * 25);
  const selected =
    visible.find((c) => c.timestamp === active) ?? visible.at(-1);
  const rvol = selected
    ? relativeVolume(bars, selected.timestamp)
    : { ratio: null, samples: 0 };
  const left = width < 500 ? 78 : 96,
    right = 12,
    top = 20,
    priceBottom = 220,
    volumeTop = 280,
    volumeBottom = 370;
  const plotWidth = width - left - right;
  const x = (t: number) => left + ((t - start + HOUR / 2) / span) * plotWidth;
  const low = evidence.low ?? 0,
    high = evidence.high ?? 1;
  const pad = Math.max((high - low) * 0.05, high * 0.001);
  const y = (v: number) =>
    priceBottom -
    ((v - low + pad) / (high - low + 2 * pad)) * (priceBottom - top);
  const maxVolume = Math.max(1, ...visible.map((c) => c.volume_usd ?? 0));
  const vy = (v: number) =>
    volumeBottom - (v / maxVolume) * (volumeBottom - volumeTop);
  const bodyWidth = Math.max(
    1,
    Math.min(18, (plotWidth / (span / HOUR)) * 0.65),
  );
  function move(startValue: number, duration = span) {
    const bounded = Math.max(
      earliest,
      Math.min(latestEnd - duration, startValue),
    );
    setWindow({ start: bounded, end: bounded + duration });
    setActive(null);
    setTablePage(0);
  }
  const zoom = (factor: number) => {
    const duration = Math.min(
      latestEnd - earliest,
      Math.max(4 * HOUR, Math.round((span * factor) / HOUR) * HOUR),
    );
    move(start + Math.round((span - duration) / (2 * HOUR)) * HOUR, duration);
  };
  function inspect(clientX: number, element: SVGSVGElement) {
    const rect = element.getBoundingClientRect();
    const time =
      start +
      ((((clientX - rect.left) / rect.width) * width - left) / plotWidth) *
        span;
    const nearest = visible.reduce<(typeof bars)[number] | undefined>(
      (best, c) =>
        !best ||
        Math.abs(c.timestamp + HOUR / 2 - time) <
          Math.abs(best.timestamp + HOUR / 2 - time)
          ? c
          : best,
      undefined,
    );
    setActive(nearest?.timestamp ?? null);
  }
  return (
    <div className="candle-workspace" ref={host}>
      <div
        className="candle-controls"
        role="group"
        aria-label="Candle range controls"
      >
        {[24, 72, 168].map((hours) => (
          <button
            className="button button-secondary"
            key={hours}
            disabled={latestEnd - earliest < hours * HOUR}
            onClick={() => move(latestEnd - hours * HOUR, hours * HOUR)}
          >
            {hours === 24 ? "24h" : hours === 72 ? "3d" : "7d"}
          </button>
        ))}
        <button
          className="button button-secondary"
          onClick={() => move(earliest, latestEnd - earliest)}
        >
          All saved
        </button>
        <button
          className="button button-secondary"
          onClick={() => zoom(0.5)}
          disabled={span <= 4 * HOUR}
        >
          Zoom in
        </button>
        <button
          className="button button-secondary"
          onClick={() => zoom(2)}
          disabled={span >= latestEnd - earliest}
        >
          Zoom out
        </button>
        <button
          className="button button-secondary"
          onClick={() =>
            move(start - Math.max(HOUR, Math.floor(span / (4 * HOUR)) * HOUR))
          }
          disabled={start <= earliest}
        >
          Earlier
        </button>
        <button
          className="button button-secondary"
          onClick={() =>
            move(start + Math.max(HOUR, Math.floor(span / (4 * HOUR)) * HOUR))
          }
          disabled={end >= latestEnd}
        >
          Later
        </button>
        <button
          className="button button-secondary"
          onClick={() => {
            setTablePage(0);
            setWindow(null);
            setActive(null);
          }}
        >
          Reset range
        </button>
      </div>
      <p className="small">
        Closed 1h bars · Provider price quote not retained · Volume USD ·
        Origin: {history.candles_origin ?? "unknown"}; provider identity not
        retained. No live feed. Hollow candles rise or stay equal; filled
        candles fall.
      </p>
      <p className="small">
        Data through {exactTime(latestEnd)} · Fetched{" "}
        {history.candles_fetched_at == null
          ? "unknown"
          : exactTime(history.candles_fetched_at)}{" "}
        · {stale ? "Stale or freshness unknown" : "Cache within expiry"}
      </p>
      <div className="candle-inspection" aria-live="polite" aria-atomic="true">
        {selected ? (
          <>
            <strong>
              {exactTime(selected.timestamp)} — closed{" "}
              {exactTime(selected.timestamp + HOUR)}
            </strong>
            <span>
              O {quote(selected.open)} · H {quote(selected.high)} · L{" "}
              {quote(selected.low)} · C {quote(selected.close)}
            </span>
            <span>
              Volume{" "}
              {selected.volume_usd == null
                ? "unknown"
                : price(selected.volume_usd)}{" "}
              · Relative volume{" "}
              {stale || rvol.ratio == null
                ? "unknown"
                : `${number(rvol.ratio, 2)}×`}{" "}
              ({rvol.samples}/24 baseline samples)
            </span>
          </>
        ) : (
          <span>No saved bars in this range.</span>
        )}
      </div>
      <svg
        viewBox={`0 0 ${width} 405`}
        role="img"
        tabIndex={0}
        aria-label="Closed hourly price and USD volume. Left and right inspect bars; Home and End select first and last. Use range buttons to pan or zoom."
        onKeyDown={(e) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key))
            return;
          e.preventDefault();
          const i = visible.findIndex(
            (c) => c.timestamp === selected?.timestamp,
          );
          const next =
            e.key === "Home"
              ? 0
              : e.key === "End"
                ? visible.length - 1
                : Math.max(
                    0,
                    Math.min(
                      visible.length - 1,
                      i + (e.key === "ArrowLeft" ? -1 : 1),
                    ),
                  );
          setActive(visible[next]?.timestamp ?? null);
        }}
        onPointerDown={(e) => {
          inspect(e.clientX, e.currentTarget);
          drag.current = { x: e.clientX, start, end };
          e.currentTarget.setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (drag.current && Math.abs(e.clientX - drag.current.x) > 8) {
            const delta =
              Math.round(
                (((drag.current.x - e.clientX) / plotWidth) * span) / HOUR,
              ) * HOUR;
            move(
              drag.current.start + delta,
              drag.current.end - drag.current.start,
            );
          } else inspect(e.clientX, e.currentTarget);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        {[0, 0.5, 1].map((r) => {
          const value = low - pad + (high - low + 2 * pad) * r;
          return (
            <g key={r}>
              <line
                x1={left}
                x2={width - right}
                y1={y(value)}
                y2={y(value)}
                className="candle-grid"
              />
              <text x={left - 8} y={y(value) + 4} textAnchor="end">
                {value < 0.001 || value >= 1000000
                  ? value.toExponential(2)
                  : number(value, 4)}
              </text>
            </g>
          );
        })}
        <text x={left} y={260}>
          Closed-bar volume (USD)
        </text>
        <text x={left - 8} y={volumeTop + 4} textAnchor="end">
          {compact(maxVolume)}
        </text>
        <text x={left - 8} y={volumeBottom} textAnchor="end">
          0
        </text>
        {evidence.usable &&
          [low, high].map((v, i) => (
            <line
              key={i}
              x1={left}
              x2={width - right}
              y1={y(v)}
              y2={y(v)}
              className="candle-boundary"
            />
          ))}
        {visible.map((c) => (
          <g
            key={c.timestamp}
            className={c.close >= c.open ? "candle-up" : "candle-down"}
          >
            <line
              x1={x(c.timestamp)}
              x2={x(c.timestamp)}
              y1={y(c.high)}
              y2={y(c.low)}
            />
            <rect
              x={x(c.timestamp) - bodyWidth / 2}
              y={Math.min(y(c.open), y(c.close))}
              width={bodyWidth}
              height={Math.max(1, Math.abs(y(c.open) - y(c.close)))}
              className={c.close >= c.open ? "candle-hollow" : ""}
            />
            {c.volume_usd != null && (
              <rect
                x={x(c.timestamp) - bodyWidth / 2}
                y={vy(c.volume_usd)}
                width={bodyWidth}
                height={Math.max(0, volumeBottom - vy(c.volume_usd))}
              />
            )}
          </g>
        ))}
        {selected && (
          <line
            x1={x(selected.timestamp)}
            x2={x(selected.timestamp)}
            y1={top}
            y2={volumeBottom}
            className="candle-crosshair"
          />
        )}
        {(width < 500 ? [0, 1] : [0, 0.5, 1]).map((r) => (
          <text
            key={r}
            x={left + r * plotWidth}
            y={395}
            textAnchor={r === 0 ? "start" : r === 1 ? "end" : "middle"}
          >
            {new Date((start + span * r) * 1000).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              hourCycle: "h23",
            })}
          </text>
        ))}
      </svg>
      <p className="small">
        Window {exactTime(start)} – {exactTime(end)} · As of{" "}
        {exactTime(history.as_of)} · Coverage {visible.length}/
        {evidence.expected} hourly bars. Missing intervals are blank; missing
        volume is unknown.
      </p>
      <p className="small">
        {evidence.usable
          ? `Observed range low ${quote(low)} / high ${quote(high)} over ${span / HOUR}h. These are observed extrema, not predictive support or resistance.`
          : "Range boundaries unavailable: require at least 12 consecutive hourly bars, full window coverage and current cache. No support/resistance conclusion."}
      </p>
      <details className="research-disclosure">
        <summary>Volume methodology and precision</summary>
        <p>
          Relative volume compares the inspected bar with the arithmetic mean of
          the preceding 24 consecutive closed 1h USD-volume bars, excluding that
          bar. Require 24/24 samples and a positive mean; stale, missing or zero
          baselines remain unknown. Rolling five-minute snapshot volumes are
          never added to this histogram. Prices show up to 10 significant
          digits; axes use rounded or scientific notation. Display times use
          your browser timezone; CSV uses UTC and full stored numeric precision.
          Cache replacement is not an immutable historical archive.
        </p>
      </details>
      <details className="research-disclosure chart-data">
        <summary>Saved OHLCV values</summary>
        <div
          className="candle-controls"
          role="group"
          aria-label="OHLCV table pagination"
        >
          <button
            className="button button-secondary"
            disabled={page === 0}
            onClick={() => setTablePage(page - 1)}
          >
            Previous rows
          </button>
          <span>
            Rows {visible.length ? page * 25 + 1 : 0}–
            {Math.min((page + 1) * 25, visible.length)} of {visible.length}
          </span>
          <button
            className="button button-secondary"
            disabled={(page + 1) * 25 >= visible.length}
            onClick={() => setTablePage(page + 1)}
          >
            Next rows
          </button>
        </div>
        <button
          className="button button-secondary"
          onClick={() => {
            const url = URL.createObjectURL(
              new Blob([candleCsv(visible)], { type: "text/csv" }),
            );
            const a = document.createElement("a");
            a.href = url;
            a.download = "saved-1h-candles.csv";
            a.click();
            URL.revokeObjectURL(url);
          }}
        >
          Export visible bars (UTC CSV)
        </button>
        <div
          className="admin-table-wrap"
          role="region"
          aria-label="Saved OHLCV table"
          tabIndex={0}
        >
          <table className="radar-table">
            <thead>
              <tr>
                {[
                  "Bar start",
                  "Open",
                  "High",
                  "Low",
                  "Close",
                  "Volume USD",
                ].map((name) => (
                  <th key={name}>{name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableBars.map((c) => (
                <tr key={c.timestamp}>
                  <td>
                    <time dateTime={new Date(c.timestamp * 1000).toISOString()}>
                      {exactTime(c.timestamp)}
                    </time>
                  </td>
                  {[c.open, c.high, c.low, c.close, c.volume_usd].map(
                    (v, i) => (
                      <td key={i}>
                        {v == null ? (
                          "unknown"
                        ) : (
                          <data value={String(v)}>{quote(v)}</data>
                        )}
                      </td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
