import { Icon, Link, Score, Stage } from "./components";
import { labels } from "./labels";
import { explainReasons, explainRisks } from "./explain";
import {
  chains,
  compact,
  exactTime,
  gmgnLink,
  relative,
  researchLink,
  tokenPath,
} from "./format";
import type { Alert, AlertDetail, CallPerformance } from "./types";

export const CHECKPOINT_HORIZONS = [1, 6, 24, 72] as const;
type CheckpointState = "observed" | "pending" | "missed" | "unavailable";
interface Checkpoint {
  horizon: number;
  state: CheckpointState;
  returnPct: number | null;
  observedAt?: number | null;
}

/** Signed percent with a real minus sign; unknown stays "Unknown", never 0%. */
export function signed(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unknown";
  const text = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  }).format(Math.abs(value));
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${text}%`;
}
function direction(value: number) {
  return value > 0
    ? { word: "up", sign: "positive", mark: "▲" }
    : value < 0
      ? { word: "down", sign: "danger", mark: "▼" }
      : { word: "unchanged", sign: "", mark: "◆" };
}
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "Unknown";
  if (seconds < 60) return "under 1 minute";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

function checkpoints(
  alert: Alert | AlertDetail,
  p: CallPerformance,
): Checkpoint[] {
  const detail = alert as AlertDetail;
  return CHECKPOINT_HORIZONS.map((horizon): Checkpoint => {
    const row = detail.checkpoint_research?.find(
      (c) => c.horizon_hours === horizon,
    );
    if (row) {
      if (row.return_pct != null)
        return {
          horizon,
          state: "observed",
          returnPct: row.return_pct,
          observedAt: row.observed_at,
        };
      if (row.state === "pending")
        return { horizon, state: "pending", returnPct: null };
      if (row.state === "missed")
        return { horizon, state: "missed", returnPct: null };
      return { horizon, state: "unavailable", returnPct: null };
    }
    const outcome = detail.outcomes?.find(
      (o) => (o.horizon_hours ?? o.hours) === horizon,
    );
    if (outcome?.return_pct != null)
      return {
        horizon,
        state: "observed",
        returnPct: outcome.return_pct,
        observedAt: outcome.observed_at,
      };
    const early =
      p.elapsed_seconds == null || p.elapsed_seconds < horizon * 3600;
    return {
      horizon,
      state: early ? "pending" : "unavailable",
      returnPct: null,
    };
  });
}
const stateText: Record<Exclude<CheckpointState, "observed">, string> = {
  pending: "Pending",
  missed: "Missed",
  unavailable: "Unavailable",
};

export function CallPerformanceCard({
  alert,
  variant = "full",
  headingLevel = 2,
  hideTokenLink = false,
}: {
  alert: Alert | AlertDetail;
  variant?: "full" | "compact" | "row";
  headingLevel?: 2 | 3;
  /** Hide 'View token' when the card is already shown on that token's page. */
  hideTokenLink?: boolean;
}) {
  const p = alert.performance;
  const o = alert.observation;
  const small = variant === "compact";
  const Heading = `h${headingLevel}` as "h2" | "h3";
  const chain = chains[alert.chain] ?? alert.chain;
  const callPage = researchLink(`/alerts/${alert.id}`);
  const tokenPage = researchLink(
    tokenPath(alert.chain, alert.contract_address),
  );
  const change = p?.change_since_call_pct;
  const dir = typeof change === "number" ? direction(change) : null;
  const reasons = o ? explainReasons(o, 2) : [];
  const risks = o ? explainRisks(o).slice(0, 2) : [];
  const title = `$${alert.symbol}`;
  if (variant === "row")
    return (
      <CallRow
        alert={alert}
        title={title}
        chain={chain}
        callPage={callPage}
        dir={dir}
      />
    );
  const bestWorst = p ? (
    <>
      <div>
        <dt>Best observed</dt>
        <dd className={p.mfe_pct != null && p.mfe_pct > 0 ? "positive" : ""}>
          {signed(p.mfe_pct)}
        </dd>
      </div>
      <div>
        <dt>Worst observed</dt>
        <dd className={p.mae_pct != null && p.mae_pct < 0 ? "danger" : ""}>
          {signed(p.mae_pct)}
        </dd>
      </div>
    </>
  ) : null;
  const actions = (
    <nav className="call-card-actions" aria-label={`Actions for ${title}`}>
      {!hideTokenLink && (
        <Link className="call-action" href={tokenPage}>
          View token
          <Icon name="arrow" size={14} />
        </Link>
      )}
      <a
        className="call-action"
        href={gmgnLink(alert.chain, alert.contract_address)}
        target="_blank"
        rel="noopener noreferrer"
      >
        Open GMGN
        <Icon name="external" size={14} />
      </a>
      {small && (
        <Link className="call-action" href={callPage}>
          Original call
          <Icon name="chevron" size={14} />
        </Link>
      )}
    </nav>
  );
  const body = (
    <>
      <div className="call-card-config">
        <Stage stage={o?.status ?? "UNKNOWN"} />
        <div className="call-card-score">
          <span>Score</span>
          <Score value={alert.score} />
        </div>
        <span>
          {alert.preset ?? "Unlabeled strategy"} · revision{" "}
          {alert.revision ?? "unknown"}
        </span>
      </div>

      <div className="call-card-why">
        <section aria-label="Top reasons">
          <h3 className="call-card-sub">Why it was called</h3>
          {reasons.length ? (
            <ul className="call-card-list">
              {reasons.map((r) => (
                <li key={r.key}>
                  <Icon name="check" size={14} />
                  <span>{r.text}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No reasons were saved with this call.</p>
          )}
        </section>
        <section aria-label="Top risks">
          <h3 className="call-card-sub">Risks to weigh</h3>
          {risks.length ? (
            <ul className="call-card-list risks">
              {risks.map((r) => (
                <li key={r.key}>
                  <Icon name="warning" size={14} />
                  <span>
                    <b className="sr-only">Risk: </b>
                    {r.text}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              No risks were saved with this call. That does not mean the token
              is safe.
            </p>
          )}
        </section>
      </div>
    </>
  );
  return (
    <article
      className={`call-card call-card-${variant}`}
      aria-label={`Call performance for ${title}, call ${alert.id}`}
    >
      <header className="call-card-head">
        <Heading>
          {small ? (
            <Link href={callPage} className="call-card-title-link">
              {title}
            </Link>
          ) : (
            title
          )}
          <span className="call-card-ident">
            {" "}
            · {chain} · #{alert.id}
          </span>
        </Heading>
        {p?.token_call_count != null &&
          p.token_call_count > 1 &&
          p.token_call_index != null && (
            <span className="call-card-badge">
              Call {p.token_call_index} of {p.token_call_count} on this token
            </span>
          )}
      </header>

      {!p ? (
        <p className="call-card-unknown">
          Performance since the call is unavailable for this alert. Nothing has
          been estimated.
        </p>
      ) : (
        <>
          <section className="call-card-hero" aria-label="Market cap change">
            <div className="call-card-caps">
              <span className="call-card-caption call-card-caps-label">
                {labels.marketCap}
              </span>
              {p.call_market_cap == null ? (
                <>
                  <strong className="call-card-unknown-cap">
                    Market cap at call unknown
                  </strong>
                  <span className="call-card-caption">
                    Latest market cap{" "}
                    {p.latest_market_cap == null
                      ? "unavailable"
                      : compact(p.latest_market_cap, true)}
                  </span>
                </>
              ) : (
                <strong>
                  <span className="sr-only">Market cap at call </span>
                  {compact(p.call_market_cap, true)}
                  <span aria-hidden="true"> → </span>
                  <span className="sr-only"> latest market cap </span>
                  {p.latest_market_cap == null
                    ? "—"
                    : compact(p.latest_market_cap, true)}
                </strong>
              )}
            </div>
            {dir && typeof change === "number" ? (
              <div className={`call-card-change ${dir.sign}`}>
                <span aria-hidden="true">{dir.mark}</span>{" "}
                <strong>{signed(change)}</strong>{" "}
                <span>{dir.word} since call</span>
              </div>
            ) : (
              <div className="call-card-change unknown">
                <strong>Change since call unknown</strong>
                <span>
                  {p.change_reason === "no_call_mc"
                    ? "Market cap at call was not saved for this call."
                    : "No later observation has been saved yet."}
                </span>
              </div>
            )}
          </section>

          <dl className="call-card-meta">
            <div>
              <dt>Since the call</dt>
              <dd>{duration(p.elapsed_seconds)}</dd>
            </div>
            <div className={`call-card-fresh fresh-${p.freshness}`}>
              <dt>Latest observation</dt>
              <dd>
                {p.freshness === "none" || p.latest_observed_at == null ? (
                  <>
                    <Icon name="clock" size={14} /> None saved after the call
                  </>
                ) : (
                  <>
                    <Icon
                      name={p.freshness === "stale" ? "warning" : "check"}
                      size={14}
                    />{" "}
                    {p.freshness === "stale" ? "Stale" : "Fresh"} ·{" "}
                    {relative(p.latest_observed_at)}
                    <time
                      className="call-card-exact"
                      dateTime={new Date(
                        p.latest_observed_at * 1000,
                      ).toISOString()}
                    >
                      {exactTime(p.latest_observed_at)}
                    </time>
                  </>
                )}
              </dd>
            </div>
            {!small && bestWorst}
          </dl>
          {!small && (
            <p className="call-card-note">
              Best and worst are the highest and lowest saved market cap
              relative to the call, across {p.observations} saved{" "}
              {p.observations === 1 ? "observation" : "observations"}. Either
              can be negative if the token only fell; gaps between observations
              are not filled.
            </p>
          )}

          {!small && (
            <section aria-label="Price change at checkpoints">
              <h3 className="call-card-sub">
                {labels.priceChange} at checkpoints
              </h3>
              <ol className="call-card-checkpoints">
                {checkpoints(alert, p).map((c) => (
                  <li
                    key={c.horizon}
                    className={`cp-${c.state}`}
                    title={labels.checkpointPriceChange(c.horizon)}
                  >
                    <span>+{c.horizon}h</span>
                    {c.state === "observed" ? (
                      <strong
                        className={
                          (c.returnPct ?? 0) > 0
                            ? "positive"
                            : (c.returnPct ?? 0) < 0
                              ? "danger"
                              : ""
                        }
                      >
                        {signed(c.returnPct)}
                        <small>
                          {(c.returnPct ?? 0) > 0
                            ? " up"
                            : (c.returnPct ?? 0) < 0
                              ? " down"
                              : " unchanged"}
                        </small>
                      </strong>
                    ) : (
                      <em>{stateText[c.state]}</em>
                    )}
                    {c.state === "observed" && (
                      <small className="cp-time">
                        {c.observedAt != null ? (
                          <>
                            Observed{" "}
                            <time
                              dateTime={new Date(
                                c.observedAt * 1000,
                              ).toISOString()}
                            >
                              {exactTime(c.observedAt)}
                            </time>
                          </>
                        ) : (
                          "Observation time unavailable"
                        )}
                      </small>
                    )}
                  </li>
                ))}
              </ol>
              <p className="call-card-note">{labels.checkpointNote}</p>
            </section>
          )}
        </>
      )}

      {small ? (
        <details className="call-card-more">
          <summary>
            <span>Score {alert.score} · reasons, risks &amp; best/worst</span>
          </summary>
          {bestWorst && (
            <dl className="call-card-meta call-card-extremes">{bestWorst}</dl>
          )}
          {body}
          {actions}
        </details>
      ) : (
        <>
          {body}
          {actions}
        </>
      )}
    </article>
  );
}

function unknownReason(p: CallPerformance) {
  return p.change_reason === "no_call_mc"
    ? "Market cap at call not saved"
    : "No later observation saved";
}
function shortReason(p: CallPerformance) {
  return p.change_reason === "no_call_mc"
    ? "no market cap at call"
    : "no later data";
}

/** Dense one-call row for journals: change, best/worst, age and stage in ~2 lines. */
function CallRow({
  alert,
  title,
  chain,
  callPage,
  dir,
}: {
  alert: Alert | AlertDetail;
  title: string;
  chain: string;
  callPage: string;
  dir: ReturnType<typeof direction> | null;
}) {
  const p = alert.performance;
  const change = p?.change_since_call_pct;
  return (
    <article
      className="call-row"
      aria-label={`Call performance for ${title}, call ${alert.id}`}
    >
      <div className="call-row-id">
        <Link href={callPage} className="call-card-title-link">
          {title}
        </Link>
        <span className="call-card-ident">
          {chain} · #{alert.id}
        </span>
        {p?.token_call_count != null &&
          p.token_call_count > 1 &&
          p.token_call_index != null && (
            <span className="call-card-badge">
              Call {p.token_call_index} of {p.token_call_count}
            </span>
          )}
      </div>
      {!p ? (
        <div className="call-row-change unknown">
          <strong>Performance unavailable</strong>
        </div>
      ) : dir && typeof change === "number" ? (
        <div className={`call-row-change ${dir.sign}`}>
          <span aria-hidden="true">{dir.mark}</span>
          <strong>{signed(change)}</strong>
          <span>{dir.word} since call</span>
        </div>
      ) : (
        <div className="call-row-change unknown">
          <strong>Change unknown</strong>
          <span title={unknownReason(p)}>{shortReason(p)}</span>
        </div>
      )}
      {p && (
        <dl className="call-row-meta">
          <div className="call-row-caps">
            <dt>Market cap</dt>
            <dd>
              {p.call_market_cap == null
                ? "unknown"
                : compact(p.call_market_cap, true)}
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> latest </span>
              {p.latest_market_cap == null
                ? "—"
                : compact(p.latest_market_cap, true)}
            </dd>
          </div>
          <div>
            <dt>Best</dt>
            <dd
              className={p.mfe_pct != null && p.mfe_pct > 0 ? "positive" : ""}
            >
              {signed(p.mfe_pct)}
            </dd>
          </div>
          <div>
            <dt>Worst</dt>
            <dd className={p.mae_pct != null && p.mae_pct < 0 ? "danger" : ""}>
              {signed(p.mae_pct)}
            </dd>
          </div>
          <div className={`call-card-fresh fresh-${p.freshness}`}>
            <dt>Age</dt>
            <dd>
              {duration(p.elapsed_seconds)}
              {p.freshness === "stale" && " · stale"}
              {p.freshness === "none" && " · no later data"}
            </dd>
          </div>
        </dl>
      )}
      <div className="call-row-config">
        <Stage stage={alert.observation?.status ?? "UNKNOWN"} />
        <span>
          Score <strong>{alert.score}</strong>/100 · rev{" "}
          {alert.revision ?? "unknown"}
          <span className="sr-only">
            , {alert.preset ?? "unlabeled"} strategy
          </span>
        </span>
      </div>
    </article>
  );
}
