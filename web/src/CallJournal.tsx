import { useEffect, useState, type ReactNode } from "react";
import { CallPerformanceCard, duration, signed } from "./CallCard";
import { Modal, useSmallScreen } from "./Modal";
import { navigate, useData } from "./api";
import { Empty, Link, Loading, Notice, Stage } from "./components";
import {
  chains,
  dateTime,
  humanKey,
  number,
  percent,
  researchLink,
  stageName,
  stageNames,
} from "./format";
import type { Alert, CallJournalData, JournalGroup } from "./types";

export const WINDOWS: Array<[string, string]> = [
  ["1", "Last 24 hours"],
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["365", "All saved (365 days)"],
];
export const SORTS: Array<[string, string]> = [
  ["newest", "Newest first"],
  ["worst", "Worst change first"],
  ["best", "Best change first"],
  ["mae", "Deepest worst-observed first"],
  ["mfe", "Highest best-observed first"],
  ["score", "Highest score first"],
  ["age", "Oldest first"],
];
const AGES: Array<[string, string, string]> = [
  ["lt24h", "Under 24 hours", "max_age_hours=24"],
  ["1to7d", "1 to 7 days", "min_age_hours=24&max_age_hours=168"],
  ["gt7d", "Over 7 days", "min_age_hours=168"],
];
const OUTCOMES: Array<[string, string]> = [
  ["negative", "Down since call"],
  ["positive", "Up since call"],
  ["flat", "Unchanged"],
  ["unknown", "Unknown change"],
];
const CHAIN_IDS = ["sol", "bsc", "base", "robinhood", "arc"];
const PAGE_SIZE = 25;
const GROUP_PAGE_SIZE = 100;

/** URL state for the journal; every value is validated, nothing is invented. */
export function journalQuery(search: string) {
  const input = new URLSearchParams(search);
  const out = new URLSearchParams();
  const days = input.get("days");
  out.set("days", WINDOWS.some(([v]) => v === days) ? days! : "7");
  const pick = (key: string, ok: (v: string) => boolean) => {
    const value = input.get(key);
    if (value && ok(value)) out.set(key, value);
  };
  pick("chain", (v) => CHAIN_IDS.includes(v));
  pick("stage", (v) => v in stageNames);
  pick("outcome", (v) => OUTCOMES.some(([o]) => o === v));
  pick("revision", (v) => /^(unknown|[0-9]{1,9})$/.test(v));
  pick("age", (v) => AGES.some(([a]) => a === v));
  pick("min_score", (v) => /^(100|[0-9]{1,2})$/.test(v));
  pick("max_score", (v) => /^(100|[0-9]{1,2})$/.test(v));
  pick("as_of", (v) => Number.isFinite(Number(v)) && Number(v) >= 0);
  pick("group", (v) => v === "token");
  pick("layout", (v) => v === "table");
  const sort = input.get("sort");
  out.set("sort", SORTS.some(([s]) => s === sort) ? sort! : "newest");
  const page = Number(input.get("page"));
  out.set(
    "page",
    Number.isInteger(page) && page >= 1 && page <= 10000 ? String(page) : "1",
  );
  return out;
}

/** API parameters for a validated UI query. Export omits paging. */
export function journalApiQuery(ui: URLSearchParams, exporting = false) {
  const out = new URLSearchParams();
  for (const key of [
    "days",
    "chain",
    "stage",
    "outcome",
    "revision",
    "min_score",
    "max_score",
    "as_of",
    "sort",
  ]) {
    const value = ui.get(key);
    if (value) out.set(key, value);
  }
  const age = AGES.find(([a]) => a === ui.get("age"));
  if (age) new URLSearchParams(age[2]).forEach((v, k) => out.set(k, v));
  if (!exporting) {
    const grouped = ui.get("group") === "token";
    out.set("page", grouped ? "1" : (ui.get("page") ?? "1"));
    out.set("page_size", String(grouped ? GROUP_PAGE_SIZE : PAGE_SIZE));
  }
  return out;
}

function tone(value: number | null) {
  return value == null
    ? ""
    : value > 0
      ? "positive"
      : value < 0
        ? "danger"
        : "";
}
const REASONS: Record<string, string> = {
  no_call_mc: "market cap at call not saved",
  no_later_observation: "no later observation saved",
  unspecified: "reason not recorded",
};

function Metric({
  label,
  value,
  detail,
  valueTone = "",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  valueTone?: string;
}) {
  return (
    <div className="journal-stat">
      <dt>{label}</dt>
      <dd className={valueTone}>{value}</dd>
      {detail && <div className="journal-stat-detail">{detail}</div>}
    </div>
  );
}

function Breakdown({
  title,
  rows,
  label,
}: {
  title: string;
  rows: JournalGroup[];
  label: (key: string) => string;
}) {
  return (
    <div
      className="journal-table-wrap"
      role="region"
      aria-label={title}
      tabIndex={0}
    >
      <table className="journal-breakdown">
        <caption>{title}</caption>
        <thead>
          <tr>
            {[
              "Group",
              "Calls",
              "Measurable",
              "Up",
              "Down",
              "Flat",
              "Unknown",
              "Median change",
            ].map((h) => (
              <th scope="col" key={h}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{label(r.key)}</th>
              <td>{r.calls}</td>
              <td>
                {r.measurable} / {r.calls}
              </td>
              <td>{r.positive}</td>
              <td>{r.negative}</td>
              <td>{r.flat}</td>
              <td>{r.unknown}</td>
              <td className={tone(r.median_change_pct)}>
                {signed(r.median_change_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CallTable({ items }: { items: Alert[] }) {
  return (
    <div
      className="journal-table-wrap"
      role="region"
      aria-label="Calls, dense table"
      tabIndex={0}
    >
      <table className="journal-calls">
        <caption className="sr-only">
          One row per call. Changes are market-cap changes since the call.
        </caption>
        <thead>
          <tr>
            {[
              "Token",
              "Stage",
              "Score",
              "Called",
              "Age",
              "Change since call",
              "Best",
              "Worst",
              "Latest data",
            ].map((h) => (
              <th scope="col" key={h}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((a) => {
            const p = a.performance;
            const c = p?.change_since_call_pct ?? null;
            return (
              <tr key={a.id}>
                <th scope="row">
                  <Link
                    className="call-card-title-link"
                    href={researchLink(`/alerts/${a.id}`)}
                  >
                    ${a.symbol}
                  </Link>
                  <span className="journal-sub">
                    {chains[a.chain] ?? a.chain} · #{a.id}
                    {p?.token_call_count != null && p.token_call_count > 1
                      ? ` · call ${p.token_call_index} of ${p.token_call_count}`
                      : ""}
                  </span>
                </th>
                <td>
                  <Stage stage={a.observation?.status ?? "UNKNOWN"} />
                </td>
                <td>{a.score}</td>
                <td>{dateTime(a.timestamp)}</td>
                <td>{duration(p?.elapsed_seconds)}</td>
                <td className={tone(c)}>
                  {c == null ? (
                    <>
                      Unknown
                      <span className="journal-sub">
                        {p?.change_reason === "no_call_mc"
                          ? "no market cap at call"
                          : "no later observation"}
                      </span>
                    </>
                  ) : (
                    <strong>
                      <span aria-hidden="true">
                        {c > 0 ? "▲ " : c < 0 ? "▼ " : "◆ "}
                      </span>
                      {signed(c)}
                      <span className="sr-only">
                        {c > 0 ? " up" : c < 0 ? " down" : " unchanged"}
                      </span>
                    </strong>
                  )}
                </td>
                <td className={tone(p?.mfe_pct ?? null)}>
                  {signed(p?.mfe_pct)}
                </td>
                <td className={tone(p?.mae_pct ?? null)}>
                  {signed(p?.mae_pct)}
                </td>
                <td>
                  {p?.freshness === "none"
                    ? "None saved"
                    : p?.freshness === "stale"
                      ? "Stale"
                      : "Fresh"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The dense table needs room for nine columns; jsdom (no matchMedia) counts as wide. */
function useWide() {
  const query = "(min-width: 1180px)";
  const [wide, setWide] = useState(
    () => window.matchMedia?.(query).matches ?? true,
  );
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const change = () => setWide(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  return wide;
}

function groupByToken(items: Alert[]) {
  const groups = new Map<string, Alert[]>();
  for (const item of items) {
    const key = `${item.chain}:${item.contract_address}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return Array.from(groups.entries());
}

export function CallJournalView({ search }: { search: string }) {
  const small = useSmallScreen();
  const wide = useWide();
  const ui = journalQuery(search);
  const api = journalApiQuery(ui);
  const source = useData<CallJournalData>(`/api/v1/track-record/calls?${api}`);
  const { data, error, loading, refresh } = source;
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const grouped = ui.get("group") === "token";
  const table = ui.get("layout") === "table" && wide;
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(ui);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.set("page", "1");
    navigate(`/outcomes?${next}`);
  };
  const select = (
    key: string,
    label: string,
    options: Array<[string, string]>,
    empty?: string,
  ) => (
    <label className="select-field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={ui.get(key) ?? ""}
        onChange={(e) => set(key, e.target.value)}
      >
        {empty !== undefined && <option value="">{empty}</option>}
        {ui.get(key) && !options.some(([v]) => v === ui.get(key)) && (
          <option value={ui.get(key)!}>Selected: {ui.get(key)}</option>
        )}
        {options.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
  const revisions = Array.from(
    new Set([...(data?.summary.revisions ?? []), ui.get("revision") ?? ""]),
  ).filter(Boolean);
  const filterControls = (
    <div className="journal-filters">
      {select(
        "chain",
        "Chain",
        CHAIN_IDS.map((c) => [c, chains[c]]),
        "All chains",
      )}
      {select(
        "stage",
        "Stage at call",
        Object.entries(stageNames).map(([k, v]) => [k, v]),
        "All stages",
      )}
      {select("outcome", "Outcome since call", OUTCOMES, "All outcomes")}
      {select(
        "revision",
        "Strategy revision",
        revisions.map((r) => [
          r,
          r === "unknown" ? "Unknown" : `Revision ${r}`,
        ]),
        "All revisions (pooled)",
      )}
      {select(
        "age",
        "Call age",
        AGES.map(([v, t]) => [v, t]),
        "Any age",
      )}
      {select(
        "min_score",
        "Score at least",
        [50, 60, 70, 75, 80, 85, 90].map((n) => [String(n), String(n)]),
        "Any",
      )}
      {select(
        "max_score",
        "Score at most",
        [59, 69, 79, 89, 99].map((n) => [String(n), String(n)]),
        "Any",
      )}
      <button
        className="text-button"
        onClick={() => navigate(`/outcomes?days=${ui.get("days")}`)}
      >
        Reset filters
      </button>
    </div>
  );
  const active = [
    "chain",
    "stage",
    "outcome",
    "revision",
    "age",
    "min_score",
    "max_score",
  ]
    .filter((k) => ui.get(k))
    .map((k) => `${humanKey(k)}: ${ui.get(k)}`);
  const exportCsv = async () => {
    if (!data) return;
    setExportError("");
    setExporting(true);
    const params = journalApiQuery(ui, true);
    params.set("as_of", String(data.as_of));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(
        `/api/v1/track-record/calls/export?${params}`,
        {
          credentials: "same-origin",
          signal: controller.signal,
        },
      );
      if (!response.ok)
        throw new Error("Call export unavailable. Retry or narrow the window.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "saved-calls.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed.");
    } finally {
      clearTimeout(timer);
      setExporting(false);
    }
  };
  const s = data?.summary;
  const cp = s?.checkpoint_24h;
  const items = data?.items ?? [];
  return (
    <>
      <div className="journal-toolbar">
        {select("days", "Window", WINDOWS)}
        {small ? (
          <button
            className="button button-secondary"
            aria-haspopup="dialog"
            onClick={() => setFiltersOpen(true)}
          >
            Filters{active.length ? ` (${active.length})` : ""}
          </button>
        ) : (
          <details className="journal-more">
            <summary>
              Filters{active.length ? ` (${active.length})` : ""}
            </summary>
            {filterControls}
          </details>
        )}
        <button
          className="button button-secondary"
          disabled={!data || exporting}
          onClick={exportCsv}
        >
          {exporting ? "Exporting…" : "Export calls (UTC CSV)"}
        </button>
      </div>
      {small && filtersOpen && (
        <Modal
          className="filter-drawer"
          labelledBy="journal-filters-title"
          onDismiss={() => setFiltersOpen(false)}
        >
          <h2 id="journal-filters-title">Filter calls</h2>
          {filterControls}
          <button
            className="button button-primary"
            onClick={() => setFiltersOpen(false)}
          >
            Show results
          </button>
        </Modal>
      )}
      {active.length > 0 && (
        <p className="small muted">Active filters: {active.join(" · ")}</p>
      )}
      {error && (
        <Notice tone="error">
          {error} {data ? "The last loaded calls are still shown. " : ""}
          <button className="text-button" onClick={refresh}>
            Retry
          </button>
        </Notice>
      )}
      {exportError && <Notice tone="error">{exportError}</Notice>}
      {loading && !data ? (
        <Loading />
      ) : !data || !s ? (
        <Empty title="Call journal unavailable">
          Retry to load saved calls. No performance has been assumed.
        </Empty>
      ) : (
        <>
          <section className="journal-summary" aria-label="Call summary">
            <dl className="journal-stats">
              <Metric
                label="Calls"
                value={s.calls}
                detail="delivered alerts in window"
              />
              <Metric
                label="Median change since call"
                value={signed(s.median_change_pct)}
                valueTone={tone(s.median_change_pct)}
                detail={`of ${s.measurable} measurable calls`}
              />
              <Metric
                label="Mean change since call"
                value={signed(s.mean_change_pct)}
                valueTone={tone(s.mean_change_pct)}
                detail={`of ${s.measurable} measurable calls`}
              />
              <Metric
                label="Measurable"
                value={`${s.measurable} / ${s.calls}`}
                detail="change since call known"
              />
              <Metric
                label="Up"
                value={`${s.positive} / ${s.measurable}`}
                valueTone={s.positive ? "positive" : ""}
                detail={
                  s.positive_rate_pct == null
                    ? "no measurable calls"
                    : `${percent(s.positive_rate_pct)} of measurable`
                }
              />
              <Metric
                label="Down"
                value={`${s.negative} / ${s.measurable}`}
                valueTone={s.negative ? "danger" : ""}
                detail={`Unchanged ${s.flat} / ${s.measurable}`}
              />
              <Metric
                label="Unknown"
                value={`${s.unknown} / ${s.calls}`}
                detail={
                  Object.entries(s.unknown_reasons)
                    .filter(([, n]) => n)
                    .map(([k, n]) => `${n} ${REASONS[k] ?? k}`)
                    .join(" · ") || "none"
                }
              />
              <Metric
                label="Median best / worst observed"
                value={
                  <>
                    <span className={tone(s.mfe_median_pct)}>
                      {signed(s.mfe_median_pct)}
                    </span>
                    {" / "}
                    <span className={tone(s.mae_median_pct)}>
                      {signed(s.mae_median_pct)}
                    </span>
                  </>
                }
                detail={`of ${s.mfe_known} calls with later data`}
              />
            </dl>
            <div className="journal-mid">
              <div className="journal-notices">
                {s.small_sample && (
                  <Notice tone="warning">
                    Small sample: {s.measurable} measurable{" "}
                    {s.measurable === 1 ? "call" : "calls"}, below{" "}
                    {s.small_sample_threshold}. These are descriptive counts and
                    do not establish predictive success.
                  </Notice>
                )}
                {s.mixed_configurations && (
                  <Notice tone="info">
                    Mixed configurations: revisions {s.revisions.join(", ")} are
                    pooled. Figures describe the calls together and are not a
                    comparison of strategies. Choose a revision to view one
                    exact cohort.
                  </Notice>
                )}
              </div>
              <div className="journal-distribution">
                <h2>Distribution of change since call</h2>
                <ul aria-label="Change since call, calls per bin">
                  {s.distribution.map((bin, index) => (
                    <li
                      key={bin.label}
                      className={index < 2 ? "down" : index > 2 ? "up" : ""}
                    >
                      <span>{bin.label}</span>
                      <span className="distribution-track" aria-hidden="true">
                        <i
                          style={{
                            width: `${s.measurable ? (bin.count / s.measurable) * 100 : 0}%`,
                          }}
                        />
                      </span>
                      <strong>
                        {bin.count} / {s.measurable}
                      </strong>
                    </li>
                  ))}
                </ul>
                <p className="small muted">
                  {s.unknown} unknown{" "}
                  {s.unknown === 1 ? "call is" : "calls are"} not placed in any
                  bin.
                </p>
              </div>
            </div>
            <details className="journal-breakdowns">
              <summary>Breakdown by stage and by revision</summary>
              <Breakdown
                title="By stage at call"
                rows={s.by_stage}
                label={stageName}
              />
              <Breakdown
                title="By strategy revision"
                rows={s.by_revision}
                label={(k) => (k === "unknown" ? "Unknown" : `Revision ${k}`)}
              />
            </details>
            {cp && (
              <p className="small muted">
                +24h price change (not market cap): {cp.returns_available}{" "}
                measured of {cp.total} calls · median{" "}
                {signed(cp.median_return_pct)} · pending {cp.pending} · missed{" "}
                {cp.missed} · unavailable {cp.missing_price + cp.unresolved}.
              </p>
            )}
            <p className="small muted journal-caveat">
              Saved observations, not executed trades: fees, slippage and
              liquidity are ignored. Repeat calls on one token are separate
              calls, not independent trades.
              {s.stale_latest > 0 &&
                ` ${s.stale_latest} of ${s.calls} calls have a stale latest observation.`}
            </p>
          </section>

          <section className="journal-list" aria-label="Calls">
            <div className="journal-list-head">
              <h2>
                Calls{" "}
                <span className="muted">
                  ({data.total}
                  {grouped && data.total > items.length
                    ? `, first ${items.length} shown`
                    : ""}
                  )
                </span>
              </h2>
              <div className="journal-list-controls">
                {select("sort", "Sort", SORTS)}
                <label className="check-field">
                  <input
                    type="checkbox"
                    checked={grouped}
                    onChange={(e) =>
                      set("group", e.target.checked ? "token" : "")
                    }
                  />
                  <span>Group by token</span>
                </label>
                {!small && (
                  <div
                    className="segmented-control"
                    role="group"
                    aria-label="Layout"
                  >
                    {(
                      [
                        ["", "List"],
                        ["table", "Dense table"],
                      ] as const
                    ).map(([value, text]) => (
                      <button
                        key={text}
                        className={
                          table === (value === "table") ? "active" : ""
                        }
                        aria-pressed={table === (value === "table")}
                        onClick={() => set("layout", value)}
                      >
                        {text}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {!items.length ? (
              <Empty title="No calls match these filters">
                Widen the window or reset the filters. Nothing has been
                estimated.
              </Empty>
            ) : grouped ? (
              groupByToken(items).map(([key, calls]) => (
                <div className="journal-group" key={key}>
                  <h3>
                    ${calls[0].symbol}{" "}
                    <span className="call-card-ident">
                      · {chains[calls[0].chain] ?? calls[0].chain} ·{" "}
                      {calls.length} {calls.length === 1 ? "call" : "calls"}
                    </span>
                  </h3>
                  {table ? (
                    <CallTable items={calls} />
                  ) : (
                    <ul className="call-row-list">
                      {calls.map((a) => (
                        <li key={a.id}>
                          <CallPerformanceCard alert={a} variant="row" />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))
            ) : table ? (
              <CallTable items={items} />
            ) : (
              <ul className="call-row-list">
                {items.map((a) => (
                  <li key={a.id}>
                    <CallPerformanceCard alert={a} variant="row" />
                  </li>
                ))}
              </ul>
            )}
            {!grouped && (
              <div className="brief-controls">
                <button
                  className="button button-secondary"
                  disabled={data.page <= 1}
                  onClick={() => set("page", String(data.page - 1))}
                >
                  Previous page
                </button>
                <span className="small muted">
                  Page {data.page} of{" "}
                  {Math.max(1, Math.ceil(data.total / data.page_size))}
                </span>
                <button
                  className="button button-secondary"
                  disabled={data.page * data.page_size >= data.total}
                  onClick={() => set("page", String(data.page + 1))}
                >
                  Next page
                </button>
              </div>
            )}
            {grouped && data.total > items.length && (
              <p className="small muted">
                Grouping shows the first {items.length} of {data.total} calls in
                this sort. Narrow the filters to group the rest.
              </p>
            )}
          </section>
          <details className="panel research-disclosure">
            <summary>Definitions, reconciliation and limitations</summary>
            <p>{data.definition}</p>
            <p>
              Reconciliation:{" "}
              {Object.entries(data.reconciliation)
                .map(([k, v]) => `${humanKey(k)}: ${number(v)}`)
                .join(" · ")}
              . Without filters the call count equals the Signals total for the
              same cutoff.
            </p>
            <ul>
              {data.limitations.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            <p className="small muted">
              The CSV exports every selected call, not just this page, with UTC
              times. Formula-like text is escaped for spreadsheets.
            </p>
          </details>
        </>
      )}
    </>
  );
}
