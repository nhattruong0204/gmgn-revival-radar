import { useState } from "react";
import { useData } from "./api";
import { CallPerformanceCard } from "./CallCard";
import type { ChartMarker } from "./Chart";
import { Empty, Icon, Loading, Notice } from "./components";
import { compact, exactTime, researchLink } from "./format";
import type { Alert, ListResponse } from "./types";

export const CALLS_PAGE_SIZE = 5;
const MARKER_LIMIT = 100;

function callsPath(
  chain: string,
  address: string,
  page: number,
  pageSize: number,
) {
  return `/api/v1/alerts?chain=${encodeURIComponent(chain)}&address=${encodeURIComponent(address)}&page=${page}&page_size=${pageSize}`;
}

/** Accessible, text-complete name for one call marker on the chart. */
export function callMarker(alert: Alert): ChartMarker {
  const p = alert.performance;
  const which =
    p?.token_call_index != null && (p.token_call_count ?? 0) > 1
      ? `Call ${p.token_call_index} of ${p.token_call_count}, alert #${alert.id}`
      : `Call #${alert.id}`;
  const cap =
    p?.call_market_cap == null
      ? "market cap at call unknown"
      : `market cap at call ${compact(p.call_market_cap, true)}`;
  return {
    id: alert.id,
    timestamp: alert.timestamp,
    label: `${which}, ${exactTime(alert.timestamp)}, ${cap}`,
    href: researchLink(`/alerts/${alert.id}`),
  };
}

/** Markers for the chart: the newest calls on this token, saved data only. */
export function useCallMarkers(chain: string, address: string) {
  const { data } = useData<ListResponse<Alert>>(
    callsPath(chain, address, 1, MARKER_LIMIT),
    false,
  );
  return (Array.isArray(data?.items) ? data.items : []).map((a) =>
    callMarker(a),
  );
}

export function TokenCalls({
  chain,
  address,
}: {
  chain: string;
  address: string;
}) {
  const [page, setPage] = useState(1);
  const { data, error, loading, refresh } = useData<ListResponse<Alert>>(
    callsPath(chain, address, page, CALLS_PAGE_SIZE),
  );
  const valid = data && Array.isArray(data.items) ? data : null;
  const pages = valid
    ? Math.max(1, Math.ceil(valid.total / valid.page_size))
    : 1;
  return (
    <section className="panel token-calls" aria-labelledby="token-calls-title">
      <div className="section-heading">
        <Icon name="alerts" />
        <h2 id="token-calls-title">Calls on this token</h2>
      </div>
      <p className="small muted">
        Confirmed sent alerts for this token, newest first. Change since each
        call uses saved market-cap observations only; it is not a prediction.
      </p>
      {error && (
        <Notice tone="error">
          {error}{" "}
          <button className="text-button" onClick={refresh}>
            Try again
          </button>
        </Notice>
      )}
      {data && !valid && (
        <Notice tone="error">
          The calls response was not in the expected format.
        </Notice>
      )}
      {loading && !data ? (
        <Loading />
      ) : valid && valid.items.length > 0 ? (
        <>
          <div className="call-card-list-grid token-calls-grid">
            {valid.items.map((a) => (
              <CallPerformanceCard
                key={a.id}
                alert={a}
                variant="compact"
                headingLevel={3}
              />
            ))}
          </div>
          <div className="token-calls-footer">
            <span>
              {valid.total} {valid.total === 1 ? "call" : "calls"} · page {page}{" "}
              of {pages}
            </span>
            {pages > 1 && (
              <nav className="token-calls-pager" aria-label="Calls pages">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  <span aria-hidden="true">←</span> Newer
                </button>
                <button
                  type="button"
                  disabled={page >= pages}
                  onClick={() => setPage(page + 1)}
                >
                  Older <span aria-hidden="true">→</span>
                </button>
              </nav>
            )}
          </div>
        </>
      ) : valid ? (
        <Empty title="No published calls for this token">
          Calls appear here after Telegram confirms delivery of an alert for
          this token.
        </Empty>
      ) : null}
    </section>
  );
}
