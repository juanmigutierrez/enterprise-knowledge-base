import { useState } from "react";
import { api } from "./api/client";
import { DocumentsScreen } from "./components/DocumentsScreen";
import { EventsPanel } from "./components/EventsPanel";
import { usePolling } from "./usePolling";

type Tab = "documents" | "events";

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
                    {(["documents", "events"] as const).map((name) => (
                        <button
                            key={name}
                            className={tab === name ? "on" : ""}
                            onClick={() => setTab(name)}
                        >
                            {name === "documents" ? "Documents" : "Events"}
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
                {tab === "documents" ? (
                    <DocumentsScreen sources={names} />
                ) : (
                    <EventsPanel sources={names} />
                )}
            </main>
        </div>
    );
}
