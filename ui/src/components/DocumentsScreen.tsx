import { useState } from "react";
import { api, type DocumentView } from "../api/client";
import { orDash, readers, time } from "../format";
import { usePolling } from "../usePolling";
import { EventTable } from "./EventTable";
import { OpBadge } from "./OpBadge";

/** Every tracked item with its current state; click one to see its history. */
export function DocumentsScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [filter, setFilter] = useState("");
    const [selected, setSelected] = useState<string | null>(null);
    const documents = usePolling(
        () => api.documents({ source: source || null }),
        3000,
        [source],
    );
    const history = usePolling(
        () =>
            selected
                ? api.events({ document_key: selected })
                : Promise.resolve({ events: [], cursor: 0 }),
        3000,
        [selected],
    );

    const rows = (documents.data ?? []).filter((d) =>
        d.document_key.toLowerCase().includes(filter.toLowerCase()),
    );

    return (
        <section className={selected ? "document-view with-history" : "document-view"}>
            <div className="pane">
                <div className="panel-title">
                    <h2>Document register</h2>
                    {documents.data && <span>{rows.length} items</span>}
                </div>
                <div className="toolbar">
                    <select
                        aria-label="Filter documents by source"
                        value={source}
                        onChange={(e) => setSource(e.target.value)}
                    >
                        <option value="">All sources</option>
                        {sources.map((s) => (
                            <option key={s}>{s}</option>
                        ))}
                    </select>
                    <input
                        type="search"
                        aria-label="Filter by document key"
                        placeholder="Filter by document key…"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                    />
                </div>
                {documents.error && <p className="error">{documents.error}</p>}
                <div className="scroll">
                    <table className="grid selectable">
                        <thead>
                            <tr>
                                <th>item</th>
                                <th>last</th>
                                <th>version</th>
                                <th>person</th>
                                <th>changed by</th>
                                <th>readers</th>
                                <th>events</th>
                                <th>at</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((d: DocumentView) => (
                                <tr
                                    key={d.document_key}
                                    className={[
                                        d.deleted ? "deleted" : "",
                                        d.document_key === selected ? "on" : "",
                                    ].join(" ")}
                                    onClick={() => setSelected(d.document_key)}
                                >
                                    <td>
                                        <button
                                            className="item-link mono"
                                            onClick={() => setSelected(d.document_key)}
                                        >
                                            {d.document_key}
                                        </button>
                                    </td>
                                    <td>
                                        <OpBadge op={d.last.op} />
                                    </td>
                                    <td className="mono">{d.last.version}</td>
                                    <td>{orDash(d.last.person_id)}</td>
                                    <td>{orDash(d.last.changed_by)}</td>
                                    <td>
                                        {d.deleted ? "—" : readers(d.last.acl)}
                                    </td>
                                    <td className="num">{d.events}</td>
                                    <td>{time(d.last.occurred_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {documents.data && !rows.length && (
                        <p className="empty">No documents match these filters.</p>
                    )}
                </div>
            </div>
            {selected && (
                <aside className="pane history">
                    <div className="panel-title">
                        <h2>History · {selected}</h2>
                        <button onClick={() => setSelected(null)}>Close history</button>
                    </div>
                    {history.error && <p className="error">{history.error}</p>}
                    <div className="scroll">
                        <EventTable
                            events={[...(history.data?.events ?? [])].reverse()}
                            showKey={false}
                        />
                    </div>
                </aside>
            )}
        </section>
    );
}
