import { Empty, JsonGrid } from "./components";
import { dateTime, humanKey } from "./format";
import type { AdminStatus, JsonRecord } from "./types";

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}
function display(value: unknown): string {
  return value === null || value === undefined
    ? "Unknown"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
export function PreviewChanges({ result }: { result?: JsonRecord | null }) {
  const before = record(result?.before),
    after = record(result?.after);
  const keys = Object.keys(after).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
  );
  return keys.length ? (
    <div className="admin-table-wrap">
      <table className="radar-table">
        <thead>
          <tr>
            <th>Setting</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key}>
              <th>{humanKey(key)}</th>
              <td>{display(before[key])}</td>
              <td>{display(after[key])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <p>
      No strategy values change. This confirmation performs only the reviewed
      action.
    </p>
  );
}
export function AdminDiagnostics({ status }: { status: AdminStatus | null }) {
  const health = status?.health ?? {},
    scans = record(health.scans),
    discovery = record(health.discovery),
    evaluations = record(health.evaluations),
    alerts = record(health.alerts),
    schedule = record(status?.worker?.schedule);
  const history = Array.isArray(status?.scan_history)
    ? status.scan_history.map(record)
    : [];
  const candidates = Array.isArray(health.best_candidates)
    ? health.best_candidates.map(record)
    : [];
  return (
    <>
      <div className="detail-grid">
        <section className="panel">
          <h2>Scan timing</h2>
          <p>
            {schedule.running === true
              ? status?.worker?.available === true
                ? "Scan in progress"
                : "Last reported scan in progress (worker unavailable)"
              : "No scan reported running"}{" "}
            · Next due: {dateTime(schedule.next_due as number)}
          </p>
          <p>
            Last started: {dateTime(scans.last_started as number)} · Last
            completed: {dateTime(scans.last_finished as number)}
          </p>
          <JsonGrid value={record(scans.duration_seconds)} />
        </section>
        <section className="panel">
          <h2>Discovery and evaluation funnel</h2>
          <JsonGrid
            value={{
              unique_discovered_tokens: discovery.unique_tokens,
              evaluations: evaluations.count,
              eligible_evaluations: evaluations.eligible,
              alerts_sent: alerts.sent,
              scans_completed: scans.completed,
            }}
          />
          <p className="small muted">{display(discovery.counting_note)}</p>
          <p className="small muted">{display(evaluations.counting_note)}</p>
        </section>
        <section className="panel">
          <h2>Provider and data coverage</h2>
          <h3>Source snapshots by chain</h3>
          <JsonGrid value={record(discovery.snapshots_by_source)} />
          <h3>Source failure counts</h3>
          <JsonGrid value={record(discovery.source_errors)} />
          <h3>Saved evidence coverage</h3>
          <JsonGrid value={record(evaluations.coverage)} />
          <h3>Missing fields</h3>
          <JsonGrid value={record(evaluations.missing_field_counts)} />
        </section>
        <section className="panel">
          <h2>Private delivery results</h2>
          <JsonGrid value={alerts} />
          <p className="small muted">
            Pending or unknown delivery is not proof of a sent message.
            Synthetic Test Alerts appear only in command history.
          </p>
          <h3>Evaluation rejection counts</h3>
          <JsonGrid value={record(evaluations.rejection_counts)} />
        </section>
      </div>
      <section className="panel">
        <h2>Recent scan history</h2>
        {history.length ? (
          <div className="admin-table-wrap">
            <table className="radar-table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>State</th>
                  <th>Duration (s)</th>
                  <th>Processed</th>
                  <th>Errors</th>
                  <th>Sent</th>
                </tr>
              </thead>
              <tbody>
                {history.map((run) => (
                  <tr key={String(run.id)}>
                    <td>{dateTime(run.started as number)}</td>
                    <td>
                      {run.finished == null ? "Unfinished record" : "Completed"}
                    </td>
                    <td>{display(run.duration_seconds)}</td>
                    <td>{display(run.processed)}</td>
                    <td>{display(run.errors)}</td>
                    <td>{display(run.sent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No recent scans">
            No saved scans in the last 24 hours.
          </Empty>
        )}
      </section>
      <section className="panel">
        <h2>Best candidates and near misses</h2>
        {candidates.length ? (
          <div className="admin-table-wrap">
            <table className="radar-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th>Score</th>
                  <th>Evaluation</th>
                  <th>Rejections / missing evidence</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((item) => (
                  <tr key={String(item.id)}>
                    <td>
                      {display(item.symbol)} · {display(item.chain)}
                      <br />
                      <span className="small mono">
                        {display(item.contract_address)}
                      </span>
                    </td>
                    <td>{display(item.score)}</td>
                    <td>{display(item.status)}</td>
                    <td>
                      {display(item.rejection_reasons)}
                      <br />
                      {display(item.missing_fields)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No saved candidates">
            No qualifying near misses in this window.
          </Empty>
        )}
      </section>
    </>
  );
}
