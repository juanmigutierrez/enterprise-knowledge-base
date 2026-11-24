import { useState } from "react";
import { api } from "./api/client";
import { ChangeLogScreen } from "./components/ChangeLogScreen";
import { DiffScreen } from "./components/DiffScreen";
import { DocumentsScreen } from "./components/DocumentsScreen";
import { EventsPanel } from "./components/EventsPanel";
import { ChunkScreen } from "./components/ChunkScreen";
import { ParseScreen } from "./components/ParseScreen";
import { usePolling } from "./usePolling";

type Tab = "documents" | "events" | "changelog" | "t2" | "t3" | "t4";

const TABS: Record<Tab, string> = {
    documents: "Documents",
    events: "Events",
    changelog: "Change log",
    t2: "T2 Diff",
    t3: "T3 Parse",
    t4: "T4 Chunk",
};

export function App() {
    const [tab, setTab] = useState<Tab>("documents");
    const sources = usePolling(api.sources, 3000);
    const names = (sources.data ?? []).map((s) => s.source);

    return (
        <div className="app">
            <header>
                <h1>ekb inspector</h1>
                <span className="tag">local · read-only</span>
                <nav>
                    {(Object.keys(TABS) as Tab[]).map((name) => (
                        <button
                            key={name}
                            className={tab === name ? "on" : ""}
                            onClick={() => setTab(name)}
                        >
                            {TABS[name]}
                        </button>
                    ))}
                </nav>
            </header>
            <div className="sources">
                {sources.error && (
                    <span className="error">
                        Inspector API unreachable: {sources.error}
                    </span>
                )}
                {(sources.data ?? []).map((s) => (
                    <div key={s.source} className="source">
                        <strong>{s.source}</strong>
                        <span>{s.items} items</span>
                        <span>{s.deleted} deleted</span>
                        <span>{s.events} events</span>
                    </div>
                ))}
            </div>
            <main>
                {tab === "documents" && <DocumentsScreen sources={names} />}
                {tab === "events" && <EventsPanel sources={names} />}
                {tab === "changelog" && <ChangeLogScreen sources={names} />}
                {tab === "t2" && <DiffScreen sources={names} />}
                {tab === "t3" && <ParseScreen sources={names} />}
                {tab === "t4" && <ChunkScreen sources={names} />}
            </main>
        </div>
    );
}
