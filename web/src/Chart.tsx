import { useMemo, useState, type PointerEvent } from "react";
import { dateTime, exactTime, number, price } from "./format";
import type { Candle } from "./types";
import { Empty } from "./components";
export { chartSegments } from "./chartData";
import { chartSegments } from "./chartData";
export interface Point {
  timestamp: number;
  value: number | null | undefined;
}
export function ObservedChart({
  points,
  label,
  money = false,
  score = false,
  candles,
}: {
  points: Point[];
  label: string;
  money?: boolean;
  score?: boolean;
  candles?: Candle[];
}) {
  const [active, setActive] = useState<number | null>(null);
  const data = useMemo(
    () =>
      points
        .filter((p) => typeof p.value === "number" && Number.isFinite(p.value))
        .sort((a, b) => a.timestamp - b.timestamp),
    [points],
  );
  if (!data.length)
    return (
      <Empty title={`No saved ${label.toLowerCase()} history`}>
        The scanner has not saved enough observations for this chart. Future
        scans will add evidence.
      </Empty>
    );
  const w = 960,
    h = 280,
    left = 96,
    right = 18,
    top = 20,
    bottom = 38;
  const times = data.map((p) => p.timestamp),
    values = data.map((p) => p.value as number);
  const start = Math.min(...times),
    end = Math.max(...times),
    lo = score
      ? 0
      : Math.min(...values, ...(candles?.map((c) => c.low) ?? values)) * 0.95,
    hi = score
      ? 100
      : Math.max(...values, ...(candles?.map((c) => c.high) ?? values)) * 1.05;
  const x = (t: number) =>
      left + ((t - start) / (end - start || 1)) * (w - left - right),
    y = (v: number) =>
      top + (1 - (v - lo) / (hi - lo || 1)) * (h - top - bottom);
  const segments = chartSegments(points);
  const selected =
    active == null ? data.at(-1)! : data[Math.min(active, data.length - 1)];
  const display = (v: number) =>
    score ? `${number(v)} / 100` : money ? price(v) : number(v, 3);
  const inspect = (e: PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect(),
      px = ((e.clientX - rect.left) / rect.width) * w;
    const t = start + ((px - left) / (w - left - right)) * (end - start);
    let best = 0;
    data.forEach((p, i) => {
      if (Math.abs(p.timestamp - t) < Math.abs(data[best].timestamp - t))
        best = i;
    });
    setActive(best);
  };
  return (
    <div className="observed-chart">
      <div className="chart-legend" aria-live="polite">
        <span>
          <i />
          {label}
        </span>
        <strong>{display(selected.value as number)}</strong>
        <small>{dateTime(selected.timestamp)}</small>
      </div>
      <div
        className="chart-scroll"
        role="region"
        aria-label={`${label} chart, horizontally scrollable`}
        tabIndex={0}
      >
        <svg
          viewBox={`0 0 ${w} ${h}`}
          role="img"
          tabIndex={0}
          onKeyDown={(event) => {
            if (
              ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
            ) {
              event.preventDefault();
              setActive((current) =>
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? data.length - 1
                    : Math.max(
                        0,
                        Math.min(
                          data.length - 1,
                          (current ?? data.length - 1) +
                            (event.key === "ArrowLeft" ? -1 : 1),
                        ),
                      ),
              );
            }
          }}
          aria-label={`${label}. ${data.length} saved points, from ${dateTime(start)} to ${dateTime(end)}. Gaps are left unconnected.`}
          onPointerMove={inspect}
          onPointerDown={inspect}
          onPointerLeave={() => setActive(null)}
        >
          <defs>
            <linearGradient
              id={`chart-fill-${label.replaceAll(" ", "")}`}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor="#54daa3" stopOpacity=".15" />
              <stop offset="100%" stopColor="#54daa3" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0, 0.25, 0.5, 0.75, 1].map((r) => {
            const value = lo + (hi - lo) * r;
            return (
              <g key={r}>
                <line
                  x1={left}
                  x2={w - right}
                  y1={y(value)}
                  y2={y(value)}
                  stroke="#20313c"
                  strokeDasharray="3 5"
                />
                <text
                  x={left - 10}
                  y={y(value) + 4}
                  textAnchor="end"
                  fill="#879aa5"
                  fontSize="12"
                >
                  {score
                    ? number(value)
                    : money
                      ? price(value)
                      : number(value, 2)}
                </text>
              </g>
            );
          })}
          {candles?.length
            ? candles.map((c, i) => (
                <g
                  key={c.timestamp}
                  stroke={c.close >= c.open ? "#53d8a1" : "#ee8c8c"}
                >
                  <line
                    x1={x(c.timestamp)}
                    x2={x(c.timestamp)}
                    y1={y(c.high)}
                    y2={y(c.low)}
                  />
                  <rect
                    x={
                      x(c.timestamp) -
                      Math.max(1, ((w - left - right) / candles.length) * 0.25)
                    }
                    y={Math.min(y(c.open), y(c.close))}
                    width={Math.max(
                      2,
                      ((w - left - right) / candles.length) * 0.5,
                    )}
                    height={Math.max(1, Math.abs(y(c.close) - y(c.open)))}
                    fill={c.close >= c.open ? "#53d8a1" : "#ee8c8c"}
                    data-index={i}
                  />
                </g>
              ))
            : segments.map((segment, i) => {
                const line = segment
                  .map(
                    (p, j) =>
                      `${j ? "L" : "M"}${x(p.timestamp)},${y(p.value as number)}`,
                  )
                  .join(" ");
                return (
                  <g key={i}>
                    <path
                      d={`${line} L${x(segment.at(-1)!.timestamp)},${h - bottom} L${x(segment[0].timestamp)},${h - bottom} Z`}
                      fill={`url(#chart-fill-${label.replaceAll(" ", "")})`}
                    />
                    <path
                      d={line}
                      fill="none"
                      stroke="#59d9a4"
                      strokeWidth="2"
                      strokeLinejoin="round"
                    />
                    {segment.length === 1 && (
                      <circle
                        cx={x(segment[0].timestamp)}
                        cy={y(segment[0].value as number)}
                        r="3"
                        fill="#59d9a4"
                      />
                    )}
                  </g>
                );
              })}
          {active != null && (
            <>
              <line
                x1={x(selected.timestamp)}
                x2={x(selected.timestamp)}
                y1={top}
                y2={h - bottom}
                stroke="#85aa98"
                strokeDasharray="3 3"
              />
              <circle
                cx={x(selected.timestamp)}
                cy={y(selected.value as number)}
                r="4"
                fill="#83eabc"
                stroke="#08111a"
                strokeWidth="2"
              />
            </>
          )}
          {[0, 0.25, 0.5, 0.75, 1].map((r) => (
            <text
              key={r}
              x={x(start + (end - start) * r)}
              y={h - 10}
              textAnchor={r === 0 ? "start" : r === 1 ? "end" : "middle"}
              fill="#879aa5"
              fontSize="12"
            >
              {new Date((start + (end - start) * r) * 1000).toLocaleDateString(
                undefined,
                {
                  month: "short",
                  day: "numeric",
                  ...(end - start < 86400 ? { hour: "2-digit" } : {}),
                },
              )}
            </text>
          ))}
        </svg>
      </div>
      <div className="chart-note">
        Coverage: {dateTime(start)} – {dateTime(end)} · {data.length} saved{" "}
        {candles?.length ? "closed 1h candles" : "observations"} · Missing
        intervals remain visible · Use pointer or arrow keys to inspect
      </div>
      <details className="research-disclosure chart-data">
        <summary>Saved chart values</summary>
        <div
          className="admin-table-wrap"
          role="region"
          aria-label={`${label} values`}
          tabIndex={0}
        >
          <table className="radar-table">
            <thead>
              <tr>
                <th>Observed time</th>
                <th>{label}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((point, index) => (
                <tr key={`${point.timestamp}:${index}`}>
                  <td>
                    <time
                      dateTime={new Date(point.timestamp * 1000).toISOString()}
                    >
                      {exactTime(point.timestamp)}
                    </time>
                  </td>
                  <td>
                    <data value={String(point.value)}>
                      {display(point.value as number)}
                    </data>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
