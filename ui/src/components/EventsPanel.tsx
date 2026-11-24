import { poll } from "../poll";
import { useEffect, useRef, useState } from "react";
import { api, type EventView } from "../api/client";
import { EventTable } from "./EventTable";

const KEEP = 500;

/** Live feed: asks for events after the last cursor every two seconds, newest first. */
export function EventsPanel({ sources }: { sources: string[] }) {
    const [events, setEvents] = useState<EventView[]>([]);
    const [source, setSource] = useState("");
    const [paused, setPaused] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [shownCursor, setShownCursor] = useState(0);
    const cursor = useRef(0);

    useEffect(() => {
        if (paused) return;
        let cancelled = false;
        const tick = () =>
            api
                .events({ since: cursor.current })
                .then((page) => {
                    if (cancelled) return;
                    cursor.current = page.cursor;
                    setShownCursor(page.cursor);
                    if (page.events.length) {
                        setEvents((current) =>
                            [...[...page.events].reverse(), ...current].slice(
                                0,
                                KEEP,
                            ),
                        );
                    }
                    setError(null);
                })
                .catch(
                    (reason: unknown) => !cancelled && setError(String(reason)),
                );
        const stop = poll(tick, 2000);
        return () => {
            cancelled = true;
            stop();
        };
    }, [paused]);

    const shown = source ? events.filter((e) => e.source === source) : events;
    return (
        <section className="pane">
            <div className="toolbar">
                <select
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                >
                    <option value="">All sources</option>
                    {sources.map((s) => (
                        <option key={s}>{s}</option>
                    ))}
                </select>
                <button onClick={() => setPaused((p) => !p)}>
                    {paused ? "Resume" : "Pause"}
                </button>
                <span className="muted">
                    {paused ? "paused" : "live"} · cursor {shownCursor} ·
                    showing {shown.length}
                </span>
            </div>
            {error && <p className="error">{error}</p>}
            <div className="scroll">
                <EventTable events={shown} />
            </div>
        </section>
    );
}
