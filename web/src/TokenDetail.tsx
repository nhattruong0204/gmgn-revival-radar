import { labels } from "./labels";
import { useState } from "react";
import { useSmallScreen } from "./Modal";
import { useData } from "./api";
import {
  Avatar,
  Empty,
  EvidenceList,
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
import { ObservedChart } from "./Chart";
import { CandleWorkspace } from "./CandleWorkspace";
import { ResearchBrief } from "./ResearchBrief";
import {
  chains,
  compact,
  dateTime,
  humanKey,
  number,
  percent,
  price,
  shortAddress,
  researchReturn,
} from "./format";
import {
  explainMissing,
  explainReasons,
  explainRisks,
  type Risk,
} from "./explain";
import type { History, Observation, TokenResponse } from "./types";

const RISK_LIMIT = 3;
function RiskList({ risks }: { risks: Risk[] }) {
  const group = (items: Risk[]) =>
    (["danger", "warning"] as const).map(
      (tone) =>
        items.some((r) => r.tone === tone) && (
          <EvidenceList
            key={tone}
            items={items.filter((r) => r.tone === tone).map((r) => r.text)}
            tone={tone === "danger" ? "danger" : "warning-text"}
          />
        ),
    );
  return (
    <>
      {group(risks.slice(0, RISK_LIMIT))}
      {risks.length > RISK_LIMIT && (
        <details className="research-disclosure">
          <summary>
            {risks.length - RISK_LIMIT} more risk finding
            {risks.length - RISK_LIMIT === 1 ? "" : "s"}
          </summary>
          {group(risks.slice(RISK_LIMIT))}
        </details>
      )}
    </>
  );
}
export function SignalEvidence({
  observation: o,
}: {
  observation: Observation;
}) {
  const security = o.token?.security,
    structure = o.signal?.structure;
  const reasons = explainReasons(o),
    risks = explainRisks(o);
  return (
    <>
      <div className="detail-grid">
        <section className="panel">
          <div className="section-heading">
            <Icon name="bolt" />
            <h2>Why it’s on the radar</h2>
          </div>
          <EvidenceList
            items={reasons.map((r) => r.text)}
            empty="No positive contributions were saved for this evaluation. Inspect the saved rule contributions in the score breakdown below."
          />
        </section>
        <section className="panel">
          <div className="section-heading">
            <Icon name="warning" />
            <h2>Risk & data coverage</h2>
          </div>
          <RiskList risks={risks} />
          {o.missing_fields.length > 0 && (
            <div className="missing-fields">
              <span>Missing evidence</span>
              {[...new Set(o.missing_fields)].map((f) => (
                <small key={f}>{explainMissing(f)}</small>
              ))}
            </div>
          )}
          <dl className="risk-grid">
            <div>
              <dt>Mint authority renounced</dt>
              <dd
                className={security?.mint_renounced === false ? "danger" : ""}
              >
                {security?.mint_renounced == null
                  ? "Unknown"
                  : security.mint_renounced
                    ? "Yes"
                    : "No"}
              </dd>
            </div>
            <div>
              <dt>Freeze authority renounced</dt>
              <dd
                className={security?.freeze_renounced === false ? "danger" : ""}
              >
                {security?.freeze_renounced == null
                  ? "Unknown"
                  : security.freeze_renounced
                    ? "Yes"
                    : "No"}
              </dd>
            </div>
            <div>
              <dt>Top 10 concentration</dt>
              <dd>{percent(security?.top10_ratio, true)}</dd>
            </div>
            <div>
              <dt>Dangerous flag</dt>
              <dd className={security?.dangerous ? "danger" : ""}>
                {security?.dangerous == null
                  ? "Unknown"
                  : security.dangerous
                    ? "Present"
                    : "Not reported"}
              </dd>
            </div>
          </dl>
          <p className="small muted">
            Unavailable security evidence remains unknown. A score does not
            establish token safety.
          </p>
        </section>
      </div>
      <details className="panel research-disclosure score-disclosure">
        <summary>Score breakdown and components</summary>
        <div className="dimension-grid">
          {[
            ["Setup", o.setup_score, "Base maturity and recovery conditions"],
            ["Trigger", o.trigger_score, "Returning market activity"],
            [
              "Confirmation",
              o.confirmation_score,
              "Closed-candle structural evidence",
            ],
          ].map(([label, value, description]) => (
            <div className="dimension" key={String(label)}>
              <span>{label}</span>
              <Score value={value as number | null | undefined} large />
              <p>{description}</p>
            </div>
          ))}
        </div>
        <div className="components-list">
          {Object.entries(o.signal?.components ?? {}).map(([key, v]) => (
            <div key={key}>
              <span>
                {humanKey(key)} <code>{key}</code>
              </span>
              <strong>
                {v > 0 ? "+" : ""}
                {v}
              </strong>
            </div>
          ))}
        </div>
        {(o.blockers.length > 0 ||
          o.missing_fields.length > 0 ||
          o.warnings.length > 0) && (
          <p className="small muted">
            Saved rule keys:{" "}
            {[
              ...o.blockers.map((k) => `blocker ${k}`),
              ...o.missing_fields.map((k) => `missing ${k}`),
              ...o.warnings.map((k) => `warning ${k}`),
            ].join(" · ")}
          </p>
        )}
      </details>
      <section className="panel">
        <div className="section-heading">
          <Icon name="outcomes" />
          <h2>Market structure</h2>
        </div>
        {structure?.available ? (
          <div className="structure-grid">
            {[
              ["Base duration", `${number(structure.base_duration_hours, 1)}h`],
              [
                "Base detected",
                structure.base_detected ? "Observed" : "Unconfirmed",
              ],
              [
                "Higher low",
                structure.higher_low_detected ? "Observed" : "Unconfirmed",
              ],
              [
                "Higher high",
                structure.higher_high_detected ? "Observed" : "Unconfirmed",
              ],
              [
                "Breakout",
                structure.breakout_detected ? "Observed" : "Unconfirmed",
              ],
              [
                "Retest",
                structure.retest_detected ? "Observed" : "Unconfirmed",
              ],
            ].map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <strong className={v === "Observed" ? "positive" : ""}>
                  {v}
                </strong>
              </div>
            ))}
          </div>
        ) : (
          <Notice tone="warning">
            Chart structure was unavailable at this observation. It has not been
            inferred from the score.
          </Notice>
        )}
      </section>
    </>
  );
}
export function TokenDetail({
  chain,
  address,
}: {
  chain: string;
  address: string;
}) {
  const small = useSmallScreen();
  const encoded = `${encodeURIComponent(chain)}/${encodeURIComponent(address)}`;
  const { data, error, loading, refresh } = useData<TokenResponse>(
    `/api/v1/tokens/${encoded}`,
  );
  const history = useData<History>(
    `/api/v1/tokens/${encoded}/history?hours=168`,
  );
  const [metric, setMetric] = useState<
      "price" | "market_cap" | "score" | "candles"
    >("price"),
    [copied, setCopied] = useState(false),
    [copyError, setCopyError] = useState(false);
  const o = data?.observation;
  const marketMetrics = o ? (
    <div className="stats-grid">
      <Stat
        label="Revival score"
        value={
          <>
            <span className="positive">{o.score}</span>
            <small> / 100</small>
          </>
        }
        detail="Observed evidence, not a probability"
      />
      <Stat
        label={labels.marketCap}
        value={compact(o.market_cap, true)}
        detail={`${percent(o.drawdown_from_ath, true)} below ATH`}
        icon="outcomes"
      />
      <Stat
        label="Liquidity"
        value={compact(o.liquidity, true)}
        detail={`${compact(o.holders)} holders`}
        icon="reports"
      />
      <Stat
        label="5m activity"
        value={compact(o.volume_5m, true)}
        detail={`${compact(o.tx_5m)} transactions · ${percent(o.token?.price_change_1h, false, true)} price / 1h`}
        icon="bolt"
      />
    </div>
  ) : null;
  return (
    <>
      <Link className="back-link" href={researchReturn()}>
        <span>←</span> Back to research
      </Link>
      {error && (
        <Notice tone="error">
          {error}{" "}
          <button className="text-button" onClick={refresh}>
            Try again
          </button>
        </Notice>
      )}
      {loading && !data ? (
        <Loading />
      ) : !o ? (
        <Empty title="No saved evaluation for this token">
          This token may not be in the scanner’s tracked universe yet.
        </Empty>
      ) : (
        <>
          <PageHeading
            eyebrow={`${chains[o.chain] ?? o.chain} / TOKEN INTELLIGENCE`}
            title={o.name || o.symbol}
            description={`Saved evidence for ${o.symbol}. Updated independently from page refresh.`}
            action={
              <div className="token-title-badge">
                <Avatar observation={o} />
                <Stage stage={o.status} />
                {small && <Score value={o.score} />}
                <ObservationStamp observation={o} />
              </div>
            }
          />
          {copyError && (
            <Notice tone="warning">
              Copy failed. Select the contract address below to copy it
              manually.
            </Notice>
          )}
          <p className="contract-address">
            <span>{chains[o.chain] ?? o.chain}</span> ·{" "}
            <span>{o.contract_address}</span>
          </p>
          <div className="token-meta-row">
            <button
              className="address-button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(o.contract_address);
                  setCopied(true);
                  setCopyError(false);
                  setTimeout(() => setCopied(false), 2000);
                } catch {
                  setCopied(false);
                  setCopyError(true);
                }
              }}
              aria-label={`Copy contract ${o.contract_address}`}
              title={o.contract_address}
            >
              {shortAddress(o.contract_address)}
              <Icon name={copied ? "check" : "copy"} size={15} />
              <span>{copied ? "Copied" : "Copy address"}</span>
            </button>

            <span className="config-pill">
              {o.preset ?? "Unlabeled strategy"} · rev {o.revision ?? "unknown"}
            </span>
            {Object.entries(data?.links ?? {})
              .filter(([, url]) => url && url.startsWith("https://"))
              .map(([label, url]) => (
                <a
                  className="external-link"
                  key={label}
                  href={url!}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {humanKey(label)}
                  <Icon name="external" size={14} />
                </a>
              ))}
          </div>
          {o.eligible === false && (
            <Notice tone="warning">
              This observation did not pass strategy filters. Review its
              blockers before interpreting the score.
            </Notice>
          )}
          {!small && marketMetrics}
          {history.data?.truncated && (
            <Notice tone="warning">
              History is limited to the latest saved points. Earlier
              observations in this window are omitted.
            </Notice>
          )}
          <section className="panel chart-panel">
            <div className="panel-header">
              <div>
                <h2>Observed history</h2>
                <p>Seven days of saved evidence</p>
              </div>
              <div className="segmented-control">
                {(["price", "market_cap", "score", "candles"] as const).map(
                  (m) => (
                    <button
                      key={m}
                      className={metric === m ? "active" : ""}
                      aria-pressed={metric === m}
                      onClick={() => setMetric(m)}
                    >
                      {m === "market_cap"
                        ? labels.marketCap
                        : m === "candles"
                          ? "1h candles"
                          : humanKey(m)}
                    </button>
                  ),
                )}
              </div>
            </div>
            {history.error && (
              <Notice tone="error">
                {history.error}{" "}
                <button className="text-button" onClick={history.refresh}>
                  Retry chart
                </button>
              </Notice>
            )}
            {history.loading && !history.data ? (
              <Loading />
            ) : history.data ? (
              metric === "candles" ? (
                <CandleWorkspace history={history.data} />
              ) : (
                <ObservedChart
                  label={
                    metric === "price"
                      ? "Observed price"
                      : metric === "market_cap"
                        ? "Observed market cap"
                        : "Revival score"
                  }
                  money={metric !== "score"}
                  score={metric === "score"}
                  points={
                    metric === "score"
                      ? history.data.scores.map((s) => ({
                          timestamp: s.timestamp,
                          value: s.score,
                        }))
                      : history.data.snapshots.map((s) => ({
                          timestamp: s.timestamp,
                          value: s[metric],
                        }))
                  }
                />
              )
            ) : null}
          </section>
          {small && (
            <details className="research-disclosure">
              <summary>Saved market metrics</summary>
              {marketMetrics}
            </details>
          )}
          <ResearchBrief key={encoded} chain={chain} address={address} />
          <section className="panel">
            <h2>Returning activity</h2>
            <p className="small muted">
              Ratios use the saved evaluation’s matching-window baseline.
              Missing activity evidence remains unknown.
            </p>
            <div className="structure-grid">
              <div>
                <span>5m volume acceleration</span>
                <strong>
                  {typeof o.signal?.acceleration?.volume_acceleration_5m ===
                  "number"
                    ? `${number(o.signal.acceleration.volume_acceleration_5m, 2)}×`
                    : "Unknown"}
                </strong>
              </div>
              <div>
                <span>5m transaction acceleration</span>
                <strong>
                  {typeof o.signal?.acceleration?.tx_acceleration_5m ===
                  "number"
                    ? `${number(o.signal.acceleration.tx_acceleration_5m, 2)}×`
                    : "Unknown"}
                </strong>
              </div>
              <div>
                <span>Returning activity observed</span>
                <strong>
                  {o.signal?.returning_activity == null
                    ? "Unknown"
                    : o.signal.returning_activity
                      ? "Yes"
                      : "No"}
                </strong>
              </div>
            </div>
          </section>
          <SignalEvidence observation={o} />
          {data?.latest_market &&
            data.latest_market.timestamp !== o.timestamp && (
              <section className="panel">
                <h2>Later market observation</h2>
                <p className="muted">
                  Saved {dateTime(data.latest_market.timestamp)}. This does not
                  replace the evidence behind the score above.
                </p>
                <div className="structure-grid">
                  <div>
                    <span>Price</span>
                    <strong>{price(data.latest_market.price)}</strong>
                  </div>
                  <div>
                    <span>{labels.marketCap}</span>
                    <strong>
                      {compact(data.latest_market.market_cap, true)}
                    </strong>
                  </div>
                  <div>
                    <span>Liquidity</span>
                    <strong>
                      {compact(data.latest_market.liquidity, true)}
                    </strong>
                  </div>
                </div>
              </section>
            )}
        </>
      )}
    </>
  );
}
