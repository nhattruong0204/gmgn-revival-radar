import { useState } from "react";
import { CallPerformanceCard } from "./CallCard";
import { EvidenceList, Icon, Link, ObservationStamp } from "./components";
import { explainMissing, explainReasons, explainRisks } from "./explain";
import {
  chains,
  compact,
  dateTime,
  relative,
  researchLink,
  shortAddress,
  tokenPath,
} from "./format";
import { RiskList, SecurityRows } from "./TokenDetail";
import type { Alert, AlertDetail, AlertGroup } from "./types";

/** Contract with a copy control and a manual-copy fallback. */
export function ContractCopy({
  chain,
  address,
}: {
  chain: string;
  address: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <div className="contract-copy">
      <p className="contract-address">
        <span>{chains[chain] ?? chain}</span> · <span>{address}</span>
      </p>
      <button
        type="button"
        className="address-button"
        aria-label={`Copy contract ${address}`}
        title={address}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(address);
            setState("copied");
            setTimeout(() => setState("idle"), 2000);
          } catch {
            setState("failed");
          }
        }}
      >
        {shortAddress(address)}
        <Icon name={state === "copied" ? "check" : "copy"} size={15} />
        <span>{state === "copied" ? "Copied" : "Copy address"}</span>
      </button>
      {state === "failed" && (
        <p className="small muted" role="status">
          Copy failed. Select the contract address above to copy it manually.
        </p>
      )}
    </div>
  );
}

/**
 * The identity, timing, security and coverage facts that used to sit in a stat
 * row and two panels around the call card, as one disclosure.
 */
export function CallRecord({ alert }: { alert: AlertDetail }) {
  const o = alert.observation;
  const security = o?.token?.security;
  const missing = o ? [...new Set(o.missing_fields)] : [];
  const reasons = o ? explainReasons(o) : [];
  const risks = o ? explainRisks(o) : [];
  return (
    <details className="research-disclosure call-record" open>
      <summary>Call record: contract, times, security and coverage</summary>
      <ContractCopy chain={alert.chain} address={alert.contract_address} />
      <div className="alert-meta">
        {o && <ObservationStamp observation={o} kind="signal" />}
        <span>Alert observation {dateTime(alert.timestamp)}</span>
        <span>
          Capture recorded{" "}
          {alert.recorded_at == null
            ? "unknown (legacy)"
            : dateTime(alert.recorded_at)}
        </span>
        <span>
          Delivery confirmed{" "}
          {alert.confirmed_at == null
            ? "first confirmation time unknown (legacy)"
            : dateTime(alert.confirmed_at)}
        </span>
        <span>
          Liquidity at call{" "}
          {o?.liquidity == null ? "Unknown" : compact(o.liquidity, true)}
        </span>
      </div>
      {missing.length > 0 && (
        <div className="missing-fields">
          <span>Missing evidence</span>
          {missing.map((f) => (
            <small key={f}>{explainMissing(f)}</small>
          ))}
        </div>
      )}
      <SecurityRows security={security} />
      <p className="small muted">
        Unavailable security evidence remains unknown. A score does not
        establish token safety.
      </p>
      {(reasons.length > 2 || risks.length > 2) && (
        <details className="call-record-full">
          <summary>
            Every saved reason and risk (the card shows the top two)
          </summary>
          <h3 className="call-card-sub">Reasons</h3>
          <EvidenceList items={reasons.map((r) => r.text)} />
          <h3 className="call-card-sub">Risks</h3>
          <RiskList risks={risks} />
        </details>
      )}
    </details>
  );
}

/** Caveats that apply to every card, stated once for the page. */
export function FeedNotes({ items }: { items: Alert[] }) {
  const unclassified = items.filter(
    (a) =>
      a.observation &&
      !["live", "synthetic", "test"].includes(a.observation.origin ?? ""),
  ).length;
  const noCallCap = items.filter(
    (a) => a.performance && a.performance.call_market_cap == null,
  ).length;
  return (
    <details className="research-disclosure feed-notes">
      <summary>Notes that apply to every call on this page</summary>
      <ul>
        <li>
          Only deliveries Telegram confirmed are listed. Failed, pending and
          uncertain deliveries are excluded.
        </li>
        {unclassified > 0 && (
          <li>
            Origin unclassified: {unclassified} of {items.length} calls were
            saved before the data origin was recorded. Their detail pages say
            so.
          </li>
        )}
        {noCallCap > 0 && (
          <li>
            Legacy calls: {noCallCap} saved no market cap at the call, so their
            change since call stays unknown rather than 0%.
          </li>
        )}
        <li>
          Change since call compares saved market caps. Best and worst observed
          are the highest and lowest saved values; gaps between observations are
          not filled.
        </li>
        <li>
          Reasons and risks are what was saved with each call. A score does not
          establish token safety, and nothing here is a forecast.
        </li>
      </ul>
    </details>
  );
}

export function SignalGroups({
  groups,
  items,
}: {
  groups: AlertGroup[];
  items: Alert[];
}) {
  const byId = new Map(items.map((a) => [a.id, a]));
  return (
    <div className="signal-groups">
      {groups.map((g) => {
        const calls = g.alert_ids
          .map((id) => byId.get(id))
          .filter((a): a is Alert => a !== undefined);
        const headingId = `group-${g.chain}-${g.contract_address}`;
        return (
          <section
            key={`${g.chain}:${g.contract_address}`}
            className="signal-group"
            aria-labelledby={headingId}
          >
            <header className="signal-group-head">
              <h2 id={headingId}>
                <Link
                  href={researchLink(tokenPath(g.chain, g.contract_address))}
                >
                  ${g.symbol}
                </Link>
              </h2>
              <p>
                <span>{chains[g.chain] ?? g.chain}</span> ·{" "}
                <code title={g.contract_address}>
                  {shortAddress(g.contract_address)}
                </code>
              </p>
              <p>
                <strong>
                  {g.call_count} {g.call_count === 1 ? "call" : "calls"}
                </strong>{" "}
                · latest {relative(g.latest_call_at)}
              </p>
            </header>
            <div className="call-card-list-grid">
              {calls.map((a) => (
                <CallPerformanceCard
                  key={a.id}
                  alert={a}
                  variant="compact"
                  headingLevel={3}
                />
              ))}
            </div>
            {g.truncated && (
              <p className="small muted signal-group-more">
                Showing the newest {calls.length} of {g.call_count} calls.{" "}
                <Link
                  href={researchLink(tokenPath(g.chain, g.contract_address))}
                >
                  Open the token
                </Link>{" "}
                for its history.
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
