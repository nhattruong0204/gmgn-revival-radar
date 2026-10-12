import type { AnchorHTMLAttributes, ReactNode } from "react";
import { navigate } from "./api";
import { dateTime, humanKey, relative, stageName } from "./format";
import type { Observation } from "./types";
export function Link({
  href = "",
  onClick,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      {...props}
      href={href}
      onClick={(e) => {
        onClick?.(e);
        if (
          !e.defaultPrevented &&
          href.startsWith("/") &&
          !e.metaKey &&
          !e.ctrlKey &&
          !e.shiftKey &&
          !e.altKey &&
          e.button === 0
        ) {
          e.preventDefault();
          navigate(href);
        }
      }}
    />
  );
}
const paths: Record<string, ReactNode> = {
  radar: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 4v8l6 4" />
      <path d="M3 12h2m14 0h2M12 19v2" />
    </>
  ),
  alerts: (
    <>
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M10 21h4" />
    </>
  ),
  reports: (
    <>
      <path d="M5 3h10l4 4v14H5z" />
      <path d="M15 3v5h4M8 12h8m-8 4h8" />
    </>
  ),
  outcomes: (
    <>
      <path d="M4 4v16h16M7 15l4-5 4 2 5-7" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6m0-10v1" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" />
    </>
  ),
  search: (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 5 5" />
    </>
  ),
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  external: (
    <>
      <path d="M14 3h7v7m0-7-10 10" />
      <path d="M10 4H4v16h16v-6" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5m-4 6a8 8 0 0 0 14 5l3-3m0 5v-5h-5" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V4H4v12h4" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  warning: (
    <>
      <path d="m12 3 10 18H2z" />
      <path d="M12 9v5m0 3v1" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="8" cy="7" r="2" />
      <circle cx="16" cy="17" r="2" />
    </>
  ),
  play: <path d="m8 4 12 8-12 8z" />,
  pause: (
    <>
      <path d="M8 4v16m8-16v16" />
    </>
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  logout: (
    <>
      <path d="M10 4H4v16h6m4-14 6 6-6 6m-6-6h12" />
    </>
  ),
  bolt: <path d="m13 2-9 12h7l-1 8 10-12h-7z" />,
};
export function Icon({
  name,
  size = 18,
  ...props
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name] ?? paths.radar}
    </svg>
  );
}
export function Stage({ stage }: { stage: string }) {
  return (
    <span className={`stage stage-${stage.toLowerCase()}`}>
      <span />
      {stageName(stage)}
    </span>
  );
}
export function Score({
  value,
  large = false,
}: {
  value: number | null | undefined;
  large?: boolean;
}) {
  return (
    <div className={`score ${large ? "score-large" : ""}`}>
      <span className={value != null && value >= 75 ? "positive" : ""}>
        {value ?? "—"}
      </span>
      <small>/100</small>
      <div className="score-track">
        <i style={{ width: `${value ?? 0}%` }} />
      </div>
    </div>
  );
}
export function Avatar({ observation }: { observation: Observation }) {
  return (
    <div
      className="token-avatar"
      style={
        {
          "--avatar-hue": String((observation.symbol.charCodeAt(0) * 19) % 360),
        } as React.CSSProperties
      }
    >
      {observation.symbol.slice(0, 1)}
    </div>
  );
}
export function Freshness({
  observed,
  refreshed,
}: {
  observed: number | null | undefined;
  refreshed?: number | null;
}) {
  const stale = observed == null || Date.now() / 1000 - observed > 1800;
  return (
    <div className={`freshness ${stale ? "stale" : ""}`}>
      <span className="status-dot" />
      <span title={dateTime(observed)}>
        Last completed scan{" "}
        {observed == null ? "unavailable" : relative(observed)}
      </span>
      {refreshed && (
        <small title={dateTime(refreshed)}>
          Page refreshed {relative(refreshed)}
        </small>
      )}
    </div>
  );
}
export function Notice({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "warning" | "error" | "success";
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : undefined}
    >
      <Icon
        name={
          tone === "success" ? "check" : tone === "info" ? "info" : "warning"
        }
      />
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon name="radar" size={32} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <div className="loading-line" />
      <div className="loading-line" />
      <div className="loading-line" />
      <span>Loading saved observations…</span>
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function Stat({
  label,
  value,
  detail,
  icon = "radar",
  tone,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon?: string;
  tone?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-label">
        {label}
        <Icon name={icon} />
      </div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  );
}
export function EvidenceList({
  items,
  empty = "No findings recorded.",
  tone = "neutral",
}: {
  items: string[] | undefined;
  empty?: string;
  tone?: string;
}) {
  return items?.length ? (
    <ul className={`evidence-list ${tone}`}>
      {items.map((v, i) => (
        <li key={i}>
          <Icon name={tone === "danger" ? "warning" : "check"} size={16} />
          {v}
        </li>
      ))}
    </ul>
  ) : (
    <p className="muted">{empty}</p>
  );
}
export function JsonGrid({
  value,
}: {
  value: Record<string, unknown> | null | undefined;
}) {
  return value && Object.keys(value).length ? (
    <dl className="json-grid">
      {Object.entries(value).map(([key, v]) => (
        <div key={key}>
          <dt>{humanKey(key)}</dt>
          <dd>
            {typeof v === "object" && v !== null
              ? JSON.stringify(v)
              : typeof v === "boolean"
                ? v
                  ? "Yes"
                  : "No"
                : String(v ?? "Unknown")}
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <p className="muted">No saved worker data available.</p>
  );
}
