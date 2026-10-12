import { useEffect, useState } from "react";
import { ApiError, DEMO, request, useData } from "./api";
import {
  Empty,
  Icon,
  JsonGrid,
  Link,
  Loading,
  Notice,
  PageHeading,
  Stat,
} from "./components";
import { dateTime, humanKey, relative } from "./format";
import type {
  AdminSettings,
  AdminStatus,
  Command,
  JsonRecord,
  Session,
} from "./types";
type DesiredAction =
  "settings" | "reset" | "pause" | "resume" | "scan" | "test";
const terminal = new Set(["succeeded", "failed", "unknown"]);
async function settleCommand(
  initial: Command,
  signal: AbortSignal,
): Promise<Command> {
  let command = initial;
  for (let n = 0; n < 30 && !terminal.has(command.status); n++) {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, 1000);
      signal.addEventListener(
        "abort",
        () => {
          clearTimeout(timer);
          reject(new Error("Request cancelled."));
        },
        { once: true },
      );
    });
    if (signal.aborted) throw new Error("Request cancelled.");
    command = await request<Command>(
      `/api/v1/admin/commands/${encodeURIComponent(command.id)}`,
      { signal },
    );
  }
  return command;
}
function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState(""),
    [error, setError] = useState<string | null>(null),
    [working, setWorking] = useState(false);
  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-icon">
          <Icon name="lock" size={26} />
        </div>
        <span className="eyebrow">PRIVATE OWNER AREA</span>
        <h1>Welcome back.</h1>
        <p>Sign in to monitor the scanner and manage its saved strategy.</p>
        {DEMO && (
          <Notice tone="warning">
            Owner controls are disabled in this synthetic demo. Use the
            connected API to access your bot.
          </Notice>
        )}
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setWorking(true);
            setError(null);
            try {
              await request<Session>("/api/v1/admin/session", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password }),
              });
              setPassword("");
              onSignedIn();
            } catch (err) {
              setError(
                err instanceof Error ? err.message : "Could not sign in.",
              );
            } finally {
              setWorking(false);
            }
          }}
        >
          <label>
            Owner password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              disabled={DEMO}
            />
          </label>
          {error && <Notice tone="error">{error}</Notice>}
          <button
            className="button button-primary"
            disabled={working || DEMO}
            type="submit"
          >
            <Icon name="lock" />
            {working ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <Link className="back-link" href="/">
          ← Return to public radar
        </Link>
      </div>
    </div>
  );
}
function SettingsFields({
  settings,
  onPreview,
  busy,
}: {
  settings: AdminSettings;
  onPreview: (
    action: DesiredAction,
    changes?: JsonRecord,
    preset?: string,
  ) => void;
  busy: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({}),
    [error, setError] = useState<string | null>(null),
    [category, setCategory] = useState("all");
  useEffect(() => {
    setValues(
      Object.fromEntries(
        Object.entries(settings.settings).map(([k, v]) => [
          k,
          typeof v === "object" ? JSON.stringify(v) : String(v),
        ]),
      ),
    );
  }, [settings]);
  const groups: Record<string, string[]> = {
    discovery: [
      "enabled_chains",
      "scan_interval_seconds",
      "request_spacing_seconds",
      "discovery_limit",
      "watchlist_limit",
      "watchlist_hours",
      "discovery_interval",
    ],
    filters: [
      "token_min_age_hours",
      "min_market_cap",
      "max_market_cap",
      "min_liquidity",
      "min_ath_drawdown",
      "max_ath_drawdown",
      "min_holders",
      "min_volume_1h",
    ],
    alerts: [
      "alerts_paused",
      "dry_run",
      "alert_score_threshold",
      "alert_cooldown_hours",
      "realert_score_increase",
      "daily_summary_enabled",
      "daily_summary_hour",
      "report_timezone",
    ],
    exclusions: [
      "exclude_tokenized_stocks",
      "exclude_stablecoins",
      "exclude_wrapped_assets",
    ],
  };
  const entries = Object.entries(settings.settings).filter(
    ([key]) =>
      category === "all" ||
      (groups[category] ?? []).includes(key) ||
      (category === "strategy" && !Object.values(groups).flat().includes(key)),
  );
  const presets = Array.isArray(settings.presets)
    ? settings.presets
    : Object.keys(settings.presets ?? {});
  const preview = () => {
    const changes: JsonRecord = {};
    try {
      for (const [key, original] of Object.entries(settings.settings)) {
        const raw = values[key];
        let parsed: unknown;
        if (typeof original === "boolean") parsed = raw === "true";
        else if (typeof original === "number") {
          parsed = Number(raw);
          if (raw === "" || !Number.isFinite(parsed))
            throw new Error(`${humanKey(key)} requires a number.`);
        } else if (typeof original === "object") parsed = JSON.parse(raw);
        else parsed = raw;
        if (JSON.stringify(parsed) !== JSON.stringify(original))
          changes[key] = parsed;
      }
      if (!Object.keys(changes).length) {
        setError("No settings have changed.");
        return;
      }
      setError(null);
      onPreview("settings", changes);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Enter valid settings before previewing.",
      );
    }
  };
  return (
    <>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Strategy presets</h2>
            <p>Review the bot’s normalized changes before applying</p>
          </div>
        </div>
        <div className="preset-grid">
          {presets.map((p) => (
            <button
              key={p}
              className="preset-card"
              disabled={busy}
              onClick={() => onPreview("settings", undefined, p)}
            >
              <Icon name="radar" />
              <strong>{humanKey(p)}</strong>
              <small>
                {p.toLowerCase() === "strict"
                  ? "Higher evidence requirements"
                  : p.toLowerCase() === "broad"
                    ? "Wider candidate coverage"
                    : "Balanced discovery and evidence"}
              </small>
              <span>
                Preview preset <Icon name="arrow" size={15} />
              </span>
            </button>
          ))}
        </div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Runtime settings</h2>
            <p>
              Saved revision {settings.revision} · Active scan revision{" "}
              {settings.active_revision ?? "unknown"}
            </p>
          </div>
          <button
            className="button button-primary"
            onClick={preview}
            disabled={busy}
          >
            <Icon name="settings" />
            Preview changes
          </button>
        </div>
        <div className="settings-tabs">
          {[
            "all",
            "discovery",
            "filters",
            "strategy",
            "alerts",
            "exclusions",
          ].map((c) => (
            <button
              key={c}
              className={category === c ? "active" : ""}
              onClick={() => setCategory(c)}
            >
              {humanKey(c)}
            </button>
          ))}
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="settings-grid">
          {entries.map(([key, original]) => (
            <label className="setting-field" key={key}>
              <span>{humanKey(key)}</span>
              {typeof original === "boolean" ? (
                <select
                  value={values[key] ?? "false"}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [key]: e.target.value }))
                  }
                >
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              ) : typeof original === "object" ? (
                <textarea
                  rows={3}
                  value={values[key] ?? ""}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [key]: e.target.value }))
                  }
                  spellCheck={false}
                />
              ) : (
                <input
                  type={typeof original === "number" ? "number" : "text"}
                  step="any"
                  value={values[key] ?? ""}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [key]: e.target.value }))
                  }
                />
              )}
              <small>
                {key.includes("ratio") || key.includes("drawdown")
                  ? "Fraction between 0 and 1"
                  : key.endsWith("_hours")
                    ? "Hours"
                    : key.endsWith("_seconds")
                      ? "Seconds"
                      : key === "enabled_chains"
                        ? "JSON array of enabled chains"
                        : key === "weights"
                          ? "JSON object of score weights"
                          : "Validated by the running bot"}
              </small>
            </label>
          ))}
        </div>
      </section>
      <section className="panel reset-panel">
        <div>
          <h2>Restore default strategy</h2>
          <p>Preview resetting runtime overrides to the configured defaults.</p>
        </div>
        <button
          className="button button-danger"
          disabled={busy}
          onClick={() => onPreview("reset")}
        >
          Preview reset
        </button>
      </section>
    </>
  );
}
export function Admin({ section }: { section: string }) {
  const session = useData<Session>("/api/v1/admin/session", false),
    authenticated = session.data?.authenticated;
  const status = useData<AdminStatus>(
    authenticated ? "/api/v1/admin/status" : null,
  );
  const settings = useData<AdminSettings>(
    authenticated ? "/api/v1/admin/settings" : null,
  );
  const commands = useData<{ items: Command[] } | Command[]>(
    authenticated ? "/api/v1/admin/commands" : null,
  );
  const [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [preview, setPreview] = useState<{
      command: Command;
      action: DesiredAction;
    } | null>(null),
    [last, setLast] = useState<Command | null>(null),
    [controller, setController] = useState<AbortController | null>(null);
  useEffect(() => () => controller?.abort(), [controller]);
  const csrf = session.data?.csrf_token;
  const post = async (payload: JsonRecord) => {
    if (!csrf) throw new Error("Session token unavailable. Sign in again.");
    const abort = new AbortController();
    setController(abort);
    const initial = await request<Command>("/api/v1/admin/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ id: crypto.randomUUID(), ...payload }),
      signal: abort.signal,
    });
    return await settleCommand(initial, abort.signal);
  };
  const makePreview = async (
    action: DesiredAction,
    changes?: JsonRecord,
    preset?: string,
  ) => {
    setBusy(true);
    setError(null);
    setPreview(null);
    setLast(null);
    try {
      const command = await post({
        action: "preview",
        desired_action: action,
        expected_revision: settings.data?.revision,
        ...(changes ? { changes } : {}),
        ...(preset ? { preset } : {}),
      });
      if (command.status === "succeeded") {
        setPreview({ command, action });
      } else if (!terminal.has(command.status)) {
        setLast(command);
        setError(
          "The preview is still queued. Its saved result will appear in command history. Create a fresh preview before confirming.",
        );
      } else
        throw new Error(
          String(
            command.result?.message ??
              command.error ??
              "The bot could not preview this action.",
          ),
        );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) session.refresh();
      setError(e instanceof Error ? e.message : "Preview failed.");
    } finally {
      setBusy(false);
      commands.refresh();
    }
  };
  const confirm = async () => {
    if (!preview) return;
    const result = preview.command.result ?? {},
      previewId = result.preview_id;
    if (typeof previewId !== "string") {
      setError(
        "The bot response did not include a valid preview ID. Create a new preview.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const command = await post({
        action: "confirm",
        preview_id: previewId,
        expected_revision: settings.data?.revision,
      });
      setLast(command);
      setPreview(null);
      settings.refresh();
      status.refresh();
      if (command.status === "failed")
        setError(
          String(
            command.result?.message ??
              command.error ??
              "The command failed. Create a new preview to try again.",
          ),
        );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Confirmation failed.");
    } finally {
      setBusy(false);
      commands.refresh();
    }
  };
  if (session.loading && !session.data) return <Loading />;
  if (!authenticated)
    return (
      <>
        <Login onSignedIn={session.refresh} />
        {session.error && !DEMO && (
          <Notice tone="error">{session.error}</Notice>
        )}
      </>
    );
  const commandList = Array.isArray(commands.data)
    ? commands.data
    : (commands.data?.items ?? []);
  const worker = status.data?.worker;
  const workerTime =
    typeof worker?.heartbeat_at === "number"
      ? worker.heartbeat_at
      : typeof worker?.updated_at === "number"
        ? worker.updated_at
        : null;
  const unavailable = worker?.available !== true;
  return (
    <>
      <div className="admin-subnav">
        <div>
          {[
            ["overview", "Overview"],
            ["settings", "Settings"],
            ["actions", "Actions"],
          ].map(([path, label]) => (
            <Link
              key={path}
              href={`/admin${path === "overview" ? "" : `/${path}`}`}
              className={section === path ? "active" : ""}
            >
              {label}
            </Link>
          ))}
        </div>
        <button
          className="text-button"
          onClick={async () => {
            try {
              await request("/api/v1/admin/session", {
                method: "DELETE",
                headers: { "X-CSRF-Token": csrf ?? "" },
              });
              session.refresh();
              setPreview(null);
            } catch (e) {
              setError(e instanceof Error ? e.message : "Logout failed.");
            }
          }}
        >
          <Icon name="logout" size={16} />
          Sign out
        </button>
      </div>
      <PageHeading
        eyebrow="OWNER CONTROL ROOM"
        title={
          section === "settings"
            ? "Strategy settings"
            : section === "actions"
              ? "Scanner controls"
              : "Scanner overview"
        }
        description={
          section === "settings"
            ? "One shared strategy revision for the website and Telegram."
            : section === "actions"
              ? "Preview every action. Confirm with the running bot."
              : "Health, coverage and saved command results for your existing bot."
        }
      />
      {[error, status.error, settings.error, commands.error]
        .filter(Boolean)
        .map((e, i) => (
          <Notice tone="error" key={i}>
            {e}
          </Notice>
        ))}
      {last && (
        <Notice
          tone={
            last.status === "failed"
              ? "error"
              : last.status === "unknown"
                ? "warning"
                : last.status === "succeeded"
                  ? "success"
                  : "info"
          }
        >
          <strong>
            {humanKey(last.action)}: {last.status}
          </strong>
          {last.status === "unknown"
            ? " · Delivery or application outcome is uncertain. Inspect history before issuing another command."
            : !terminal.has(last.status)
              ? " · Waiting for the worker. Follow this command in history."
              : ""}
          {last.error ? ` · ${last.error}` : ""}
        </Notice>
      )}
      {unavailable && (
        <Notice tone="warning">
          The worker heartbeat is unavailable or stale. Controls can remain
          queued until the existing bot is running. An unfinished scan alone
          does not show that the worker is online.
        </Notice>
      )}
      {section === "settings" ? (
        settings.data ? (
          <SettingsFields
            settings={settings.data}
            onPreview={(...args) => void makePreview(...args)}
            busy={busy}
          />
        ) : settings.loading ? (
          <Loading />
        ) : (
          <Empty title="Settings snapshot unavailable">
            Start the upgraded bot to publish its allowlisted configuration.
          </Empty>
        )
      ) : section === "actions" ? (
        <div className="actions-grid">
          {(
            [
              [
                "scan",
                "Scan now",
                "Request a scan through the existing scheduler. Concurrent requests do not create overlapping scanners.",
                "radar",
              ],
              [
                settings.data?.settings.alerts_paused ? "resume" : "pause",
                settings.data?.settings.alerts_paused
                  ? "Resume alerts"
                  : "Pause alerts",
                "Changes signal delivery mute state. Scheduled market report controls remain independent.",
                "pause",
              ],
              [
                "test",
                "Send test alert",
                "Send one clearly labeled synthetic example to the owner’s private Telegram chat. No public signal is created.",
                "alerts",
              ],
            ] as [DesiredAction, string, string, string][]
          ).map(([action, title, text, icon]) => (
            <section className="panel action-card" key={action}>
              <div className="action-icon">
                <Icon name={icon} size={25} />
              </div>
              <h2>{title}</h2>
              <p>{text}</p>
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => void makePreview(action)}
              >
                Preview action <Icon name="arrow" size={16} />
              </button>
            </section>
          ))}
        </div>
      ) : (
        <>
          <div className="stats-grid">
            <Stat
              label="Worker heartbeat"
              value={unavailable ? "Unavailable" : "Online"}
              detail={relative(workerTime)}
              icon="clock"
              tone={unavailable ? "warning-text" : "positive"}
            />
            <Stat
              label="Saved revision"
              value={settings.data?.revision ?? "—"}
              detail={`Active: ${settings.data?.active_revision ?? "unknown"}`}
              icon="settings"
            />
            <Stat
              label="Signal delivery"
              value={
                settings.data
                  ? settings.data.settings.alerts_paused
                    ? "Paused"
                    : "Enabled"
                  : "—"
              }
              detail={
                settings.data?.settings.dry_run
                  ? "Dry run is enabled"
                  : "Bot-published runtime state"
              }
              icon="alerts"
            />
            <Stat
              label="Command history"
              value={commandList.length}
              detail={`${commandList.filter((c) => ["queued", "running"].includes(c.status)).length} awaiting completion`}
              icon="reports"
            />
          </div>
          <div className="detail-grid">
            <section className="panel">
              <h2>Worker state</h2>
              <JsonGrid value={worker} />
            </section>
            <section className="panel">
              <h2>Scanner health</h2>
              <JsonGrid value={status.data?.health} />
            </section>
          </div>
        </>
      )}
      <section className="panel command-history">
        <div className="panel-header">
          <div>
            <h2>Command history</h2>
            <p>Persisted requests and results from the shared controller</p>
          </div>
          <button className="text-button" onClick={commands.refresh}>
            <Icon name="refresh" size={15} />
            Refresh
          </button>
        </div>
        {commandList.length ? (
          <div className="command-list">
            {commandList.map((c) => (
              <details key={c.id}>
                <summary>
                  <div>
                    <strong>{humanKey(c.action)}</strong>
                    <small>{dateTime(c.created_at)}</small>
                  </div>
                  <span className={`command-status command-${c.status}`}>
                    {c.status}
                  </span>
                  <Icon name="chevron" size={15} />
                </summary>
                <div className="command-details">
                  <span className="small muted">Command {c.id}</span>
                  {c.error && <Notice tone="error">{c.error}</Notice>}
                  <JsonGrid value={c.result} />
                </div>
              </details>
            ))}
          </div>
        ) : (
          <Empty title="No saved commands">
            Website commands will appear here with their recorded outcomes.
          </Empty>
        )}
      </section>
      {preview && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget && !busy) setPreview(null);
          }}
        >
          <section
            className="confirmation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirmation-title"
          >
            <div className="modal-header">
              <span className="eyebrow">BOT-VALIDATED PREVIEW</span>
              <button
                className="icon-button"
                aria-label="Close preview"
                disabled={busy}
                onClick={() => setPreview(null)}
              >
                <Icon name="close" />
              </button>
            </div>
            <h2 id="confirmation-title">
              Confirm{" "}
              {preview.action === "test"
                ? "test alert"
                : humanKey(preview.action).toLowerCase()}
              ?
            </h2>
            <p>
              {preview.action === "test"
                ? "This sends one synthetic test message to your private Telegram chat."
                : preview.action === "reset"
                  ? "The saved runtime overrides will be reset after this confirmation."
                  : preview.action === "settings"
                    ? "Ordinary strategy changes take effect on the next scan. Historical signals keep their original evidence."
                    : "The running bot will execute this saved preview."}
            </p>
            <JsonGrid value={preview.command.result} />
            {error && <Notice tone="error">{error}</Notice>}
            <div className="modal-actions">
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => setPreview(null)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                disabled={busy}
                onClick={() => void confirm()}
              >
                {busy ? "Waiting for bot…" : "Confirm action"}
                <Icon name="check" size={16} />
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
