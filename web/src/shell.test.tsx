// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { Modal } from "./Modal";
import { Radar } from "./Radar";
import { Reports } from "./Publications";
import { demoRequest } from "./demo";
import { navigate } from "./api";
import { researchReturn } from "./format";
import { useState } from "react";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal("requestAnimationFrame", (fn: () => void) => {
    fn();
    return 1;
  });
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify(
            path.includes("/admin/")
              ? { authenticated: false, enabled: false }
              : path.includes("/radar")
                ? {
                    ...(demoRequest(path) as { items: unknown[] }),
                    items: (
                      demoRequest(path) as { items: unknown[] }
                    ).items.slice(0, 1),
                  }
                : demoRequest(path),
          ),
          { headers: { "Content-Type": "application/json" } },
        ),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("isolates mobile navigation, traps focus, closes on Escape and restores its opener", async () => {
  render(<App />);
  const opener = screen.getByRole("button", { name: "Open navigation" });
  await userEvent.click(opener);
  const dialog = screen.getByRole("dialog", { name: "Navigation" });
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Close navigation" }),
  );
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(screen.queryByRole("main")).toBeNull();
  await userEvent.tab({ shift: true });
  expect(document.activeElement).toBe(
    screen.getByRole("link", { name: /Owner area/ }),
  );
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
  expect(screen.getByRole("main")).toBeTruthy();
});

it("keeps the same URL-backed filters inside the mobile drawer and returns focus", async () => {
  window.history.replaceState(null, "", "/?chain=sol&page=3");
  render(<Radar search="?chain=sol&page=3" meta={null} />);
  const opener = screen.getByRole("button", { name: "Filters & sort" });
  await userEvent.click(opener);
  await userEvent.selectOptions(
    screen.getByLabelText("Observation window"),
    "168",
  );
  expect(window.location.search).toContain("age_hours=168");
  expect(window.location.search).toContain("chain=sol");
  expect(new URLSearchParams(window.location.search).has("page")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Show results" }));
  expect(document.activeElement).toBe(opener);
});

it("does not dismiss or repeat an owner action while confirmation is pending", async () => {
  function Pending() {
    const [open, setOpen] = useState(true);
    return open ? (
      <Modal
        labelledBy="pending"
        dismissible={false}
        onDismiss={() => setOpen(false)}
      >
        <h2 id="pending">Pending result</h2>
        <button disabled>Waiting</button>
      </Modal>
    ) : (
      <p>Closed</p>
    );
  }
  render(<Pending />);
  await userEvent.keyboard("{Escape}");
  expect(screen.getByRole("dialog", { name: "Pending result" })).toBeTruthy();
  expect(screen.queryByText("Closed")).toBeNull();
});

it("restores the selected report window through navigation back", async () => {
  window.history.replaceState(null, "", "/reports?hours=72");
  render(<Reports />);
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([path]) => String(path).includes("hours=72")),
    ).toBe(true),
  );
  await userEvent.click(screen.getByRole("button", { name: "4h" }));
  expect(window.location.search).toContain("hours=4");
  act(() => navigate("/reports?hours=72"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "72h" }).className).toBe(
      "active",
    ),
  );
});

it("preserves same-origin research context and rejects external return destinations", () => {
  window.history.replaceState(
    null,
    "",
    "/tokens/sol/address?return_to=" +
      encodeURIComponent("/?chain=sol&age_hours=168&page=3"),
  );
  expect(researchReturn()).toBe("/?chain=sol&age_hours=168&page=3");
  window.history.replaceState(
    null,
    "",
    "/tokens/sol/address?return_to=" + encodeURIComponent("//external.invalid"),
  );
  expect(researchReturn()).toBe("/");
});
