// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useData } from "./api";

function View() {
  const state = useData("/api/v1/radar");
  return <div>{state.error ?? "ready"}</div>;
}
beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function tick(milliseconds: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}
it("polls at thirty seconds and suspends requests in a hidden tab", async () => {
  const fetcher = vi.fn(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetcher);
  render(<View />);
  await tick(0);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await tick(30000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: true,
  });
  await tick(90000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await tick(0);
  expect(fetcher).toHaveBeenCalledTimes(3);
});
it("backs off failed requests instead of repeating at the normal interval", async () => {
  const fetcher = vi.fn(async () => new Response("{}", { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  render(<View />);
  await tick(0);
  await tick(30000);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await tick(30000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await tick(119999);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await tick(1);
  expect(fetcher).toHaveBeenCalledTimes(3);
});
