import { compact, number, percent } from "./format";
import type { Observation, Token } from "./types";

/**
 * Single dictionary that turns scorer, blocker, warning and missing-field keys
 * into plain sentences. Values come only from saved evidence; an absent value
 * is never rendered as a measurement and never becomes a positive statement.
 * Wording for blockers and missing fields mirrors the Telegram labels in
 * src/revival_radar/diagnostics.py and telegram_presentation.py.
 */
export interface Reason {
  key: string;
  text: string;
  points: number;
}
export interface Risk {
  key: string;
  text: string;
  tone: "danger" | "warning";
}

type Accel = Record<string, number | boolean | null> | undefined;
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const times = (v: unknown) => {
  const n = num(v);
  return n == null ? null : `${number(n, 1)}×`;
};
const sentence = (s: string) => s.replace(/^./, (c) => c.toUpperCase());

/** Humanised fallback that never shows snake_case or dotted identifiers. */
export function humanise(key: string): string {
  const words = key
    .replace(/[._]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return words ? sentence(words) : "Unnamed finding";
}

/** Labels for token fields, shared by blockers and missing evidence. */
export const fieldLabels: Record<string, string> = {
  market_cap: "Market cap",
  liquidity: "Liquidity",
  drawdown_from_ath: "ATH drawdown",
  holders: "Holders",
  volume_1h: "Hourly volume",
  volume_5m: "5m volume",
  price: "Price",
  price_change_5m: "5m price increase",
  price_change_1h: "Hourly price increase",
  token_age_seconds: "Token age",
  baseline_history: "Not enough recent scan history",
  ath_market_cap: "ATH market cap",
  tx_5m: "5m transactions",
  tx_1h: "Hourly transactions",
  candles: "Price candles",
  asset_type: "Asset type",
  "security.dev_ratio": "Developer holdings",
  "security.sniper_ratio": "Sniper holdings",
  "security.bundler_ratio": "Bundler aggregate",
  "security.top10_ratio": "Top 10 holder concentration",
  "security.insider_ratio": "Insider holdings",
  "security.dangerous": "Security assessment",
  volume_5m_baseline_unavailable_or_zero: "5m volume baseline missing or zero",
  returning_activity: "Returning activity",
  strategy_revision: "Strategy revision",
};
const fieldLabel = (key: string) => fieldLabels[key] ?? humanise(key);

/** Blocker keys (stored evaluation rejection reasons). */
export const blockerSentences: Record<string, string> = {
  no_base: "No price base detected",
  base_too_short: "Base too short",
  enrichment_deferred: "Enrichment deferred by budget",
  no_returning_activity: "Activity has not returned",
  score_below_threshold: "Score below alert threshold",
  security_dangerous: "Known security risk",
  dangerous: "Known security risk",
};

/** Risk sentences for scorer penalty components. */
const penaltySentence = (
  key: string,
  o: Observation,
  t: Token,
): string | null => {
  const sec = t.security ?? o.security;
  switch (key) {
    case "concentration_penalty":
      return num(sec?.top10_ratio) == null
        ? "Holder concentration was above the configured limit"
        : `The top 10 holders own ${percent(sec?.top10_ratio, true)} of supply`;
    case "danger_penalty":
      return "A dangerous security flag was reported";
    case "sniper_penalty":
      return num(sec?.sniper_ratio) == null
        ? "Sniper holdings were above the configured limit"
        : `Snipers hold ${percent(sec?.sniper_ratio, true)} of supply`;
    case "bundler_penalty":
      return num(sec?.bundler_ratio) == null
        ? "Bundler holdings were above the configured limit"
        : `Bundled wallets hold ${percent(sec?.bundler_ratio, true)} of supply`;
    case "liquidity_penalty":
      return "Liquidity fell sharply since the previous scan";
    case "dev_penalty":
      return "Developer holdings fell since the previous scan (a proxy, not proof of selling)";
    case "insider_penalty":
      return "Insider holdings fell since the previous scan (a proxy, not proof of selling)";
    default:
      return null;
  }
};

/** Positive component sentences, with the measured value where saved. */
const positiveSentence = (
  key: string,
  o: Observation,
  t: Token,
): string | null => {
  const a: Accel = o.signal?.acceleration;
  const s = o.signal?.structure;
  const usd = (v: unknown) => (num(v) == null ? "" : ` (${compact(v, true)})`);
  switch (key) {
    case "drawdown":
      return num(t.drawdown_from_ath) == null
        ? "Price sits inside the configured range below its all-time high"
        : `Price is ${percent(t.drawdown_from_ath, true)} below its all-time high, inside the configured recovery range`;
    case "liquidity":
      return num(t.liquidity) == null
        ? "Liquidity met the configured minimum"
        : `Liquidity of ${compact(t.liquidity, true)} met the configured minimum`;
    case "holders":
      return num(t.holders) == null
        ? "Holder count held up against the previous scan"
        : `${number(t.holders)} holders, and the count held up against the previous scan`;
    case "base":
      return num(s?.base_duration_hours) == null
        ? "A stable price base was detected"
        : `A stable price base has held for ${number(s?.base_duration_hours, 1)} hours`;
    case "base_duration":
      return num(s?.base_duration_hours) == null
        ? "The price base is long enough to earn the maturity bonus"
        : `The price base has lasted ${number(s?.base_duration_hours, 1)} hours, long enough for the maturity bonus`;
    case "compression":
      return "Volatility tightened inside the price base";
    case "volume_5m": {
      const ratio = times(a?.volume_ratio_5m ?? a?.volume_acceleration_5m);
      return ratio == null
        ? `5-minute volume${usd(t.volume_5m)} cleared the acceleration threshold`
        : `5-minute volume was ${ratio} its recent baseline${usd(t.volume_5m)}`;
    }
    case "volume_1h": {
      const ratio = times(a?.volume_acceleration_1h);
      return ratio == null
        ? `Hourly volume${usd(t.volume_1h)} cleared the acceleration threshold`
        : `Hourly volume was ${ratio} the previous scan${usd(t.volume_1h)}`;
    }
    case "transactions": {
      const five = times(a?.tx_acceleration_5m),
        hour = times(a?.tx_acceleration_1h);
      return five != null
        ? `5-minute transactions were ${five} their baseline`
        : hour != null
          ? `Hourly transactions were ${hour} the previous scan`
          : "Transaction count cleared the acceleration threshold";
    }
    case "hot_rank": {
      const gain = num(a?.hot_rank_improvement);
      return gain == null
        ? "Hot Search rank improved"
        : `Hot Search rank improved by ${number(gain)} places`;
    }
    case "trending":
      return "Currently on the Trending list";
    case "both_sources":
      return "Listed on both Hot Search and Trending";
    case "higher_low":
      return "Closed candles made a higher low inside the base";
    case "higher_high":
      return "Closed candles made a higher high";
    case "breakout":
      return "A closed candle broke above the base range";
    case "retest":
      return "Price retested the base range and held";
    default:
      return null;
  }
};

const componentLine = /^[a-z0-9_]+:\s*[+-]?\d+$/;

/** Top positive contributions, ranked by saved points (default 3). */
export function explainReasons(o: Observation, limit = 3): Reason[] {
  const t: Token = o.token ?? o;
  const ranked = Object.entries(o.signal?.components ?? {})
    .filter(([, points]) => typeof points === "number" && points > 0)
    .map(([key, points], order) => ({ key, points, order }))
    .sort((a, b) => b.points - a.points || a.order - b.order)
    .map(({ key, points }) => ({
      key,
      points,
      text:
        positiveSentence(key, o, t) ??
        `${humanise(key)} added ${points} points`,
    }));
  // Narrative reasons saved by other writers; raw "name: +N" lines are not narrative.
  const narrative = [...new Set(o.signal?.reasons ?? [])]
    .filter((r) => !componentLine.test(r.trim()))
    .map((text) => ({ key: text, points: 0, text: explainText(text) }));
  return [...ranked, ...narrative].slice(0, limit);
}

/** Risks: blockers first, then scorer penalties by size, then warnings. */
export function explainRisks(o: Observation): Risk[] {
  const t: Token = o.token ?? o;
  const components = o.signal?.components ?? {};
  const out: Risk[] = [];
  const seen = new Set<string>();
  const add = (key: string, text: string, tone: Risk["tone"]) => {
    const id = text.trim().toLowerCase();
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({ key, text, tone });
  };
  for (const b of o.blockers) add(b, explainText(b), "danger");
  Object.entries(components)
    .filter(([, points]) => typeof points === "number" && points < 0)
    .sort((a, b) => a[1] - b[1])
    .forEach(([key]) =>
      add(
        key,
        penaltySentence(key, o, t) ?? `${humanise(key)} reduced the score`,
        "warning",
      ),
    );
  for (const w of o.warnings) {
    // The scorer mirrors each penalty into warnings as "bundler penalty".
    if (/^[a-z]+ penalty$/.test(w) && w.replace(" ", "_") in components)
      continue;
    add(w, explainText(w), "warning");
  }
  return out;
}

/** Plain label for one missing-evidence key. */
export function explainMissing(key: string): string {
  return fieldLabel(key);
}

const gateReason =
  /^([a-z0-9_.]+):\s*(unavailable|below minimum|above maximum|excluded\s+.+)$/;
const embedded = /\b[a-z][a-z0-9]*(?:[._][a-z0-9]+)+\b/g;
const internalSource = /^(blockers|missing_fields|warnings)\.\d+$/;

/**
 * Plain text for any saved sentence or key. Known keys use the dictionary;
 * snake_case or dotted identifiers inside longer text are humanised in place.
 */
export function explainText(text: string): string {
  const trimmed = text.trim();
  if (trimmed in blockerSentences) return blockerSentences[trimmed];
  if (trimmed in fieldLabels) return fieldLabels[trimmed];
  const gate = gateReason.exec(trimmed);
  if (gate) {
    const [, field, rule] = gate;
    if (field === "asset_type")
      return sentence(rule.replace(/^excluded/, "excluded asset type"));
    const label = fieldLabel(field);
    return rule === "unavailable"
      ? `${label} was not saved`
      : `${label} is ${rule}`;
  }
  if (/^[a-z0-9]+(?:[._][a-z0-9]+)+$/.test(trimmed)) return humanise(trimmed);
  return text.replace(embedded, (m) =>
    (fieldLabels[m] ?? humanise(m)).toLowerCase(),
  );
}

/** True for stored claim-field identifiers such as "missing_fields.0". */
export const isInternalSource = (field: string) => internalSource.test(field);

/** Display label for a brief-comparison key such as "unresolved: volume". */
export function explainDiffKey(key: string): string {
  const groups: Record<string, string> = {
    support: "Supporting fact",
    counter_evidence: "Counter-evidence",
    unresolved: "Unresolved evidence",
  };
  const sep = key.indexOf(": ");
  if (sep < 0) return key;
  const head = key.slice(0, sep),
    rest = key.slice(sep + 2);
  if (head in groups)
    return `${groups[head]}: ${internalSource.test(rest) ? "saved finding" : explainText(rest)}`;
  return `${head}: ${internalSource.test(rest) ? "saved finding" : explainText(rest)}`;
}
