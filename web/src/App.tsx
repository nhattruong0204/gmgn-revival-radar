import { useState } from "react";
import { DEMO, navigate, useData, useLocation } from "./api";
import { Icon, Link, Notice } from "./components";
import { Radar } from "./Radar";
import { TokenDetail } from "./TokenDetail";
import { Alerts, Methodology, OutcomePage, Reports } from "./Publications";
import { Admin } from "./Admin";
import type { Meta } from "./types";
import { relative } from "./format";
import { previewState, previewStates } from "./demo";
const navigation = [
  ["/", "Radar", "radar"],
  ["/alerts", "Alerts", "alerts"],
  ["/reports", "Market reports", "reports"],
  ["/outcomes", "Signal outcomes", "outcomes"],
  ["/how-it-works", "How it works", "info"],
];
export function App() {
  const location = useLocation(),
    path = location.split("?")[0],
    search = location.includes("?")
      ? location.slice(location.indexOf("?"))
      : "";
  const [open, setOpen] = useState(false);
  const state = previewState(new URLSearchParams(search).get("preview_state"));
  const meta = useData<Meta>(
    DEMO ? `/api/v1/meta?preview_state=${state}` : "/api/v1/meta",
  );
  const parts = path.split("/");
  let page;
  if (path === "/") page = <Radar search={search} meta={meta.data} />;
  else if (path.startsWith("/tokens/") && parts.length === 4) {
    try {
      page = (
        <TokenDetail
          chain={decodeURIComponent(parts[2])}
          address={decodeURIComponent(parts[3])}
        />
      );
    } catch {
      page = (
        <Notice tone="error">The token address in this URL is invalid.</Notice>
      );
    }
  } else if (path === "/alerts" || path.startsWith("/alerts/"))
    page = <Alerts id={parts[2]} />;
  else if (path === "/reports") page = <Reports />;
  else if (path === "/outcomes") page = <OutcomePage />;
  else if (path === "/how-it-works") page = <Methodology />;
  else if (path === "/admin" || path.startsWith("/admin/"))
    page = <Admin section={parts[2] ?? "overview"} />;
  else
    page = (
      <div className="not-found">
        <span className="eyebrow">404 / OFF THE RADAR</span>
        <h1>Nothing tracked here.</h1>
        <p>This page could not be found.</p>
        <Link className="button button-primary" href="/">
          Back to radar <Icon name="arrow" />
        </Link>
      </div>
    );
  const title =
    navigation.find(([href]) => path === href)?.[1] ??
    (path.startsWith("/admin")
      ? "Owner area"
      : path.startsWith("/tokens")
        ? "Token detail"
        : "Alert detail");
  return (
    <div className="app-shell">
      <div
        className={`sidebar-scrim ${open ? "visible" : ""}`}
        onClick={() => setOpen(false)}
      />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <Link className="brand" href="/" onClick={() => setOpen(false)}>
          <img src="/revival-radar.png" alt="" />
          <span>
            revival
            <span>
              radar<span className="brand-dot">.</span>
            </span>
          </span>
        </Link>
        <div className="sidebar-section-label">INTELLIGENCE</div>
        <nav aria-label="Main navigation">
          {navigation.map(([href, label, icon]) => (
            <Link
              key={href}
              href={href}
              onClick={() => setOpen(false)}
              className={`nav-link ${path === href || (href === "/alerts" && path.startsWith("/alerts/")) ? "active" : ""}`}
            >
              <Icon name={icon} />
              <span>{label}</span>
              {href === "/" && <span className="nav-live-dot" />}
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="scanner-card">
            <div className="scanner-card-top">
              <span className={`status-dot ${meta.error ? "offline" : ""}`} />
              <strong>
                {DEMO
                  ? "Synthetic preview"
                  : meta.error
                    ? "Service unavailable"
                    : "Saved observations"}
              </strong>
              <Icon name="radar" size={16} />
            </div>
            <p>
              {meta.data?.last_scan_at
                ? `Completed scan ${relative(meta.data.last_scan_at)}`
                : "Awaiting a completed scan"}
            </p>
            <div className="scanner-card-decoration">
              <i />
              <i />
              <i />
            </div>
          </div>
          {meta.data?.telegram_url?.startsWith("https://") && (
            <a
              className="nav-link telegram-link"
              href={meta.data.telegram_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Icon name="alerts" />
              <span>Open Telegram</span>
              <Icon name="external" size={14} />
            </a>
          )}
          <Link
            className={`nav-link owner-link ${path.startsWith("/admin") ? "active" : ""}`}
            href="/admin"
            onClick={() => setOpen(false)}
          >
            <Icon name="lock" />
            <span>Owner area</span>
            <small>PRIVATE</small>
          </Link>
          <div className="sidebar-footer">
            SEE THE SIGNAL. FOLLOW THE EVIDENCE.
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="menu-button"
              aria-label="Open navigation"
              onClick={() => setOpen((v) => !v)}
            >
              <Icon name="menu" />
            </button>
            <span>Workspace</span>
            <Icon name="chevron" size={12} />
            <strong>{title}</strong>
          </div>
          <div className="topbar-right">
            <span className="network-indicator">
              <span className="status-dot" />
              {DEMO ? "DEMO DATA" : "SCANNER OBSERVATIONS"}
            </span>
            <span className="version-badge">V1.0</span>
          </div>
        </header>
        <main key={DEMO ? `${path}:${state}` : path}>
          <div className="content-container">
            {DEMO && (
              <Notice tone="warning">
                <strong>Synthetic demo.</strong> Every token, score and return
                shown here is sample data. Owner controls are disabled.
                <label className="preview-selector">
                  Synthetic preview state
                  <select
                    value={state}
                    onChange={(event) => {
                      const params = new URLSearchParams(search);
                      params.set("preview_state", event.target.value);
                      navigate(`${path}?${params}`);
                    }}
                  >
                    {previewStates.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              </Notice>
            )}
            {page}
          </div>
          <footer className="page-footer">
            <span>© {new Date().getFullYear()} Revival Radar</span>
            <span>
              Data from saved GMGN observations{" "}
              <span className="footer-dot">·</span>
              <Link href="/how-it-works">Methodology &amp; coverage</Link>
            </span>
            <span>Built for the comeback.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
