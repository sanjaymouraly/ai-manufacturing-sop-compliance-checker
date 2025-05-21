import { useCallback, useEffect, useRef, useState } from 'react';

interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
}

/**
 * Minimal data-fetching hook: runs `fn` on mount and whenever `deps` change,
 * discarding results from superseded requests.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> & {
  reload: () => void;
  setData: (value: T) => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  const latest = useRef(0);

  // `fn` is re-created on every render by callers; deps drive re-fetching instead.
  const run = useRef(fn);
  run.current = fn;

  useEffect(() => {
    const ticket = ++latest.current;
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: null }));
    run
      .current()
      .then((data) => {
        if (!cancelled && ticket === latest.current) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (!cancelled && ticket === latest.current) setState({ data: null, error, loading: false });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const setData = useCallback((value: T) => setState({ data: value, error: null, loading: false }), []);
  return { ...state, reload, setData };
}
