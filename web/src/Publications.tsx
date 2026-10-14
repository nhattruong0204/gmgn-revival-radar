import { navigate, useData, useLocation } from "./api";
import {
  Avatar,
  Empty,
  Freshness,
  ObservationStamp,
  Icon,
  Link,
  Loading,
  Notice,
  PageHeading,
  Score,
  Stage,
  Stat,
} from "./components";
import {
  chains,
  compact,
  dateTime,
  percent,
  relative,
  tokenPath,
  researchLink,
  researchReturn,
} from "./format";
import type { Alert, AlertDetail, ListResponse, Report } from "./types";
import { CallPerformanceCard } from "./CallCard";
import { SignalEvidence } from "./TokenDetail";
import { ResearchBrief } from "./ResearchBrief";
import { CheckpointTable } from "./TrackRecord";
export function Alerts({ id }: { id?: string }) {
  const location = useLocation();
  const params = new URLSearchParams(location.split("?")[1]);
  const page = Math.min(
    10000,
    Math.max(1, Math.trunc(Number(params.get("page"))) || 1),
  );
  const setPage = (value: number) => navigate(`/alerts?page=${value}`);
  const feed = useData<ListResponse<Alert>>(
    !id ? `/api/v1/alerts?page=${page}&page_size=25` : null,
  );
  const detail = useData<AlertDetail>(
    id ? `/api/v1/alerts/${encodeURIComponent(id)}` : null,
  );
  const source = id ? detail : feed;
  return (
    <>
      <PageHeading
        eyebrow="SIGNAL ARCHIVE"
        title={
          id ? `${detail.data?.symbol ?? "Token"} alert` : "Confirmed alerts"
        }
        description={
          id
            ? "The exact evidence saved when this alert was sent."
            : "Signals with confirmed Telegram delivery. Saved at the moment of the alert."
        }
        action={
          id ? (
            <Link className="button button-secondary" href="/alerts">
              ← All alerts
            </Link>
          ) : (
            <button className="button button-secondary" onClick={feed.refresh}>
              <Icon name="refresh" />
              Refresh
            </button>
          )
        }
      />
      {id && (
        <Link className="back-link" href={researchReturn()}>
          ← Back to research
        </Link>
      )}
      {source.error && (
        <Notice tone="error">
          {source.error}{" "}
          <button className="text-button" onClick={source.refresh}>
            Try again
          </button>
        </Notice>
      )}
      {source.loading && !source.data ? (
        <Loading />
      ) : id && detail.data ? (
        <>
          <CallPerformanceCard alert={detail.data} variant="full" />
          <p className="contract-address">
            {chains[detail.data.chain] ?? detail.data.chain} ·{" "}
            {detail.data.contract_address}
          </p>
          <div className="alert-meta">
            <Stage stage={detail.data.observation?.status ?? "UNKNOWN"} />
            {detail.data.observation && (
              <ObservationStamp
                observation={detail.data.observation}
                kind="signal"
              />
            )}
            <span>Alert observation {dateTime(detail.data.timestamp)}</span>
            <span>
              Capture recorded{" "}
              {detail.data.recorded_at == null
                ? "unknown (legacy)"
                : dateTime(detail.data.recorded_at)}
            </span>
            <span>
              Delivery confirmed{" "}
              {detail.data.confirmed_at == null
                ? "first confirmation time unknown (legacy)"
                : dateTime(detail.data.confirmed_at)}
            </span>
            <span>
              {detail.data.preset ?? "Unlabeled strategy"} · revision{" "}
              {detail.data.revision ?? "unknown"}
            </span>
            <Link
              className="external-link"
              href={researchLink(
                tokenPath(detail.data.chain, detail.data.contract_address),
              )}
            >
              Current token observation
              <Icon name="arrow" size={15} />
            </Link>
          </div>
          <div className="stats-grid">
            <Stat
              label="Alert-time score"
              value={<Score value={detail.data.score} />}
              detail="Original confirmed signal"
            />
            <Stat
              label="Alert-time liquidity"
              value={compact(detail.data.observation?.liquidity, true)}
            />
            <Stat
              label="Alert evidence observed"
              value={dateTime(detail.data.observation?.timestamp)}
              detail="Saved independently from current markets"
            />
          </div>
          {detail.data.observation &&
            (!detail.data.observation.token ||
              !detail.data.observation.signal) && (
              <Notice tone="warning">
                Original detail evidence is unavailable for this legacy alert.
                Unknown fields have not been replaced with current metrics.
              </Notice>
            )}
          {(detail.data.checkpoint_research?.length ?? 0) > 0 && (
            <section className="panel">
              <h2>Saved checkpoint coverage</h2>
              <CheckpointTable items={detail.data.checkpoint_research!} />
            </section>
          )}
          {detail.data.original_brief_id ? (
            <ResearchBrief
              chain={detail.data.chain}
              address={detail.data.contract_address}
              versionId={detail.data.original_brief_id}
            />
          ) : (
            <Notice>
              There is no supported original brief association for this alert.
              The later archive is separate; no association was reconstructed.
            </Notice>
          )}
          {detail.data.observation ? (
            <>
              <SignalEvidence observation={detail.data.observation} />
              <details className="research-disclosure">
                <summary>Separate research brief archive</summary>
                <p>
                  These versions may be later than the alert. They do not
                  replace its original evidence above.
                </p>
                <ResearchBrief
                  key={detail.data.id}
                  chain={detail.data.chain}
                  address={detail.data.contract_address}
                />
              </details>
            </>
          ) : (
            <Notice tone="warning">
              Original evidence is unavailable for this legacy alert. It has not
              been replaced with current evidence.
            </Notice>
          )}
          <section className="panel">
            <h2>Observed after the alert</h2>
            <p className="muted">
              Recorded prices at supported horizons. These are signal returns,
              not executed-trade P&amp;L.
            </p>
            {detail.data.outcomes?.length ? (
              <div className="structure-grid">
                {detail.data.outcomes.map((v, i) => (
                  <div key={i}>
                    <span>+{v.horizon_hours ?? v.hours ?? "?"}h</span>
                    <strong
                      className={
                        v.return_pct != null && v.return_pct >= 0
                          ? "positive"
                          : "muted"
                      }
                    >
                      {percent(v.return_pct, false, true)}
                    </strong>
                    <small>
                      {v.observed_at
                        ? dateTime(v.observed_at)
                        : "Observation unavailable"}
                    </small>
                  </div>
                ))}
              </div>
            ) : (
              <Empty title="No outcome observations yet">
                A future stored market observation may complete the supported
                horizons.
              </Empty>
            )}
          </section>
        </>
      ) : id ? (
        <Empty title="Alert evidence unavailable">
          This alert may not be public or its saved data is unavailable. Retry
          the request or return to the confirmed alert list.
        </Empty>
      ) : (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Sent signals</h2>
              <p>Failed, pending and uncertain deliveries are excluded</p>
            </div>
            <Freshness observed={feed.data?.last_scan_at} />
          </div>
          {feed.data?.items.length ? (
            <>
              <div className="call-card-list-grid">
                {feed.data.items.map((a) => (
                  <CallPerformanceCard
                    key={a.id}
                    alert={a}
                    variant="compact"
                    headingLevel={3}
                  />
                ))}
              </div>
              <div className="panel-footer">
                <span>{feed.data.total} confirmed sent alerts</span>
                <div className="pagination">
                  <button
                    disabled={page === 1}
                    onClick={() => setPage(page - 1)}
                  >
                    ←
                  </button>
                  <span>Page {page}</span>
                  <button
                    disabled={page * 25 >= feed.data.total}
                    onClick={() => setPage(page + 1)}
                  >
                    →
                  </button>
                </div>
              </div>
            </>
          ) : (
            <Empty title="No confirmed sent alerts">
              Alerts appear here after Telegram confirms delivery.
            </Empty>
          )}
        </section>
      )}
    </>
  );
}
export function Reports() {
  const location = useLocation();
  const selected = Number(
    new URLSearchParams(location.split("?")[1]).get("hours"),
  );
  const hours = [1, 4, 12, 24, 72, 168].includes(selected) ? selected : 24;
  const setHours = (value: number) => navigate(`/reports?hours=${value}`);
  const { data, error, loading, refresh } = useData<Report>(
    `/api/v1/reports?hours=${hours}`,
  );
  return (
    <>
      <PageHeading
        eyebrow="THE BIGGER PICTURE"
        title="Market reports"
        description="Top Solana candidates, ranked by their peak observed revival score."
        action={
          <button className="button button-secondary" onClick={refresh}>
            <Icon name="refresh" />
            Refresh
          </button>
        }
      />
      <div className="report-toolbar">
        <span className="chain-chip">Solana only</span>
        <div className="segmented-control">
          {[1, 4, 12, 24, 72, 168].map((h) => (
            <button
              key={h}
              className={hours === h ? "active" : ""}
              onClick={() => setHours(h)}
            >
              {h === 168 ? "7d" : `${h}h`}
            </button>
          ))}
        </div>
        <span className="muted small">
          Top 10 · Up to seven days of evidence
        </span>
      </div>
      {error && (
        <Notice tone="error">
          {error}{" "}
          <button className="text-button" onClick={refresh}>
            Try again
          </button>
        </Notice>
      )}
      {data?.multiple_configurations && (
        <Notice tone="warning">
          This window includes multiple strategy configurations. Compare saved
          revisions before interpreting changes in scores.
        </Notice>
      )}
      <section className="panel report-panel">
        <div className="panel-header">
          <div>
            <h2>Window leaders</h2>
            <p>
              {data
                ? `${dateTime(data.since)} — ${dateTime(data.until)}`
                : "Reading saved rankings"}
            </p>
          </div>
          <span className="muted small">Peak / Latest</span>
        </div>
        {loading && !data ? (
          <Loading />
        ) : !data?.entries.length ? (
          <Empty title="No report observations in this window">
            Completed scans will populate the Solana ranking.
          </Empty>
        ) : (
          <div className="report-list">
            {data.entries.map((e, i) => (
              <div
                className="report-entry"
                key={`${e.peak.chain}:${e.peak.contract_address}`}
              >
                <div
                  className="report-row"
                  key={`${e.peak?.chain}:${e.peak?.contract_address}`}
                >
                  <span className="rank-number">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <Link
                    className="token-link"
                    href={researchLink(
                      tokenPath(e.peak.chain, e.peak.contract_address),
                    )}
                  >
                    <Avatar observation={e.peak} />
                    <span>
                      <strong>{e.peak.symbol}</strong>
                      <small>{e.peak.name ?? "Solana"}</small>
                    </span>
                  </Link>
                  <Stage stage={e.peak.status} />
                  <ObservationStamp observation={e.peak} />
                  <div className="report-scores">
                    <strong className={e.peak.score >= 75 ? "positive" : ""}>
                      {e.peak.score}
                    </strong>
                    <span>/</span>
                    <strong>{e.latest.score}</strong>
                  </div>
                  <div className="report-market">
                    <strong>{compact(e.latest.market_cap, true)}</strong>
                    <small>Latest market cap</small>
                  </div>
                  <Link
                    className="detail-link"
                    aria-label={`View ${e.peak.symbol}`}
                    href={researchLink(
                      tokenPath(e.peak.chain, e.peak.contract_address),
                    )}
                  >
                    <Icon name="chevron" />
                  </Link>
                </div>
                <details className="report-evidence">
                  <summary>
                    Peak and latest evidence · {e.observations ?? "Unknown"}{" "}
                    saved observations
                    {(e.peak.blockers.length > 0 ||
                      e.latest.blockers.length > 0) && (
                      <span className="danger"> · Known blockers present</span>
                    )}
                    {(e.peak.missing_fields.length > 0 ||
                      e.latest.missing_fields.length > 0) && (
                      <span className="warning-text"> · Missing evidence</span>
                    )}
                  </summary>
                  {(typeof e.observations !== "number" ||
                    e.observations < 3) && (
                    <Notice tone="warning">
                      Sparse sample: these observations do not establish
                      continuous performance.
                    </Notice>
                  )}
                  {[
                    ["Peak", e.peak],
                    ["Latest", e.latest],
                  ].map(([label, value]) => {
                    const o = value as import("./types").Observation;
                    return (
                      <section key={String(label)} className="panel">
                        <h3>
                          {String(label)} saved evaluation · {o.score}/100
                        </h3>
                        <p>
                          {dateTime(o.timestamp)} ·{" "}
                          {o.preset ?? "Unlabeled strategy"} · revision{" "}
                          {o.revision ?? "unknown"}
                        </p>
                        <Freshness
                          label="Evaluation observed"
                          observed={o.timestamp}
                        />
                        <SignalEvidence observation={o} />
                      </section>
                    );
                  })}
                </details>
              </div>
            ))}
          </div>
        )}
        <div className="panel-footer">
          Peak and latest are saved evaluations. Sparse observations do not
          establish continuous performance.
        </div>
      </section>
      <details className="research-disclosure">
        <summary>Report window & collection totals</summary>
        <div className="stats-grid">
          <Stat
            label="Evaluations"
            value={compact(data?.evaluations)}
            detail="In completed scans"
          />
          <Stat
            label="Unique tokens"
            value={compact(data?.unique_tokens)}
            detail="Observed in this window"
          />
          <Stat
            label="Completed scans"
            value={compact(data?.completed_scans)}
            detail={`${data?.incomplete_scans ?? "—"} incomplete scans excluded`}
            icon="check"
          />
          <Stat
            label="Latest completion"
            value={relative(data?.last_finished)}
            detail={data?.timezone ?? "Saved source timezone"}
            icon="clock"
          />
        </div>
      </details>
    </>
  );
}
export { TrackRecordPage as OutcomePage } from "./TrackRecord";
export function Methodology() {
  return (
    <>
      <PageHeading
        eyebrow="EVIDENCE BEFORE CONVICTION"
        title="How the radar works"
        description="A clear view of what the scanner observes, and what each score means."
      />
      <div className="method-hero">
        <div className="method-radar">
          <img src="/revival-radar.png" alt="" />
        </div>
        <div>
          <span className="eyebrow">FIND THE COMEBACK</span>
          <h2>
            From quiet base
            <br />
            to returning activity.
          </h2>
          <p>
            OnchainFlow tracks established tokens after a deep decline. It looks
            for a mature base, renewed participation and market structure that
            supports a potential revival.
          </p>
        </div>
      </div>
      <div className="method-grid">
        {[
          [
            "01",
            "Discover",
            "The bot discovers tokens from enabled GMGN sources and revisits its tracked universe. Public pages display stored observations from completed scans.",
          ],
          [
            "02",
            "Evaluate",
            "Setup measures recovery conditions. Trigger measures returning activity. Confirmation reflects cached closed-candle structure. Older unknown dimensions stay unknown.",
          ],
          [
            "03",
            "Qualify",
            "Eligibility filters, risk findings and missing data accompany every score. A strong score can coexist with a filter blocker or unavailable security evidence.",
          ],
          [
            "04",
            "Observe",
            "Confirmed sent Telegram alerts enter the public archive. Later saved market observations support the outcome horizons; no executed trades are implied.",
          ],
        ].map(([n, title, text]) => (
          <section className="panel method-card" key={n}>
            <span className="method-number">{n}</span>
            <h2>{title}</h2>
            <p>{text}</p>
          </section>
        ))}
      </div>
      <section className="panel">
        <h2>From watch to revival</h2>
        <div className="stage-explainer">
          {[
            ["IGNORE", "Below the configured monitoring requirements."],
            [
              "WATCH",
              "A candidate worth monitoring; evidence is still developing.",
            ],
            [
              "EARLY_REVIVAL",
              "Returning activity is present, with limited confirmation.",
            ],
            [
              "REVIVING",
              "Returning activity and developing structure meet the saved strategy.",
            ],
            [
              "CONFIRMED_REVIVAL",
              "A mature base and supporting chart evidence meet the strategy.",
            ],
            [
              "STRONG_REVIVAL",
              "Stronger evidence across the configured revival criteria.",
            ],
          ].map(([stage, text]) => (
            <div key={stage}>
              <Stage stage={stage} />
              <p>{text}</p>
            </div>
          ))}
        </div>
        <p className="small muted">
          Stage thresholds depend on the saved strategy and revision. Historical
          observations keep the configuration that produced them.
        </p>
      </section>
      <section className="panel">
        <h2>Know the coverage</h2>
        <p>
          Observation time tells you when evidence was collected. Page refresh
          time tells you when your browser last read it. Token charts leave gaps
          visible, and market reports cover Solana only, with a ten-token
          ranking and at most seven days of history.
        </p>
        <p>
          Only tracked candidates are covered, not the entire market. Unknown
          security fields are unavailable evidence. Scores are heuristic rule
          matches, not calibrated probabilities. They summarize observations and
          do not certify safety or predict future returns.
        </p>
      </section>
    </>
  );
}
