import { useCallback, useEffect, useRef, useState } from "react";
import { demoRequest, previewState } from "./demo";
export const DEMO = import.meta.env.VITE_DEMO_MODE === "true";
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  if (DEMO && !path.includes("/admin/")) {
    const state = previewState(
      new URLSearchParams(window.location.search).get("preview_state"),
    );
    if (state === "loading")
      await new Promise((resolve) => setTimeout(resolve, 1500));
    return demoRequest(path, state) as T;
  }
  if (DEMO && path.includes("/admin/"))
    throw new ApiError(
      "Owner controls are disabled in the synthetic demo.",
      503,
    );
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), 15000);
  const cancel = () => timeout.abort();
  options.signal?.addEventListener("abort", cancel);
  if (options.signal?.aborted) cancel();
  try {
    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...options.headers },
      signal: timeout.signal,
    });
    if (!response.ok) {
      let message =
        response.status === 401
          ? "Sign in to access owner controls."
          : response.status === 403
            ? "Your session could not authorize this request. Sign in again."
            : `The data service returned ${response.status}.`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
        else if (typeof body.error === "string") message = body.error;
      } catch {
        /* A proxy HTML error is not suitable for display. */
      }
      throw new ApiError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timeout.signal.aborted)
      throw new Error("The data service did not respond in time. Try again.");
    throw new Error(
      "Cannot reach the data service. Check the connection and try again.",
    );
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}
export function useData<T>(path: string | null, poll = true) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState<string | null>(null),
    [loading, setLoading] = useState(true),
    [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(() => setGeneration((n) => n + 1), []);
  const previousPath = useRef(path);
  useEffect(() => {
    let disposed = false,
      timer: ReturnType<typeof setTimeout> | undefined,
      controller: AbortController | undefined,
      failures = 0,
      sequence = 0;
    if (previousPath.current !== path) {
      setData(null);
      setError(null);
      previousPath.current = path;
    }
    if (!path) {
      setLoading(false);
      return;
    }
    const run = async () => {
      if (document.hidden && poll) {
        timer = setTimeout(run, 30000);
        return;
      }
      clearTimeout(timer);
      const current = ++sequence;
      controller?.abort();
      controller = new AbortController();
      try {
        const next = await request<T>(path, { signal: controller.signal });
        if (!disposed && current === sequence) {
          setData(next);
          setError(null);
          setRefreshedAt(Date.now() / 1000);
          failures = 0;
        }
      } catch (e) {
        if (!disposed && current === sequence) {
          setError(e instanceof Error ? e.message : "Data unavailable.");
          failures++;
        }
      } finally {
        if (!disposed && current === sequence) {
          setLoading(false);
          if (poll)
            timer = setTimeout(run, Math.min(30000 * 2 ** failures, 240000));
        }
      }
    };
    setLoading(true);
    void run();
    const visible = () => {
      if (!document.hidden && poll) {
        clearTimeout(timer);
        void run();
      }
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      disposed = true;
      clearTimeout(timer);
      ++sequence;
      controller?.abort();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [path, generation, poll]);
  return { data, error, loading, refreshedAt, refresh };
}
export function navigate(path: string) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo({ top: 0, behavior: "instant" });
}
export function useLocation() {
  const [value, setValue] = useState(
    () => window.location.pathname + window.location.search,
  );
  useEffect(() => {
    const change = () =>
      setValue(window.location.pathname + window.location.search);
    window.addEventListener("popstate", change);
    return () => window.removeEventListener("popstate", change);
  }, []);
  return value;
}
