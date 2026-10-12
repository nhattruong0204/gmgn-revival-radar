export interface ChartPoint {
  timestamp: number;
  value: number | null | undefined;
}

// Unknown measurements and absent intervals must never become an interpolated line.
export function chartSegments(points: ChartPoint[]): ChartPoint[][] {
  const ordered = points
    .filter((p) => Number.isFinite(p.timestamp))
    .sort((a, b) => a.timestamp - b.timestamp);
  const intervals = ordered
    .slice(1)
    .map((p, i) => p.timestamp - ordered[i].timestamp)
    .filter((v) => v > 0)
    .sort((a, b) => a - b);
  const cadence = intervals[Math.floor((intervals.length - 1) / 2)] ?? 3600;
  const segments: ChartPoint[][] = [];
  let previous: ChartPoint | undefined;
  for (const point of ordered) {
    if (typeof point.value !== "number" || !Number.isFinite(point.value)) {
      previous = undefined;
      continue;
    }
    if (!previous || point.timestamp - previous.timestamp > cadence * 1.8)
      segments.push([]);
    segments.at(-1)!.push(point);
    previous = point;
  }
  return segments;
}
