import { useState } from "react";
import { useData } from "./api";
import { Empty, Loading, Notice } from "./components";
import { dateTime, humanKey } from "./format";
import type { BriefClaim, BriefPage, ResearchBrief as Brief } from "./types";

export function briefDiff(before: Brief, after: Brief) {
  const facts = (b: Brief) =>
    Object.fromEntries([
      ["Outcome", b.outcome],
      ["Strategy revision", b.strategy_revision],
      ["Strategy", b.strategy],
      ["Score version", b.score_version],
      ["Evidence as of", dateTime(b.as_of)],
      ["Saved at", dateTime(b.saved_at)],
      ...b.sources.flatMap((source) => [
        [`Source time: ${source.kind}`, dateTime(source.observed_at)],
        [
          `Cache fetched: ${source.kind}`,
          source.fetched_at == null
            ? "Not recorded"
            : dateTime(source.fetched_at),
        ],
        [
          `Cache expiry: ${source.kind}`,
          source.expires_at == null
            ? "Not recorded"
            : dateTime(source.expires_at),
        ],
        [`Coverage: ${source.kind}`, source.coverage],
        [`Origin: ${source.kind}`, source.origin],
        ...Object.entries(source.values).map(([key, value]) => [
          `${source.kind}: ${key}`,
          value,
        ]),
        [`Closed bars: ${source.kind}`, JSON.stringify(source.candles)],
      ]),
      ...(["support", "counter_evidence", "unresolved"] as const).flatMap(
        (group) =>
          b[group].map((c) => [
            `${group}: ${c.key}`,
            `${c.text}${c.value != null ? ` · ${c.value}` : ""}`,
          ]),
      ),
      ...b.scenarios.map((s) => [
        `Scenario: ${s.kind}`,
        JSON.stringify({ text: s.text, level: s.level }),
      ]),
    ]);
  const left = facts(before),
    right = facts(after);
  return [...new Set([...Object.keys(left), ...Object.keys(right)])]
    .filter((key) => left[key] !== right[key])
    .map((key) => ({
      key,
      before: left[key] ?? "Not recorded",
      after: right[key] ?? "Not recorded",
    }));
}

export function ResearchBrief({
  chain,
  address,
  asOf,
  versionId,
}: {
  chain: string;
  address: string;
  asOf?: number;
  versionId?: string;
}) {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Brief | null>(null);
  const [compare, setCompare] = useState<Brief | null>(null);
  const feed = useData<BriefPage>(
    `/api/v1/tokens/${encodeURIComponent(chain)}/${encodeURIComponent(address)}/briefs?page=${page}&page_size=10${asOf == null ? "" : `&as_of=${asOf}`}${versionId ? `&version_id=${encodeURIComponent(versionId)}` : ""}`,
  );
  const versions = feed.data?.items ?? [];
  const current = selected ?? versions[0];
  const prior = compare;
  const prefix = `brief-${current?.id ?? "empty"}`;
  const sourceAnchor = (id: string) => `${prefix}-${id.replaceAll(":", "-")}`;
  const revealSource = (id: string) => {
    const target = document.getElementById(sourceAnchor(id));
    const disclosure = target?.closest("details");
    if (disclosure) disclosure.open = true;
    target?.focus();
  };
  const claims = (heading: string, rows: BriefClaim[], empty: string) => (
    <section>
      <h3>{heading}</h3>
      {rows.length ? (
        <ul>
          {rows.map((c) => (
            <li key={c.key}>
              <span>
                {humanKey(c.text)}
                {c.value != null && typeof c.value === "number"
                  ? ` · ${c.value}`
                  : ""}
              </span>{" "}
              <a
                href={`#${sourceAnchor(c.source_id)}`}
                onClick={() => revealSource(c.source_id)}
              >
                Source: {c.field}
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{empty}</p>
      )}
    </section>
  );
  return (
    <section
      className="panel research-brief"
      aria-label="Versioned research brief"
    >
      <h2>Research brief</h2>
      <p className="muted">
        Saved facts and conditional scenarios. No entry instruction, price
        target or probability.
      </p>
      {feed.error && (
        <Notice tone="error">
          {feed.error}{" "}
          <button className="text-button" onClick={feed.refresh}>
            Retry briefs
          </button>
        </Notice>
      )}
      {feed.loading && !feed.data ? (
        <Loading />
      ) : !current ? (
        feed.error ? null : (
          <Empty title="No immutable brief saved yet">
            Briefs begin with evaluations recorded after this feature is
            enabled. Legacy evidence has not been reconstructed into historical
            versions.
          </Empty>
        )
      ) : (
        <>
          <div className="brief-controls">
            <label>
              Saved version
              <select
                aria-label="Saved version"
                value={current.id}
                onChange={(e) => {
                  setSelected(
                    versions.find((b) => b.id === e.target.value) ?? current,
                  );
                  setCompare(null);
                }}
              >
                {!versions.some((b) => b.id === current.id) && (
                  <option value={current.id}>
                    {dateTime(current.as_of)} · {current.id.slice(0, 8)}
                  </option>
                )}
                {versions.map((b) => (
                  <option key={b.id} value={b.id}>
                    {dateTime(b.as_of)} · {b.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Compare with
              <select
                aria-label="Compare with"
                value={compare?.id ?? ""}
                onChange={(e) =>
                  setCompare(
                    versions.find((b) => b.id === e.target.value) ?? null,
                  )
                }
              >
                <option value="">Choose a saved version</option>
                {prior && !versions.some((b) => b.id === prior.id) && (
                  <option value={prior.id}>
                    {dateTime(prior.as_of)} · {prior.id.slice(0, 8)}
                  </option>
                )}
                {versions
                  .filter((b) => b.id !== current.id)
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {dateTime(b.as_of)} · {b.id.slice(0, 8)}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <p>
            <strong>
              {humanKey(current.outcome)} / conditional research case
            </strong>{" "}
            · Evaluation {dateTime(current.as_of)}
          </p>
          <p className="small muted">
            {current.brief_version} · {current.strategy ?? "Unknown strategy"} ·
            strategy revision {current.strategy_revision ?? "unknown"} · score
            version {current.score_version ?? "unknown"} · saved{" "}
            {dateTime(current.saved_at)} · origin {current.origin}
          </p>
          {Date.now() / 1000 - current.as_of > 86400 && (
            <Notice tone="warning">
              Historical brief: this saved evidence is more than 24 hours old.
            </Notice>
          )}
          <div className="brief-evidence">
            {claims(
              "Supporting facts",
              current.support,
              "No supported positive facts were saved.",
            )}
            {claims(
              "Counter-evidence",
              current.counter_evidence,
              "No counter-evidence was recorded; this is not a safety finding.",
            )}
            {claims(
              "Coverage & unresolved evidence",
              current.unresolved,
              "No unresolved fields were recorded for these brief rules.",
            )}
          </div>
          <h3>What to monitor</h3>
          <ul>
            {current.scenarios.map((s) => (
              <li key={s.kind}>
                <strong>{humanKey(s.kind)}: </strong>
                {s.text}{" "}
                {s.level != null && <span>Observed level: {s.level}. </span>}
                <a
                  href={`#${sourceAnchor(s.source_id)}`}
                  onClick={() => revealSource(s.source_id)}
                >
                  Source: {s.field}
                </a>
              </li>
            ))}
          </ul>
          {prior && (
            <section aria-label="Version changes">
              <h3>Version changes</h3>
              <p>
                From {prior.id.slice(0, 8)} ({dateTime(prior.as_of)}) to{" "}
                {current.id.slice(0, 8)} ({dateTime(current.as_of)}). Each
                source retains its own observation and cache times.
              </p>
              {briefDiff(prior, current).length ? (
                <ul>
                  {briefDiff(prior, current).map((d) => (
                    <li key={d.key}>
                      <strong>{humanKey(d.key)}</strong>
                      {d.key.startsWith("Closed bars:") ? (
                        <details className="research-disclosure">
                          <summary>
                            Inspect changed closed-candle inputs
                          </summary>
                          <pre>
                            Before: {String(d.before)}
                            {"\n"}After: {String(d.after)}
                          </pre>
                        </details>
                      ) : (
                        <>
                          <div>Before: {String(d.before)}</div>
                          <div>After: {String(d.after)}</div>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>
                  No rule fact or scenario changes. Inspect the versions’ source
                  timestamps below.
                </p>
              )}
              <details className="research-disclosure">
                <summary>Compared version source evidence</summary>
                <pre>{JSON.stringify(prior.sources, null, 2)}</pre>
              </details>
            </section>
          )}
          <details className="research-disclosure">
            <summary>Source evidence and exact version IDs</summary>
            <p>
              Brief {current.id} · evaluation {current.evaluation_id} · scan{" "}
              {current.scan_id}. Provider identity and price quote currency were
              not retained and remain unknown. Hourly volume, where present, is
              USD.
            </p>
            {current.sources.map((s) => (
              <section key={s.id} id={sourceAnchor(s.id)} tabIndex={-1}>
                <h3>{humanKey(s.kind)}</h3>
                <p>
                  {s.coverage} · origin {s.origin} · observed{" "}
                  {dateTime(s.observed_at)}
                </p>
                <p>
                  Cache fetched:{" "}
                  {s.fetched_at == null
                    ? "Not applicable"
                    : dateTime(s.fetched_at)}{" "}
                  · expires:{" "}
                  {s.expires_at == null
                    ? "Not applicable"
                    : dateTime(s.expires_at)}
                </p>
                <pre>
                  {JSON.stringify(
                    { values: s.values, candles: s.candles },
                    null,
                    2,
                  )}
                </pre>
              </section>
            ))}
          </details>
        </>
      )}
      {(feed.data?.total ?? 0) > 10 && (
        <div className="brief-controls">
          <button
            className="button button-secondary"
            disabled={page === 1}
            onClick={() => {
              setPage((p) => p - 1);
            }}
          >
            Newer versions
          </button>
          <span>
            Page {page} · {feed.data?.total} versions
          </span>
          <button
            className="button button-secondary"
            disabled={page * 10 >= (feed.data?.total ?? 0)}
            onClick={() => {
              setPage((p) => p + 1);
            }}
          >
            Older versions
          </button>
        </div>
      )}
    </section>
  );
}
