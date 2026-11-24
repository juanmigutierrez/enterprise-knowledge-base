import { poll } from "./poll";
import { useEffect, useRef, useState } from "react";

/** Calls `load` now and every `intervalMs`; keeps the last result and error. */
export function usePolling<T>(
    load: () => Promise<T>,
    intervalMs: number,
    deps: unknown[] = [],
) {
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<string | null>(null);
    const loadRef = useRef(load);

    useEffect(() => {
        loadRef.current = load;
    });

    useEffect(() => {
        let cancelled = false;
        const tick = () =>
            loadRef
                .current()
                .then((result) => {
                    if (!cancelled) {
                        setData(result);
                        setError(null);
                    }
                })
                .catch((reason: unknown) => {
                    if (!cancelled) setError(String(reason));
                });
        const stop = poll(tick, intervalMs);
        return () => {
            cancelled = true;
            stop();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are the caller's
    }, [intervalMs, ...deps]);

    return { data, error };
}
