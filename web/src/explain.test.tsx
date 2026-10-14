// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { SignalEvidence } from "./TokenDetail";
import {
  explainDiffKey,
  explainMissing,
  explainReasons,
  explainRisks,
  explainText,
  humanise,
} from "./explain";
import type { Observation } from "./types";

afterEach(cleanup);

const base: Observation = {
  id: 1,
  timestamp: 1800000000,
  chain: "sol",
  contract_address: "A".repeat(32),
  symbol: "NEW",
  score: 61,
  status: "REVIVING",
  eligible: true,
  blockers: [],
  missing_fields: [],
  warnings: [],
  token: {
    timestamp: 1800000000,
    chain: "sol",
    contract_address: "A".repeat(32),
    symbol: "NEW",
    liquidity: 52000,
    holders: 1200,
    drawdown_from_ath: 0.82,
    volume_5m: 33000,
    volume_1h: 90000,
    security: { top10_ratio: 0.31, bundler_ratio: 0.12 },
  },
  signal: {
    // Exactly what the current scorer saves: component strings only.
    reasons: [
      "volume_5m: +15",
      "drawdown: +10",
      "base: +8",
      "bundler_penalty: -5",
    ],
    warnings: ["bundler penalty"],
    components: {
      drawdown: 10,
      volume_5m: 15,
      base: 8,
      transactions: 10,
      bundler_penalty: -5,
    },
    acceleration: {
      volume_ratio_5m: 4.13,
      tx_acceleration_5m: 2,
      hot_rank_improvement: null,
    },
    structure: {
      available: true,
      base_detected: true,
      base_duration_hours: 14,
    },
  },
};

const componentKeys = [
  "drawdown",
  "liquidity",
  "holders",
  "base",
  "base_duration",
  "compression",
  "volume_5m",
  "volume_1h",
  "transactions",
  "hot_rank",
  "trending",
  "both_sources",
  "higher_low",
  "higher_high",
  "breakout",
  "retest",
];
const penaltyKeys = [
  "concentration_penalty",
  "danger_penalty",
  "sniper_penalty",
  "bundler_penalty",
  "liquidity_penalty",
  "dev_penalty",
  "insider_penalty",
];

it("explains every scorer component key as a plain sentence", () => {
  for (const key of componentKeys) {
    const [reason] = explainReasons(
      { ...base, signal: { components: { [key]: 5 } } },
      1,
    );
    expect(reason.text, key).not.toMatch(/[a-z]+_[a-z0-9]+/);
    expect(reason.text.length).toBeGreaterThan(10);
  }
  for (const key of penaltyKeys) {
    const [risk] = explainRisks({
      ...base,
      signal: { components: { [key]: -5 } },
    });
    expect(risk.text, key).not.toMatch(/[a-z]+_[a-z0-9]+/);
    expect(risk.tone).toBe("warning");
  }
});

it("includes the measured value from saved evidence", () => {
  const reasons = explainReasons(base);
  expect(reasons[0].text).toBe(
    "5-minute volume was 4.1× its recent baseline ($33K)",
  );
  expect(reasons.map((r) => r.key)).toEqual([
    "volume_5m",
    "drawdown",
    "transactions",
  ]);
  expect(explainRisks(base)[0].text).toBe("Bundled wallets hold 12% of supply");
});

it("words drawdown as market cap and uses only the baseline ratio for 5m volume", () => {
  expect(
    explainReasons({ ...base, signal: { components: { drawdown: 10 } } })[0]
      .text,
  ).toBe(
    "Market cap is 82% below its all-time-high market cap, inside the configured recovery range",
  );
  const noValue = explainReasons({
    ...base,
    token: null,
    signal: { components: { drawdown: 10 } },
  });
  expect(noValue[0].text).toBe(
    "Market cap sits inside the configured range below its all-time high",
  );
  const [volume] = explainReasons({
    ...base,
    signal: {
      components: { volume_5m: 15 },
      acceleration: { volume_acceleration_5m: 9, volume_ratio_5m: null },
    },
  });
  expect(volume.text).toBe(
    "5-minute volume ($33K) cleared the acceleration threshold",
  );
  expect(volume.text).not.toContain("9");
});

it("ranks by points and shows at most three reasons", () => {
  const reasons = explainReasons(base);
  expect(reasons).toHaveLength(3);
  expect(reasons.map((r) => r.points)).toEqual([15, 10, 10]);
  expect(explainReasons(base, 10).map((r) => r.key)).toContain("base");
});

it("never turns a missing measurement into a number or a positive claim", () => {
  const [reason] = explainReasons({
    ...base,
    token: null,
    signal: { components: { volume_5m: 15 } },
  });
  expect(reason.text).toBe(
    "5-minute volume cleared the acceleration threshold",
  );
  expect(reason.text).not.toMatch(/\$|—|×/);
  // No saved points means no reason at all, and zero components are not reasons.
  expect(
    explainReasons({ ...base, signal: { components: { drawdown: 0 } } }),
  ).toEqual([]);
  expect(explainReasons({ ...base, signal: null })).toEqual([]);
});

it("falls back to a humanised key for unknown keys", () => {
  expect(humanise("weird_new_rule")).toBe("Weird new rule");
  expect(
    explainReasons({
      ...base,
      signal: { components: { weird_new_rule: 7 } },
    })[0].text,
  ).toBe("Weird new rule added 7 points");
  expect(explainText("security.mystery_ratio")).toBe("Security mystery ratio");
  expect(explainText("blockers.0")).toBe("Blockers 0");
  expect(explainMissing("brand_new_field")).toBe("Brand new field");
  expect(explainDiffKey("unresolved: missing_fields.2")).toBe(
    "Unresolved evidence: saved finding",
  );
});

it("maps blockers, gate reasons and missing fields to plain language", () => {
  expect(explainText("no_base")).toBe("No price base detected");
  expect(explainText("no_returning_activity")).toBe(
    "Activity has not returned",
  );
  expect(explainText("score_below_threshold")).toBe(
    "Score below alert threshold",
  );
  expect(explainText("volume_1h: below minimum")).toBe(
    "Hourly volume is below minimum",
  );
  expect(explainText("liquidity: unavailable")).toBe("Liquidity was not saved");
  expect(explainText("volume_5m is unavailable")).toBe(
    "5m volume is unavailable",
  );
  expect(explainMissing("security.dev_ratio")).toBe("Developer holdings");
});

it("renders a current-scorer payload with reasons and no raw keys outside the disclosure", () => {
  const { container } = render(
    <SignalEvidence
      observation={{
        ...base,
        blockers: ["no_base", "no_returning_activity", "score_below_threshold"],
        missing_fields: ["security.dev_ratio", "baseline_history"],
        warnings: ["bundler penalty", "liquidity: below minimum"],
      }}
    />,
  );
  const panels = container.querySelectorAll("section.panel");
  const visible = [...panels].map((p) => p.textContent).join(" ");
  expect(visible).toContain("5-minute volume was 4.1× its recent baseline");
  expect(
    container.querySelectorAll(".evidence-list li").length,
  ).toBeGreaterThan(3);
  expect(visible).toContain("No price base detected");
  expect(visible).not.toMatch(/[a-z]+_[a-z0-9_]+/);
  expect(visible).not.toMatch(/missing_fields\.\d/);
  expect(visible).not.toContain("No narrative reasons");
  const disclosure = container.querySelector("details.score-disclosure");
  expect(disclosure?.textContent).toContain("no_base");
  expect(disclosure?.textContent).toContain("volume_5m");
});

it("caps visible risks at three and keeps the rest in a disclosure", () => {
  const { container } = render(
    <SignalEvidence
      observation={{
        ...base,
        blockers: ["no_base", "no_returning_activity", "score_below_threshold"],
        warnings: ["Sparse candles"],
      }}
    />,
  );
  const more = container.querySelector(".panel details.research-disclosure");
  expect(more?.textContent).toContain("Sparse candles");
  expect(more?.textContent).toContain("more risk finding");
});
