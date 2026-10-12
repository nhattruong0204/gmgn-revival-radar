import { useEffect, useState } from "react";
import { navigate, useData } from "./api";
import {
  Avatar,
  Empty,
  Freshness,
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
  radarQuery,
  relative,
  shortAddress,
  tokenPath,
} from "./format";
import type { ListResponse, Meta, Observation } from "./types";
export function Radar({ search, meta }: { search: string; meta: Meta | null }) {
  const q = radarQuery(search),
    [draft, setDraft] = useState(q.get("search") ?? "");
  useEffect(() => {
    setDraft(new URLSearchParams(search).get("search") ?? "");
  }, [search]);
  const { data, error, loading, refresh, refreshedAt } = useData<
    ListResponse<Observation>
  >(`/api/v1/radar?${q}`);
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    navigate(`/?${next}`);
  };
  const sorting = (key: string) => {
    const next = new URLSearchParams(search);
    next.set("sort", key);
    next.set(
      "order",
      q.get("sort") === key && q.get("order") === "desc" ? "asc" : "desc",
    );
    next.delete("page");
    navigate(`/?${next}`);
  };
  const items = data?.items ?? [];
  return (
    <>
      <PageHeading
        eyebrow="THE COMEBACK STARTS HERE"
        title="Revival radar"
        description="Find returning activity. Follow the evidence."
        action={
          <button
            className="button button-secondary"
            onClick={refresh}
            disabled={loading}
          >
            <Icon name="refresh" className={loading ? "spin" : ""} />
            Refresh
          </button>
        }
      />
      <div className="stats-grid">
        <Stat
          label="Matching candidates"
          value={data ? data.total : "—"}
          detail="In the scanner’s tracked universe"
        />
        <Stat
          label="Confirmed on this page"
          value={
            data
              ? items.filter((o) =>
                  ["CONFIRMED_REVIVAL", "STRONG_REVIVAL"].includes(o.status),
                ).length
              : "—"
          }
          detail="Chart evidence + returning activity"
          icon="check"
          tone="positive"
        />
        <Stat
          label="Chain coverage"
          value={meta?.chains?.length ?? "—"}
          detail={
            meta?.chains?.map((c) => c.name).join(" · ") ??
            "Saved observations only"
          }
          icon="outcomes"
        />
        <Stat
          label="Last completed scan"
          value={relative(data?.last_scan_at ?? meta?.last_scan_at)}
          detail="Observation time, independent of refresh"
          icon="clock"
        />
      </div>
      <div className="radar-panel">
        <div className="panel-header">
          <div>
            <h2>
              Discover candidates{" "}
              <span className="count-pill">{data?.total ?? "—"}</span>
            </h2>
            <p>Latest saved evaluation per token</p>
          </div>
          <Freshness
            observed={data?.last_scan_at ?? meta?.last_scan_at}
            refreshed={refreshedAt}
          />
        </div>
        <div className="filters">
          <form
            className="search-box"
            onSubmit={(e) => {
              e.preventDefault();
              change("search", draft);
            }}
          >
            <Icon name="search" />
            <input
              aria-label="Search tokens"
              placeholder="Search token or contract address"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            {draft && (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => {
                  setDraft("");
                  change("search", "");
                }}
              >
                <Icon name="close" size={15} />
              </button>
            )}
            <button className="search-submit" type="submit">
              Search
            </button>
          </form>
          <label className="select-field">
            <span>Chain</span>
            <select
              aria-label="Filter chain"
              value={q.get("chain") ?? ""}
              onChange={(e) => change("chain", e.target.value)}
            >
              <option value="">All chains</option>
              {(
                meta?.chains ??
                Object.entries(chains).map(([id, name]) => ({ id, name }))
              ).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="select-field">
            <span>Stage</span>
            <select
              aria-label="Filter stage"
              value={q.get("status") ?? ""}
              onChange={(e) => change("status", e.target.value)}
            >
              <option value="">Watch & above</option>
              <option value="STRONG_REVIVAL">Strong revival</option>
              <option value="CONFIRMED_REVIVAL">Confirmed revival</option>
              <option value="REVIVING">Reviving</option>
              <option value="EARLY_REVIVAL">Early revival</option>
              <option value="WATCH">Watch</option>
              <option value="IGNORE">Below watch</option>
            </select>
          </label>
          <label className="select-field">
            <span>Minimum score</span>
            <select
              aria-label="Minimum score"
              value={q.get("min_score") ?? "40"}
              onChange={(e) => change("min_score", e.target.value)}
            >
              {[0, 40, 50, 60, 65, 70, 75, 80, 90].map((s) => (
                <option key={s} value={s}>
                  {s === 0 ? "Any score" : `${s}+`}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && (
          <Notice tone="error">
            {error}{" "}
            {data
              ? "The last loaded observations are still visible."
              : "No live data is being shown."}{" "}
            <button className="text-button" onClick={refresh}>
              Try again
            </button>
          </Notice>
        )}
        {loading && !data ? (
          <Loading />
        ) : !items.length ? (
          <Empty
            title={
              error
                ? "Data service unavailable"
                : "No candidates match these filters"
            }
          >
            {error
              ? "Connect the API and refresh to view saved scanner observations."
              : "Try a lower minimum score, another stage, or a different token search."}
          </Empty>
        ) : (
          <>
            <div className="radar-table-wrap">
              <table className="radar-table">
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Stage</th>
                    <th>
                      <button onClick={() => sorting("score")}>
                        Score{" "}
                        {q.get("sort") === "score"
                          ? q.get("order") === "desc"
                            ? "↓"
                            : "↑"
                          : ""}
                      </button>
                    </th>
                    <th>
                      <button onClick={() => sorting("market_cap")}>
                        Market cap {q.get("sort") === "market_cap" ? "↓" : ""}
                      </button>
                    </th>
                    <th>
                      <button onClick={() => sorting("liquidity")}>
                        Liquidity
                      </button>
                    </th>
                    <th>ATH drawdown</th>
                    <th>
                      <button onClick={() => sorting("volume_5m")}>
                        5m volume
                      </button>
                    </th>
                    <th>
                      <button onClick={() => sorting("timestamp")}>
                        Observed
                      </button>
                    </th>
                    <th>
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((o) => (
                    <tr key={`${o.chain}:${o.contract_address}`}>
                      <td>
                        <Link
                          className="token-link"
                          href={tokenPath(o.chain, o.contract_address)}
                        >
                          <Avatar observation={o} />
                          <span>
                            <strong>
                              {o.symbol}
                              <small className="chain-label">
                                {chains[o.chain] ?? o.chain}
                              </small>
                            </strong>
                            <small>
                              {o.name || shortAddress(o.contract_address)}
                            </small>
                          </span>
                        </Link>
                      </td>
                      <td>
                        <Stage stage={o.status} />
                        {o.eligible === false && (
                          <span className="risk-label">Blocked by filters</span>
                        )}
                      </td>
                      <td>
                        <Score value={o.score} />
                      </td>
                      <td>{compact(o.market_cap, true)}</td>
                      <td>{compact(o.liquidity, true)}</td>
                      <td className="muted">
                        {percent(o.drawdown_from_ath, true)}
                      </td>
                      <td>
                        <strong>{compact(o.volume_5m, true)}</strong>
                        <small className="table-subtext">
                          {compact(o.tx_5m)} transactions
                        </small>
                      </td>
                      <td title={dateTime(o.timestamp)}>
                        {relative(o.timestamp)}
                        {(o.missing_fields.length > 0 ||
                          o.blockers.length > 0) && (
                          <small
                            className={`table-subtext ${o.blockers.length ? "danger" : "warning-text"}`}
                          >
                            {o.blockers.length
                              ? "Risk findings"
                              : "Partial evidence"}
                          </small>
                        )}
                      </td>
                      <td>
                        <Link
                          className="detail-link"
                          aria-label={`View ${o.symbol}`}
                          href={tokenPath(o.chain, o.contract_address)}
                        >
                          <Icon name="chevron" size={16} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-cards">
              {items.map((o) => (
                <Link
                  key={`${o.chain}:${o.contract_address}`}
                  href={tokenPath(o.chain, o.contract_address)}
                  className="token-card"
                >
                  <div className="token-card-header">
                    <div className="token-link">
                      <Avatar observation={o} />
                      <span>
                        <strong>{o.symbol}</strong>
                        <small>{o.name}</small>
                      </span>
                    </div>
                    <Score value={o.score} />
                  </div>
                  <Stage stage={o.status} />
                  <div className="token-card-metrics">
                    <div>
                      <small>Market cap</small>
                      <strong>{compact(o.market_cap, true)}</strong>
                    </div>
                    <div>
                      <small>Liquidity</small>
                      <strong>{compact(o.liquidity, true)}</strong>
                    </div>
                    <div>
                      <small>ATH drawdown</small>
                      <strong>{percent(o.drawdown_from_ath, true)}</strong>
                    </div>
                  </div>
                  <div className="token-card-footer">
                    <span>
                      {chains[o.chain] ?? o.chain} · {relative(o.timestamp)}
                    </span>
                    {(o.blockers.length || o.missing_fields.length) > 0 && (
                      <span className="warning-text">
                        {o.blockers.length
                          ? "Blocked by filters"
                          : "Partial evidence"}
                      </span>
                    )}
                    <Icon name="arrow" size={16} />
                  </div>
                </Link>
              ))}
            </div>
          </>
        )}
        <div className="panel-footer">
          <span>
            Only stored observations. Public browsing never triggers a provider
            request.
          </span>
          {data && (
            <div className="pagination">
              <button
                disabled={data.page <= 1}
                onClick={() => {
                  const next = new URLSearchParams(search);
                  next.set("page", String(data.page - 1));
                  navigate(`/?${next}`);
                }}
                aria-label="Previous page"
              >
                ←
              </button>
              <span>
                {data.page} /{" "}
                {Math.max(1, Math.ceil(data.total / data.page_size))}
              </span>
              <button
                disabled={data.page * data.page_size >= data.total}
                onClick={() => {
                  const next = new URLSearchParams(search);
                  next.set("page", String(data.page + 1));
                  navigate(`/?${next}`);
                }}
                aria-label="Next page"
              >
                →
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="bottom-note">
        <Icon name="info" size={16} />
        <span>
          Scores describe observed evidence. Read the risk findings and{" "}
          <Link href="/how-it-works">methodology</Link> before interpreting a
          signal.
        </span>
      </div>
    </>
  );
}
