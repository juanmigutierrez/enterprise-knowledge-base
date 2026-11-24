import { useState } from "react";
import { api, type DecisionView, type DiffAction } from "../api/client";
import { ACTIONS, avoidedRuns, totalDecisions } from "../diff";
import { readers, time, versionStep } from "../format";
import { usePolling } from "../usePolling";
import { ActionBadge } from "./ActionBadge";
import { OpBadge } from "./OpBadge";

const KEEP = 500;

/**
 * T2 Diff (S4): what each change event still needed, and why.
 * Top: how many events T2 processed, fast-tracked, skipped and purged.
 * Below: every decision, newest first; click one to see that item's
 * decisions in order, which is its history as T2 saw it.
 */
export function DiffScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [action, setAction] = useState<DiffAction | "">("");
    const [selected, setSelected] = useState<string | null>(null);
    const page = usePolling(
        () => api.t2Decisions({ source: source || null, action: action || null }),
        2000,
        [source, action],
    );
    const history = usePolling(
        () =>
            selected
                ? api.t2Decisions({ document_key: selected })
                : Promise.resolve(null),
        2000,
        [selected],
    );
    const counts = page.data?.counts;
    const rows = [...(page.data?.decisions ?? [])].reverse().slice(0, KEEP);
    const itemRows =
        history.data && selected === history.data.decisions[0]?.document_key
            ? history.data.decisions
            : [];

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>T2 · Diff against the registry</h2>
                <p className="muted">
                    For each event, T2 compares the content hash and the readers
                    with what was last processed, then decides what the rest of
                    the pipeline must do. A delete is never skipped, and neither
                    is a revocation.
                </p>
                {page.error && <p className="error">{page.error}</p>}
                {counts && (
                    <div className="counts">
                        {ACTIONS.map(({ action: a, next }) => (
                            <div key={a} className="count">
                                <ActionBadge action={a} />
                                <strong className="num">{counts[a]}</strong>
                                <span className="muted">{next}</span>
                            </div>
                        ))}
                        <div className="count">
                            <span className="muted">T3–T7 runs avoided</span>
                            <strong className="num">{avoidedRuns(counts)}</strong>
                            <span className="muted">
                                of {totalDecisions(counts)} decisions
                            </span>
                        </div>
                    </div>
                )}
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Decisions</strong>
                        <select
                            value={source}
                            onChange={(e) => setSource(e.target.value)}
                        >
                            <option value="">All sources</option>
                            {sources.map((s) => (
                                <option key={s}>{s}</option>
                            ))}
                        </select>
                        <select
                            value={action}
                            onChange={(e) =>
                                setAction(e.target.value as DiffAction | "")
                            }
                        >
                            <option value="">All actions</option>
                            {ACTIONS.map(({ action: a }) => (
                                <option key={a} value={a}>
                                    {a}
                                </option>
                            ))}
                        </select>
                        <span className="muted">
                            showing {rows.length} · newest first
                        </span>
                    </div>
                    <div className="scroll">
                        <DecisionTable
                            rows={rows}
                            selected={selected}
                            onSelect={setSelected}
                        />
                    </div>
                </div>
                <aside className="pane history">
                    <h2>{selected ?? "Item history"}</h2>
                    <div className="scroll">
                        {!selected && (
                            <p className="muted">
                                Click a decision to see every decision T2 made
                                for that item, oldest first.
                            </p>
                        )}
                        {history.error && (
                            <p className="error">{history.error}</p>
                        )}
                        {selected && (
                            <ol className="decisions">
                                {itemRows.map((d) => (
                                    <li key={d.seq}>
                                        <ActionBadge action={d.action} />{" "}
                                        <OpBadge op={d.op} />{" "}
                                        <span className="mono">
                                            {versionStep(
                                                d.previous_version,
                                                d.version,
                                            )}
                                        </span>
                                        <div>{d.reason}</div>
                                        <div className="muted">
                                            readers: {readers(d.acl)}
                                            {d.purged > 0 &&
                                                ` · ${d.purged} original${d.purged > 1 ? "s" : ""} purged`}
                                        </div>
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>
                </aside>
            </div>
        </section>
    );
}

function DecisionTable({
    rows,
    selected,
    onSelect,
}: {
    rows: DecisionView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">No decisions yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>#</th>
                    <th>at</th>
                    <th>item</th>
                    <th>op</th>
                    <th>version</th>
                    <th>action</th>
                    <th>why</th>
                    <th>purged</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((d) => (
                    <tr
                        key={d.seq}
                        className={[
                            d.action === "skip" ? "skipped" : "",
                            d.document_key === selected ? "on" : "",
                        ].join(" ")}
                        onClick={() => onSelect(d.document_key)}
                    >
                        <td className="num">{d.seq}</td>
                        <td>{time(d.decided_at)}</td>
                        <td className="mono">{d.document_key}</td>
                        <td>
                            <OpBadge op={d.op} />
                        </td>
                        <td className="mono">
                            {versionStep(d.previous_version, d.version)}
                        </td>
                        <td>
                            <ActionBadge action={d.action} />
                        </td>
                        <td className="reason">{d.reason}</td>
                        <td className="num">{d.purged || ""}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
