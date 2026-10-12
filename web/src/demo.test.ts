import { describe, expect, it } from "vitest";
import { demoObservations, demoRequest, previewState } from "./demo";
import type { ListResponse, Observation, TokenResponse } from "./types";

describe("foundation preview states", () => {
  it("returns chain metadata and empty Radar data without changing fixtures", () => {
    expect(demoRequest("/api/v1/meta")).toMatchObject({
      chains: [{ id: "sol", name: "Solana" }],
    });
    expect(demoRequest("/api/v1/radar", "empty")).toMatchObject({
      items: [],
      total: 0,
    });
    expect(
      (demoRequest("/api/v1/radar") as ListResponse<Observation>).total,
    ).toBe(12);
  });
  it("keeps known risk and unknown values visible in token evidence", () => {
    const path = `/api/v1/tokens/sol/${demoObservations[0].contract_address}`;
    const detail = demoRequest(path, "partial") as TokenResponse;
    expect(detail.observation).toMatchObject({
      eligible: false,
      liquidity: null,
      confirmation_score: null,
    });
    expect(detail.observation?.token?.security).toMatchObject({
      dangerous: true,
      mint_renounced: false,
      freeze_renounced: null,
    });
    expect(detail.observation?.blockers.length).toBeGreaterThan(0);
    expect(demoObservations[0].eligible).toBe(true);
    expect(demoObservations[0].liquidity).not.toBeNull();
  });
  it("separates stale observation timestamps from the current response cutoff", () => {
    const current = demoRequest("/api/v1/radar") as ListResponse<Observation>;
    const stale = demoRequest(
      "/api/v1/radar",
      "stale",
    ) as ListResponse<Observation>;
    expect(stale.as_of).toBe(current.as_of);
    expect(stale.last_scan_at).toBe(current.last_scan_at! - 7200);
    expect(stale.items[0].timestamp).toBe(current.items[0].timestamp - 7200);
  });
  it("provides explicit error and loading scenarios with a safe default", () => {
    expect(() => demoRequest("/api/v1/radar", "error")).toThrow(
      "Synthetic preview",
    );
    expect(demoRequest("/api/v1/radar", "loading")).toMatchObject({
      total: 12,
    });
    expect(previewState("unrecognized")).toBe("ready");
  });
});
