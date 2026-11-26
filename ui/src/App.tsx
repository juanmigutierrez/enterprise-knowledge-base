import { useState } from "react";
import { ArchitectureScreen } from "./components/ArchitectureScreen";
import { api } from "./api/client";
import { ChangeLogScreen } from "./components/ChangeLogScreen";
import { DiffScreen } from "./components/DiffScreen";
import { DocumentsScreen } from "./components/DocumentsScreen";
import { EntitiesScreen } from "./components/EntitiesScreen";
import { EventsPanel } from "./components/EventsPanel";
import { ChunkScreen } from "./components/ChunkScreen";
import { GatewayScreen } from "./components/GatewayScreen";
import { LifecycleScreen } from "./components/LifecycleScreen";
import { ParseScreen } from "./components/ParseScreen";
import { PermissionsScreen } from "./components/PermissionsScreen";
import { PipelineScreen } from "./components/PipelineScreen";
import { SearchScreen } from "./components/SearchScreen";
import { usePolling } from "./usePolling";

type Tab =
    | "documents"
    | "events"
    | "changelog"
    | "pipeline"
    | "t2"
    | "t3"
    | "t4"
    | "t6"
    | "t8"
    | "search"
    | "lifecycle"
    | "architecture"
    | "gateway";

const TABS: Record<Tab, string> = {
    documents: "Documents",
    events: "Events",
    changelog: "Change log",
    pipeline: "Pipeline",
    t2: "T2 Diff",
    t3: "T3 Parse",
    t4: "T4 Chunk",
    t6: "T6 Entities",
    t8: "T8 Permissions",
    search: "Search as user",
    lifecycle: "Lifecycle",
    architecture: "Architecture",
    gateway: "Model gateway",
};

const guides: Record<Tab, string> = {
    architecture: "Choose a lifecycle step to highlight its key components in the Docker Compose design. The offline inspector exercises the same logic with in-memory stores. For the actual worker history, open the lifecycle step.",
    documents: "Find the latest state of an item from any source. Filter by source or document key, then select the key to see who changed it and when.",
    events: "Watch changes arrive from the sources. Choose a source to narrow the feed, or pause it to read an event’s operation, version and readers.",
    changelog: "Trace what the system kept when a change arrived. Select an item in the registry to see the versions, sizes and checksums of its stored originals.",
    pipeline: "Follow a change through the worker. Filter by source or status, then select an item to see which steps ran, how long they took and where an attempt failed.",
    t2: "See how the worker decides what to do with a change. Filter by action and select an item to learn why it was processed, skipped, removed or given new readers.",
    t3: "See how a source document becomes readable text. Select a document, then switch between Blocks and Clean text to compare its structure with the extracted text.",
    t4: "Inspect the passages that search can return. Select a document and compare its chunks, section labels, sizes and embedding details.",
    t6: "Explore the people and teams that documents mention. Filter by People or Teams and select a name to find the passages linked to it.",
    t8: "Check who can read a document before it appears in search. Select a document to inspect its readers, then use the access grid to compare users.",
    search: "See how identity changes search results. Choose two users, ask one question and compare which passages each user can cite. Try the suggested salary question to see restricted access.",
    lifecycle: "Follow a policy from creation to deletion. Open a step to see what it tests, apply the change in the synthetic source, then compare the answers before and after.",
    gateway: "See which model handled a request and what it cost. Read the newest calls alongside provider availability, quotas and routes to distinguish a fresh request from a cache hit.",
};

const navigation = [
    { title: "Workspace", tabs: ["documents", "events", "changelog"] },
    { title: "Processing", tabs: ["pipeline", "t2", "t3", "t4", "t6", "t8"] },
    { title: "Access & operations (Tutorial)", tabs: ["search", "lifecycle", "architecture", "gateway"] },
] as const;

export function App() {
    const [tab, setTab] = useState<Tab>("documents");
    const [diagramStep, setDiagramStep] = useState("");
    const sources = usePolling(api.sources, 3000);
    const names = (sources.data ?? []).map((s) => s.source);

    return (
        <div className="app">
            <aside className="sidebar">
                <div className="brand">
                    <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
                    <span>Knowledge Base<small>Inspector</small></span>
                </div>
                <nav aria-label="Inspector screens">
                    {navigation.map((group) => (
                        <div className="nav-group" key={group.title}>
                            <h2>{group.title}</h2>
                            {group.tabs.map((name) => (
                                <button key={name} aria-current={tab === name ? "page" : undefined}
                                    onClick={() => setTab(name)}>
                                    {TABS[name]}
                                </button>
                            ))}
                        </div>
                    ))}
                </nav>
            </aside>
            <div className="workspace">
                <header className="page-head">
                    <div>
                        <h1>{TABS[tab]}</h1>
                        <p className="page-guide">{guides[tab]}</p>
                    </div>
                    <span className="connection" role="status" data-state={
                        sources.error ? "offline" : sources.data ? "online" : "connecting"
                    }>
                        {sources.error ? "API unavailable" : sources.data ? "Local API connected" : "Connecting to API"}
                    </span>
                </header>
                {sources.error && <p className="error source-error">{sources.error}</p>}
                {names.length > 0 && (
                    <section className="sources" aria-label="Source activity">
                        {sources.data?.map((source) => (
                            <div key={source.source} className="source">
                                <strong>{source.source}</strong>
                                <div className="source-volume"><b>{source.items}</b> items</div>
                                <small>{source.events} events · {source.deleted} deleted</small>
                            </div>
                        ))}
                    </section>
                )}
                <label className="mobile-nav">
                    View
                    <select value={tab} onChange={(event) => setTab(event.target.value as Tab)}>
                        {navigation.map((group) => (
                            <optgroup key={group.title} label={group.title}>
                                {group.tabs.map((name) => <option key={name} value={name}>{TABS[name]}</option>)}
                            </optgroup>
                        ))}
                    </select>
                </label>
                <main>
                    {tab === "documents" && <DocumentsScreen sources={names} />}
                    {tab === "events" && <EventsPanel sources={names} />}
                    {tab === "changelog" && <ChangeLogScreen sources={names} />}
                    {tab === "pipeline" && <PipelineScreen sources={names} />}
                    {tab === "t2" && <DiffScreen sources={names} />}
                    {tab === "t3" && <ParseScreen sources={names} />}
                    {tab === "t4" && <ChunkScreen sources={names} />}
                    {tab === "t6" && <EntitiesScreen />}
                    {tab === "t8" && <PermissionsScreen />}
                    {tab === "search" && <SearchScreen />}
                    {tab === "lifecycle" && <LifecycleScreen initialStep={diagramStep} onDiagram={(id) => {
                        setDiagramStep(id);
                        setTab("architecture");
                    }} />}
                    {tab === "architecture" && <ArchitectureScreen step={diagramStep} onSelect={setDiagramStep}
                        onReturn={() => setTab("lifecycle")} />}
                    {tab === "gateway" && <GatewayScreen />}
                </main>
            </div>
        </div>
    );
}
