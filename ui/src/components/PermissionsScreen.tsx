import { useState } from "react";
import { api, type MembershipView, type PublicationView } from "../api/client";
import { time } from "../format";
import {
    byActive,
    canRead,
    peopleCount,
    readersLabel,
    readersNewer,
    shortKey,
    statusClass,
    statusLabel,
} from "../permissions";
import { usePolling } from "../usePolling";

/**
 * T8 Permissions (S11): what is searchable, and for whom.
 * Top: every document with its publication status, the readers T8 copied from
 * the source (and the version they came from), and who could read it now.
 * Middle: who can read what, users against documents: each cell is the
 * document's groups against the user's groups today, nothing stored per user.
 * Bottom: the permission store's users, their groups, and the leavers who read
 * nothing whatever their groups say.
 */
export function PermissionsScreen() {
    const view = usePolling(api.t8Permissions, 3000);
    const [selected, setSelected] = useState<string | null>(null);
    const documents = view.data?.documents ?? [];
    const users = byActive(view.data?.users ?? []);
    const published = documents.filter((d) => d.status === "published");
    const shown = documents.find((d) => d.document_key === selected) ?? null;

    return (
        <section className="stack flow">
            <div className="pane compact">
                <h2>T8 · Readers on every chunk, then the publication gate</h2>
                <p className="muted">
                    T8 copies each page’s readers (user and group IDs) onto its chunks
                    and publishes them to the search store, but only chunks with readers,
                    a sensitivity (T5) and a vector (T7). Groups stay groups: who is in
                    them comes from the identity provider and is looked up when someone
                    asks, so a group change rewrites no chunk. A change of readers alone
                    takes T2’s fast lane straight here; nothing is embedded again. A
                    leaver reads nothing, whatever their groups.
                </p>
                {view.error && <p className="error">{view.error}</p>}
                <div className="counts">
                    <div className="count">
                        <strong className="num">{view.data?.published_chunks ?? 0}</strong>
                        <span className="muted">chunks published</span>
                    </div>
                    <div className="count">
                        <strong className="num">{view.data?.published_documents ?? 0}</strong>
                        <span className="muted">documents searchable</span>
                    </div>
                    <div className="count">
                        <strong className="num">{view.data?.active_users ?? 0}</strong>
                        <span className="muted">active users</span>
                    </div>
                    <div className="count">
                        <strong className="mono">{view.data?.resolver_id || "—"}</strong>
                        <span className="muted">resolver</span>
                    </div>
                </div>
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Documents</strong>
                    </div>
                    <div className="scroll">
                        <DocumentTable rows={documents} selected={selected} onSelect={setSelected} />
                    </div>
                </div>
                <aside className="pane">
                    <div className="toolbar">
                        <strong className="mono">{selected ?? "Readers"}</strong>
                    </div>
                    <div className="scroll">
                        {!selected && <p className="muted">Click a document to see its readers.</p>}
                        {selected && !shown && !view.error && (
                            <p className="muted">Not in the stores: deleted, or not processed yet.</p>
                        )}
                        {shown && <ReadersDetail document={shown} />}
                    </div>
                </aside>
            </div>
            <div className="pane compact">
                <h3>Who can read what now</h3>
                <AccessGrid documents={published} users={users} />
            </div>
            <div className="pane compact">
                <h3>Users in the permission store</h3>
                <UserTable rows={users} />
            </div>
        </section>
    );
}

function DocumentTable({
    rows,
    selected,
    onSelect,
}: {
    rows: PublicationView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">Nothing published yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>document</th>
                    <th>status</th>
                    <th>chunks</th>
                    <th>readers</th>
                    <th>can read now</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((d) => (
                    <tr
                        key={d.document_key}
                        className={d.document_key === selected ? "on" : ""}
                        onClick={() => onSelect(d.document_key)}
                    >
                        <td className="mono" title={d.title}>
                            {shortKey(d.document_key)}
                        </td>
                        <td>
                            <span className={statusClass(d.status)}>{statusLabel(d.status)}</span>
                        </td>
                        <td className="num">{d.chunks}</td>
                        <td className="mono">{readersLabel(d.acl)}</td>
                        <td className="num" title={d.readers.join(", ")}>
                            {peopleCount(d.readers.length)}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function ReadersDetail({ document }: { document: PublicationView }) {
    return (
        <div>
            <h3 className="doc-title">{document.title || shortKey(document.document_key)}</h3>
            <p className="muted">
                <span className={statusClass(document.status)}>{statusLabel(document.status)}</span>{" "}
                · {document.chunks} chunks in the search store
                {document.text_version && <> · text v{document.text_version}</>}
                {document.acl_version && <> · readers v{document.acl_version}</>}
                {readersNewer(document) && " (changed by the fast lane)"}
            </p>
            <p>
                Readers: <span className="mono">{readersLabel(document.acl)}</span>
            </p>
            {document.unknown_groups.length > 0 && (
                <p className="pending">
                    No identity user is in {document.unknown_groups.join(", ")}: it admits nobody
                    until someone joins.
                </p>
            )}
            <p>
                Can read it now ({peopleCount(document.readers.length)}):{" "}
                <span className="mono">{document.readers.join(", ") || "—"}</span>
            </p>
            <p className="muted">
                {document.resolver_id && <>Resolved by {document.resolver_id}</>}
                {document.changed_at && <> · readers changed in the source {time(document.changed_at)}</>}
            </p>
        </div>
    );
}

function AccessGrid({
    documents,
    users,
}: {
    documents: PublicationView[];
    users: MembershipView[];
}) {
    if (!documents.length || !users.length)
        return <p className="empty">Nothing to show until pages are published and users loaded.</p>;
    return (
        <div className="scroll">
            <table className="grid access">
                <thead>
                    <tr>
                        <th>user</th>
                        {documents.map((d) => (
                            <th key={d.document_key} className="mono" title={d.title}>
                                {shortKey(d.document_key).replace("HR-", "")}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {users.map((u) => (
                        <tr key={u.user_id} className={u.active ? "" : "deleted"}>
                            <td className="mono">{u.user_id}</td>
                            {documents.map((d) => (
                                <td
                                    key={d.document_key}
                                    className={canRead(d, u.user_id) ? "cell yes" : "cell"}
                                >
                                    {canRead(d, u.user_id) ? "✓" : "·"}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function UserTable({ rows }: { rows: MembershipView[] }) {
    if (!rows.length) return <p className="empty">No users yet: the identity sync loads them.</p>;
    return (
        <table className="grid">
            <thead>
                <tr>
                    <th>user</th>
                    <th>status</th>
                    <th>groups</th>
                    <th>documents</th>
                    <th>record</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((u) => (
                    <tr key={u.user_id} className={u.active ? "" : "deleted"}>
                        <td className="mono">{u.user_id}</td>
                        <td>{u.active ? "active" : "left: reads nothing"}</td>
                        <td className="mono">{u.groups.join(", ")}</td>
                        <td className="num">{u.documents}</td>
                        <td className="mono">v{u.source_version}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
