import { labels } from "./labels";
import { useState } from "react";
import { Modal } from "./Modal";
import { navigate, useData, useLocation } from "./api";
import { CallJournalView } from "./CallJournal";
import { Empty, Link, Loading, Notice, PageHeading, Stat } from "./components";
import { dateTime, humanKey, percent, researchLink, chains } from "./format";
import type { CheckpointResearch, TrackRecordData } from "./types";

export function trackQuery(search: string) {
  const input = new URLSearchParams(search);
  const output = new URLSearchParams();
  output.set(
    "days",
    [1, 3, 7, 30, 90, 365].includes(Number(input.get("days")))
      ? input.get("days")!
      : "7",
  );
  output.set(
    "horizon",
    [1, 6, 24, 72].includes(Number(input.get("horizon")))
      ? input.get("horizon")!
      : "24",
  );
  for (const key of [
    "chain",
    "preset",
    "revision",
    "score_version",
    "origin",
    "knowledge",
    "cohort",
    "checkpoint",
    "bucket",
    "as_of",
  ]) {
    const value = input.get(key);
    if (!value) continue;
    const valid =
      key === "revision"
        ? /^(unknown|[0-9]{1,9})$/.test(value)
        : key === "cohort"
          ? /^[a-f0-9]{16}$/.test(value)
          : key === "bucket"
            ? /^[0-4]$/.test(value)
            : key === "as_of"
              ? Number.isFinite(Number(value)) && Number(value) >= 0
              : key === "score_version"
                ? value.length <= 100
                : key === "chain"
                  ? ["sol", "bsc", "base", "robinhood", "arc"].includes(value)
                  : key === "preset"
                    ? [
                        "Strict",
                        "Balanced",
                        "Broad",
                        "Custom",
                        "unknown",
                      ].includes(value)
                    : key === "origin"
                      ? ["live", "unknown", "synthetic"].includes(value)
                      : key === "knowledge"
                        ? ["recorded", "legacy_unknown"].includes(value)
                        : [
                            "return",
                            "missing_price",
                            "unresolved",
                            "pending",
                            "missed",
                          ].includes(value);
    if (valid) output.set(key, value);
  }
  const page = Number(input.get("page"));
  output.set(
    "page",
    Number.isInteger(page) && page >= 1 && page <= 10000 ? String(page) : "1",
  );
  output.set("page_size", "25");
  return output;
}

export function CheckpointTable({ items }: { items: CheckpointResearch[] }) {
  return (
    <div
      className="checkpoint-table-wrap"
      role="region"
      aria-label="Saved checkpoints"
      tabIndex={0}
    >
      <table className="checkpoint-table">
        <caption>
          Saved checkpoint evidence; prices have unknown quote currency.{" "}
          {labels.checkpointNote} Changes are percentages.
        </caption>
        <thead>
          <tr>
            {[
              "Alert / origin",
              "Checkpoint window",
              "Observed / recorded",
              "Saved prices",
              labels.priceChange,
              "Source",
            ].map((h) => (
              <th scope="col" key={h}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={`${row.alert_id}:${row.horizon_hours}`}>
              <td>
                <strong>{row.symbol}</strong>
                <div>
                  {chains[row.chain] ?? row.chain} · alert #{row.alert_id}
                </div>
                <div>{dateTime(row.timestamp)}</div>
                <div>
                  {row.origin} · {humanKey(row.knowledge)}
                </div>
                <div>
                  {row.preset ?? "Unknown strategy"} · rev{" "}
                  {row.revision ?? "unknown"} ·{" "}
                  {row.score_version ?? "Unknown score version"}
                </div>
                <div className="small">{row.contract_address}</div>
              </td>
              <td>
                +{row.horizon_hours}h<div>Due {dateTime(row.due_at)}</div>
                <div>Through {dateTime(row.window_end)} inclusive</div>
              </td>
              <td>
                {row.observed_at == null
                  ? "No visible observation"
                  : dateTime(row.observed_at)}
                <div>
                  Recorded{" "}
                  {row.recorded_at == null
                    ? "Unknown / not observed"
                    : dateTime(row.recorded_at)}
                </div>
                <div>Checkpoint origin: {row.checkpoint_origin}</div>
              </td>
              <td>
                Initial{" "}
                {row.initial_price == null
                  ? "Unknown"
                  : String(row.initial_price)}
                <div>
                  Checkpoint{" "}
                  {row.checkpoint_price == null
                    ? "Unknown"
                    : String(row.checkpoint_price)}
                </div>
              </td>
              <td>
                <strong
                  className={
                    row.return_pct == null
                      ? "muted"
                      : row.return_pct < 0
                        ? "danger"
                        : row.return_pct > 0
                          ? "positive"
                          : ""
                  }
                >
                  {row.return_pct == null
                    ? humanKey(row.state)
                    : percent(row.return_pct, false, true)}
                </strong>
                <div>{row.reason}</div>
                <details>
                  <summary>Exact saved values</summary>
                  <p>
                    Computable price change:{" "}
                    {row.return_pct == null ? "Unknown" : `${row.return_pct}%`}.
                    Stored scalar:{" "}
                    {row.reported_return_pct == null
                      ? "Unknown"
                      : `${row.reported_return_pct}%`}
                    . Unresolved scalars are excluded from aggregates.
                  </p>
                </details>
              </td>
              <td>
                <Link href={researchLink(`/alerts/${row.alert_id}`)}>
                  Alert evidence
                </Link>
                <div>{row.source}</div>
                <div>
                  {row.brief_id
                    ? "Captured brief association"
                    : "No captured brief association"}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CheckpointRecordPage() {
  const location = useLocation();
  const query = trackQuery(location.split("?")[1] ?? "");
  const source = useData<TrackRecordData>(`/api/v1/track-record?${query}`);
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const { data, error, loading, refresh } = source;
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(query);
    value ? next.set(key, value) : next.delete(key);
    if (key !== "page") next.set("page", "1");
    if (!["cohort", "page", "checkpoint", "bucket"].includes(key))
      next.delete("cohort");
    next.delete("page_size");
    next.set("view", "checkpoints");
    navigate(`/outcomes?${next}`);
  };
  const control = (
    key: string,
    label: string,
    options: Array<[string, string]>,
  ) => (
    <label className="select-field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={query.get(key) ?? ""}
        onChange={(e) => set(key, e.target.value)}
      >
        {!options.some(([value]) => value === (query.get(key) ?? "")) && (
          <option value={query.get(key) ?? ""}>
            Selected: {query.get(key)} (no matching cohort)
          </option>
        )}
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
  const exportCsv = async () => {
    if (!data) return;
    setExportError("");
    setExporting(true);
    const params = new URLSearchParams(query);
    params.set("as_of", String(data.as_of));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api/v1/track-record/export?${params}`, {
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          "Cohort export unavailable. Retry or narrow the alert window.",
        );
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "saved-checkpoints.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Export failed.");
    } finally {
      clearTimeout(timer);
      setExporting(false);
    }
  };
  const counts = data?.summary;
  return (
    <>
      <div className="brief-controls">
        {" "}
        {control(
          "days",
          "Alert window",
          [1, 3, 7, 30, 90, 365].map((n) => [
            String(n),
            `Last ${n} day${n === 1 ? "" : "s"}`,
          ]),
        )}
        {control(
          "horizon",
          "Checkpoint horizon",
          [1, 6, 24, 72].map((n) => [String(n), `+${n}h`]),
        )}
        <button
          className="button button-secondary"
          aria-haspopup="dialog"
          onClick={() => setFiltersOpen(true)}
        >
          Cohort filters
        </button>
        <button
          className="button button-secondary"
          disabled={!data || exporting}
          onClick={exportCsv}
        >
          {exporting ? "Exporting…" : "Export complete cohort (UTC CSV)"}
        </button>
      </div>
      <p className="small muted">
        Active filters:{" "}
        {Array.from(query.entries())
          .filter(([key]) => !["page", "page_size"].includes(key))
          .map(([key, value]) => `${humanKey(key)}: ${value}`)
          .join(" · ")}
      </p>
      {filtersOpen && (
        <Modal
          labelledBy="cohort-filter-title"
          onDismiss={() => setFiltersOpen(false)}
        >
          <h2 id="cohort-filter-title">Cohort filters</h2>{" "}
          <div className="cohort-filters panel">
            {control("chain", "Chain", [
              ["", "All saved chains"],
              ...["sol", "bsc", "base", "robinhood", "arc"].map(
                (n) => [n, chains[n] ?? n] as [string, string],
              ),
            ])}
            {control("preset", "Alert-time strategy", [
              ["", "All saved strategies"],
              ...["Strict", "Balanced", "Broad", "Custom", "unknown"].map(
                (n) => [n, n] as [string, string],
              ),
            ])}
            {control("revision", "Strategy revision", [
              ["", "All saved revisions"],
              ["unknown", "Unknown"],
              ...Array.from(
                new Set(
                  data?.cohorts
                    ?.map((c) => c.revision)
                    .filter((n): n is number => n != null) ?? [],
                ),
              ).map((n) => [String(n), String(n)] as [string, string]),
            ])}
            {control("score_version", "Score version", [
              ["", "All saved versions"],
              ["unknown", "Unknown"],
              ...Array.from(
                new Set(
                  data?.cohorts
                    ?.map((c) => c.score_version)
                    .filter((n): n is string => n != null) ?? [],
                ),
              ).map((n) => [n, n] as [string, string]),
            ])}
            {control("origin", "Alert origin", [
              ["", "All allowed origins"],
              ["live", "Recorded live"],
              ["unknown", "Unknown"],
            ])}
            {control("knowledge", "Availability evidence", [
              ["", "Recorded and legacy (separate cohorts)"],
              ["recorded", "Capture/delivery recorded"],
              ["legacy_unknown", "Legacy availability unknown"],
            ])}
            {control("cohort", "Exact compatible cohort", [
              ["", "All matching cohorts (coverage only if mixed)"],
              ...(data?.cohorts ?? []).map(
                (c) =>
                  [
                    c.id,
                    `${c.chain} · ${c.preset ?? "unknown"} / ${c.revision ?? "unknown"} · ${c.score_version ?? "unknown"} · ${c.origin} · ${c.knowledge}`,
                  ] as [string, string],
              ),
            ])}
            {control("checkpoint", "Checkpoint state", [
              ["", "All states"],
              ["return", "Measured return"],
              ["missing_price", "Observed, return unavailable"],
              ["unresolved", "Unresolved evidence"],
              ["pending", "Pending horizon/window"],
              ["missed", "Missed observation"],
            ])}
            <label className="select-field">
              <span>As-of cutoff (UTC)</span>
              <input
                aria-label="As-of cutoff (UTC)"
                type="datetime-local"
                step="1"
                value={
                  query.has("as_of")
                    ? new Date(Number(query.get("as_of")) * 1000)
                        .toISOString()
                        .slice(0, 19)
                    : ""
                }
                onChange={(e) =>
                  set(
                    "as_of",
                    e.target.value
                      ? String(Date.parse(e.target.value + "Z") / 1000)
                      : "",
                  )
                }
              />
            </label>
            <button
              className="text-button"
              onClick={() => navigate("/outcomes?view=checkpoints")}
            >
              Reset filters
            </button>
          </div>
          <button
            className="button button-primary"
            onClick={() => setFiltersOpen(false)}
          >
            Show results
          </button>
        </Modal>
      )}
      <Notice>
        One row is one delivered alert, including repeated tokens. Negative,
        zero, unavailable, pending and missed observations remain visible. No
        fees, slippage, executable liquidity or trading profit is assumed.
      </Notice>
      {error && (
        <Notice tone="error">
          {error}{" "}
          <button className="text-button" onClick={refresh}>
            Retry cohort
          </button>
        </Notice>
      )}
      {exportError && <Notice tone="error">{exportError}</Notice>}
      {!loading && !data && error && (
        <Empty title="Outcome data unavailable">
          Retry to load saved cohort evidence. No returns have been assumed.
        </Empty>
      )}
      {loading && !data ? (
        <Loading />
      ) : data && counts ? (
        <>
          <p className="small muted">
            {data.methodology_version} · Alert interval [{dateTime(data.since)},{" "}
            {dateTime(data.as_of)}] inclusive · cutoff {dateTime(data.as_of)}
          </p>
          {!counts.comparable && (
            <Notice tone="warning">
              Different chains, strategies, revisions, score versions, origins
              or availability histories are pooled here. Rates and medians are
              withheld. Select an exact cohort.
            </Notice>
          )}
          {counts.small_sample && (
            <Notice tone="warning">
              Small sample: {counts.returns_available} verified checkpoint
              returns, below 20. This descriptive sample does not establish
              predictive success.
            </Notice>
          )}
          <div className="stats-grid">
            <Stat
              label="Matched alerts"
              value={counts.total}
              detail="All selected states"
            />
            <Stat
              label="Observed checkpoints"
              value={`${counts.observed} / ${counts.total}`}
              detail="Observed / matched alerts"
            />
            <Stat
              label={labels.computablePriceChanges}
              value={`${counts.returns_available} / ${counts.total}`}
              detail="Available / matched alerts"
            />
            <Stat
              label={labels.medianPriceChange}
              value={
                counts.comparable
                  ? percent(counts.median_return_pct, false, true)
                  : "Withheld"
              }
              detail={`Based on ${counts.returns_available} computable returns`}
            />
          </div>
          <section className="panel">
            <h2>Coverage and return distribution</h2>
            <p>
              Positive {counts.positive} / {counts.returns_available} · Negative{" "}
              {counts.negative} / {counts.returns_available} · Exactly zero{" "}
              {counts.zero} / {counts.returns_available}. Positive-return rate:{" "}
              {counts.comparable
                ? percent(counts.positive_rate_pct)
                : "withheld for mixed cohorts"}
              .
            </p>
            <p>
              Observed without a usable return: {counts.missing_price} ·
              Unresolved: {counts.unresolved} · Pending: {counts.pending} ·
              Missed: {counts.missed}. These are not zero returns.
            </p>
            {query.has("bucket") && (
              <p>
                Distribution bucket filter is active.{" "}
                <button
                  className="text-button"
                  onClick={() => set("bucket", "")}
                >
                  Clear bucket filter
                </button>
              </p>
            )}
            <div
              className="return-distribution"
              aria-label="Observed return frequencies"
            >
              {counts.distribution.map((bin, index) => (
                <button
                  key={bin.label}
                  className="distribution-bin"
                  aria-pressed={query.get("bucket") === String(index)}
                  onClick={() => set("bucket", String(index))}
                >
                  <span>{bin.label}</span>
                  <span className="distribution-track">
                    <i
                      style={{
                        width: `${counts.returns_available ? (bin.count / counts.returns_available) * 100 : 0}%`,
                      }}
                    />
                  </span>
                  <strong>
                    {bin.count} / {counts.returns_available}
                  </strong>
                </button>
              ))}
            </div>
            <p className="small muted">
              Bars count finite, consistent observed changes only. Inspecting a
              bin applies an explicit retrospective result filter; it is not a
              recommendation.
            </p>
          </section>
          <section className="panel">
            <h2>Contributing saved checkpoints</h2>
            <p>
              Page {data.page} · {data.total} selected alerts. Open any row’s
              original alert evidence; later observations remain separate.
            </p>
            {data.items.length ? (
              <CheckpointTable items={data.items} />
            ) : (
              <Empty title="No delivered alerts match this cohort">
                Widen the alert window or reset the filters. No observations or
                returns have been fabricated.
              </Empty>
            )}
            <div className="brief-controls">
              <button
                className="button button-secondary"
                disabled={data.page <= 1}
                onClick={() => set("page", String(data.page - 1))}
              >
                Previous page
              </button>
              <button
                className="button button-secondary"
                disabled={data.page * data.page_size >= data.total}
                onClick={() => set("page", String(data.page + 1))}
              >
                Next page
              </button>
            </div>
          </section>
          <details className="panel research-disclosure">
            <summary>Cohort definitions, exclusions and limitations</summary>
            <p>{data.definition}</p>
            {data.reconciliation && (
              <p>
                Reconciliation:{" "}
                {Object.entries(data.reconciliation)
                  .map(([key, value]) => `${humanKey(key)}: ${value}`)
                  .join(" · ")}
                . The same checkpoint-accounting rules drive the bot summary;
                origin and cohort exclusions explain differing totals.
              </p>
            )}
            <h3>Exclusions</h3>
            <ul>
              {data.exclusions.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            <h3>Limits</h3>
            <ul>
              {data.limitations.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            <h3>Saved compatible cohorts</h3>
            <ul>
              {data.cohorts.map((c) => (
                <li key={c.id}>
                  {c.id} · {c.chain} / {c.preset ?? "unknown"} / revision{" "}
                  {c.revision ?? "unknown"} / score version{" "}
                  {c.score_version ?? "unknown"} / {c.origin} / {c.knowledge}:{" "}
                  {c.counts.total} alerts, {c.counts.returns_available} verified
                  returns; positive {c.counts.positive} /{" "}
                  {c.counts.returns_available}, negative {c.counts.negative} /{" "}
                  {c.counts.returns_available}.
                </li>
              ))}
            </ul>
            <p className="small muted">
              The CSV exports the entire selected cohort, not just this page,
              with UTC times and these rules. Formula-like text is escaped for
              spreadsheets.
            </p>
          </details>
        </>
      ) : null}
    </>
  );
}

export function TrackRecordPage() {
  const location = useLocation();
  const search = location.split("?")[1] ?? "";
  const checkpoints = new URLSearchParams(search).get("view") === "checkpoints";
  return (
    <>
      <PageHeading
        eyebrow="SAVED SIGNAL RESEARCH"
        title="Track record"
        description="If you had followed the radar's previous calls, which moved favourably, which failed, and by how much? Saved observations, not realized trading performance."
      />
      <nav className="journal-tabs" aria-label="Track record views">
        <Link
          href="/outcomes"
          aria-current={checkpoints ? undefined : "page"}
          className={checkpoints ? "" : "active"}
        >
          Call journal
        </Link>
        <Link
          href="/outcomes?view=checkpoints"
          aria-current={checkpoints ? "page" : undefined}
          className={checkpoints ? "active" : ""}
        >
          Checkpoint detail
        </Link>
      </nav>
      {checkpoints ? (
        <CheckpointRecordPage />
      ) : (
        <CallJournalView search={search} />
      )}
    </>
  );
}
