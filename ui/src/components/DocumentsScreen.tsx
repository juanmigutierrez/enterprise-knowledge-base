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
        <section className="split">
            <div className="pane">
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
                    <input
                        placeholder="Filter by item…"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                    />
                    <span className="muted">{rows.length} items</span>
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
                                    <td className="mono">{d.document_key}</td>
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
                </div>
            </div>
            <aside className="pane history">
                <h2>{selected ?? "History"}</h2>
                <div className="scroll">
                    {selected ? (
                        <EventTable
                            events={[...(history.data?.events ?? [])].reverse()}
                            showKey={false}
                        />
                    ) : (
                        <p className="muted">
                            Click an item to see every event it has had, newest
                            first.
                        </p>
                    )}
                </div>
            </aside>
        </section>
    );
}
