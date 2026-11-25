import { useState } from "react";
import { api, type EntityKind, type EntityLinkView, type EntityView } from "../api/client";
import { byDocument, entityClass, linkCount, mentionLabel } from "../entities";
import { usePolling } from "../usePolling";

/**
 * T6 Entities (S9): the people and teams the HR records define, and the
 * chunks linked to each. Left: every entity in the store, with how many
 * chunks name it. Right: one entity, the record that defines it, and every
 * chunk that names it with the words behind the link. Below: links to
 * entities the store no longer holds (a leaver), kept by pages that have not
 * changed since.
 */
export function EntitiesScreen() {
    const [kind, setKind] = useState<EntityKind | "">("");
    const [selected, setSelected] = useState<string | null>(null);
    const list = usePolling(() => api.t6Entities({ kind: kind || null }), 3000, [kind]);
    const entities = list.data?.entities ?? [];
    const stale = list.data?.stale_links ?? [];
    const shown = entities.find((e) => e.entity_id === selected) ?? null;

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>T6 · Link chunks to the people and teams they name</h2>
                <p className="muted">
                    The HR record API defines who the company knows: every employee
                    becomes a person under their identity user ID, every team a team.
                    T6 runs after T5 and links each chunk by exact match: a full name
                    or a work email for a person, a team name next to a word like
                    “team” for a team. A first name alone, or a bare “People”, is not
                    a link. A leaver’s purge removes them from the store.
                </p>
                {list.error && <p className="error">{list.error}</p>}
                <div className="counts">
                    <div className="count">
                        <strong className="num">{list.data?.people ?? 0}</strong>
                        <span className="muted">people</span>
                    </div>
                    <div className="count">
                        <strong className="num">{list.data?.teams ?? 0}</strong>
                        <span className="muted">teams</span>
                    </div>
                    <div className="count">
                        <strong className="num">{list.data?.linked_chunks ?? 0}</strong>
                        <span className="muted">linked chunks</span>
                    </div>
                    <div className="count">
                        <strong className="num">{stale.length}</strong>
                        <span className="muted">links to removed entities</span>
                    </div>
                </div>
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Entity store</strong>
                        <select
                            value={kind}
                            onChange={(e) => {
                                setKind(e.target.value as EntityKind | "");
                                setSelected(null);
                            }}
                        >
                            <option value="">People and teams</option>
                            <option value="person">People</option>
                            <option value="team">Teams</option>
                        </select>
                    </div>
                    <div className="scroll">
                        <EntityTable rows={entities} selected={selected} onSelect={setSelected} />
                    </div>
                </div>
                <aside className="pane">
                    <div className="toolbar">
                        <strong className="mono">{selected ?? "Links"}</strong>
                    </div>
                    <div className="scroll">
                        {!selected && <p className="muted">Click an entity to see its links.</p>}
                        {selected && !shown && !list.error && (
                            <p className="muted">Not in the store: its record was deleted.</p>
                        )}
                        {shown && <EntityDetail entity={shown} />}
                    </div>
                </aside>
            </div>
            {stale.length > 0 && (
                <div className="pane compact">
                    <h3>Links to entities the store no longer holds</h3>
                    <p className="muted">
                        Links are written when a page is processed. These pages have not
                        changed since the entity went, so they keep its ID until they
                        change or are rebuilt; the store no longer answers for it.
                    </p>
                    <LinkList links={stale} showEntity />
                </div>
            )}
        </section>
    );
}

function EntityTable({
    rows,
    selected,
    onSelect,
}: {
    rows: EntityView[];
    selected: string | null;
    onSelect: (entityId: string) => void;
}) {
    if (!rows.length) return <p className="empty">No entities yet: the HR records load them.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>entity</th>
                    <th>name</th>
                    <th>also named</th>
                    <th>team</th>
                    <th>links</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((e) => (
                    <tr
                        key={e.entity_id}
                        className={e.entity_id === selected ? "on" : ""}
                        onClick={() => onSelect(e.entity_id)}
                    >
                        <td>
                            <span className={entityClass(e.entity_id)}>{e.entity_id}</span>
                        </td>
                        <td>{e.name}</td>
                        <td className="mono">{e.aliases.join(", ")}</td>
                        <td className="mono">{e.team_id ?? ""}</td>
                        <td className="num">{e.links.length}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function EntityDetail({ entity }: { entity: EntityView }) {
    return (
        <div>
            <h3 className="doc-title">{entity.name}</h3>
            <p className="muted">
                {entity.kind} · defined by{" "}
                <span className="mono">{entity.source_key}</span> (v{entity.source_version})
                · {linkCount(entity.links.length)}
            </p>
            {entity.links.length === 0 && (
                <p className="muted">No chunk names it yet.</p>
            )}
            <LinkList links={entity.links} />
        </div>
    );
}

function LinkList({ links, showEntity = false }: { links: EntityLinkView[]; showEntity?: boolean }) {
    return (
        <div className="chunks">
            {byDocument(links).map((group) => (
                <div key={group.document_key} className="chunk">
                    <div className="chunk-head">
                        <span className="mono">{group.document_key}</span>
                        <span>{group.title}</span>
                    </div>
                    <ul className="links">
                        {group.links.map((link) => (
                            <li key={`${link.chunk_id}-${link.entity_id}`}>
                                <span className="mono">{link.chunk_id}</span>{" "}
                                {showEntity && (
                                    <span className={entityClass(link.entity_id)}>
                                        {link.entity_id}
                                    </span>
                                )}{" "}
                                {mentionLabel(link)}
                            </li>
                        ))}
                    </ul>
                </div>
            ))}
        </div>
    );
}
