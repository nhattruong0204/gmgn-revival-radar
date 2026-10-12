// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Admin } from "./Admin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("returns to sign-in when protected reads discover an expired session", async () => {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  let sessions = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.endsWith("/session"))
        return new Response(
          JSON.stringify({
            authenticated: ++sessions === 1,
            csrf_token: "synthetic-csrf",
          }),
        );
      return new Response(
        JSON.stringify({ detail: "Owner session expired. Sign in again." }),
        { status: 401 },
      );
    }),
  );
  render(<Admin section="overview" />);
  await screen.findByRole("heading", { name: "Welcome back." });
  expect(sessions).toBeGreaterThan(1);
  expect(
    screen.queryByRole("heading", { name: "Scanner overview" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
});
