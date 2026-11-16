import { ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError } from "./api";

/** A permission limit is not a failure: the server told us this role may not
 * see this, which is the system working. Shown calmly, with the way out. */
export function Restricted({ what, roles }: { what: string; roles: string[] }) {
  return (
    <div className="notice">
      <b>{what} is not visible to your role.</b> The roles that can see it:{" "}
      {roles.join(", ")}. Switch role in the header to look around; a real deployment
      assigns roles through your identity provider.
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="hint">Loading {what}…</p>;
}

export function ErrorBanner({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.message : String(error);
  return <div className="error">{msg}</div>;
}

export type AsyncState<T> = {
  data: T | null;
  error: unknown;
  forbidden: boolean;
  loading: boolean;
  reload: () => void;
};

/** Fetch on mount and whenever `deps` change (the role token is always one of
 * them, so switching role refetches everything). A 403 is reported separately
 * from a real error. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    setForbidden(false);
    run()
      .then((d) => live && setData(d))
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 403) setForbidden(true);
        else setError(e);
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [run, tick]);

  return { data, error, forbidden, loading, reload: () => setTick((t) => t + 1) };
}

/** Standard screen scaffold: one place that decides between loading,
 * forbidden, error and content, so every screen behaves the same way. */
export function Panel<T>({ state, what, roles, children }: {
  state: AsyncState<T>;
  what: string;
  roles: string[];
  children: (data: T) => ReactNode;
}) {
  if (state.forbidden) return <Restricted what={what} roles={roles} />;
  if (state.error) return <ErrorBanner error={state.error} />;
  if (state.loading && state.data === null) return <Loading what={what.toLowerCase()} />;
  if (state.data === null) return null;
  return <>{children(state.data)}</>;
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 6000);
    return () => clearTimeout(t);
  }, [onDone]);
  return (
    <div className="toast" role="status">
      {text}
    </div>
  );
}
