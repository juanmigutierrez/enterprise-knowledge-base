import { useState } from "react";
import { api } from "./api/client";
import { ChangeLogScreen } from "./components/ChangeLogScreen";
import { DocumentsScreen } from "./components/DocumentsScreen";
import { EventsPanel } from "./components/EventsPanel";
import { usePolling } from "./usePolling";

type Tab = "documents" | "events" | "changelog";

const TABS: Record<Tab, string> = {
    documents: "Documents",
    events: "Events",
    changelog: "Change log",
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
            </main>
        </div>
    );
}
