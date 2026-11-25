import { useState } from "react";
import {
    api,
    type StepTotalsView,
    type TracedDocumentView,
    type TraceStatus,
    type TraceView,
} from "../api/client";
import { OP_LABEL, time } from "../format";
import { STATUSES, ms, route, sourceId, statusClass, stepClass, where } from "../pipeline";
import { usePolling } from "../usePolling";

/**
 * Pipeline (S12): the worker, one consumer group running T2 → T8 for every event.
 * Top: how each item's latest attempt ended, and every step's runs, failures and time.
 * Left: every item the worker has seen. Right: one item's attempts, newest first, each
 * step by step: what it did, or why it failed. A trace holds no content, so a deleted
 * item's history stays.
 */
export function PipelineScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [status, setStatus] = useState<TraceStatus | "">("");
    const [selected, setSelected] = useState<string | null>(null);
    const view = usePolling(
        () => api.pipeline({ source: source || null, status: status || null }),
        2000,
        [source, status],
    );
    const detail = usePolling(
        () =>
            selected ? api.pipelineTrace({ document_key: selected }) : Promise.resolve(null),
        2000,
        [selected],
    );
    const statuses = view.data?.statuses;
    const traces = detail.data && detail.data.document_key === selected ? detail.data.traces : [];

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>Worker · T2 → T8 on the bus</h2>
                <p className="muted">
                    One consumer group, <span className="mono">worker</span>, reads the
                    change log. For each event, in its item’s order, one handler runs the
                    whole write path with the options the profile names. T2 records the
                    event only once every step succeeded, so a failure means “deliver it
                    again”; after three failures the event is parked and its item
                    withdrawn from search until it is retried.
                </p>
                {view.error && <p className="error">{view.error}</p>}
                {statuses && (
                    <div className="counts">
                        {STATUSES.map(({ status: s, meaning }) => (
                            <div key={s} className="count" title={meaning}>
                                <span className={statusClass(s)}>{s}</span>
                                <strong className="num">{statuses[s] ?? 0}</strong>
                            </div>
                        ))}
                        <div className="count">
                            <span className="muted">attempts</span>
                            <strong className="num">{view.data?.attempts ?? 0}</strong>
                        </div>
                    </div>
                )}
                <StepTotals rows={view.data?.steps ?? []} />
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Items</strong>
                        <select value={source} onChange={(e) => setSource(e.target.value)}>
                            <option value="">All sources</option>
                            {sources.map((s) => (
                                <option key={s}>{s}</option>
                            ))}
                        </select>
                        <select
                            value={status}
                            onChange={(e) => setStatus(e.target.value as TraceStatus | "")}
                        >
                            <option value="">Any latest status</option>
                            {STATUSES.map(({ status: s }) => (
                                <option key={s} value={s}>
                                    {s}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div className="scroll">
                        <ItemTable
                            rows={view.data?.documents ?? []}
                            selected={selected}
                            onSelect={setSelected}
                        />
                    </div>
                </div>
                <aside className="pane">
                    <div className="toolbar">
                        <strong className="mono">{selected ?? "Trace"}</strong>
                    </div>
                    <div className="scroll">
                        {!selected && (
                            <p className="muted">
                                Click an item to follow it through the line, attempt by
                                attempt.
                            </p>
                        )}
                        {detail.error && <p className="error">{detail.error}</p>}
                        {traces.map((t) => (
                            <Attempt key={t.seq} trace={t} />
                        ))}
                    </div>
                </aside>
            </div>
        </section>
    );
}

function StepTotals({ rows }: { rows: StepTotalsView[] }) {
    if (!rows.length) return null;
    return (
        <table className="grid totals">
            <thead>
                <tr>
                    <th>step</th>
                    {rows.map((r) => (
                        <th key={r.step}>{r.step}</th>
                    ))}
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td className="muted">runs</td>
                    {rows.map((r) => (
                        <td key={r.step} className="num">
                            {r.runs}
                        </td>
                    ))}
                </tr>
                <tr>
                    <td className="muted">failed</td>
                    {rows.map((r) => (
                        <td key={r.step} className={r.failed ? "num failed" : "num"}>
                            {r.failed || ""}
                        </td>
                    ))}
                </tr>
                <tr>
                    <td className="muted">time</td>
                    {rows.map((r) => (
                        <td key={r.step} className="num">
                            {ms(r.total_ms)}
                        </td>
                    ))}
                </tr>
            </tbody>
        </table>
    );
}

function ItemTable({
    rows,
    selected,
    onSelect,
}: {
    rows: TracedDocumentView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">No events handled yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>item</th>
                    <th>source</th>
                    <th>latest</th>
                    <th>version</th>
                    <th>attempts</th>
                    <th>time</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((d) => (
                    <tr
                        key={d.document_key}
                        className={d.document_key === selected ? "on" : ""}
                        onClick={() => onSelect(d.document_key)}
                    >
                        <td className="mono">{sourceId(d.document_key)}</td>
                        <td>{d.source}</td>
                        <td>
                            <span className={statusClass(d.last.status)}>{d.last.status}</span>
                        </td>
                        <td className="mono">v{d.last.version}</td>
                        <td className="num">{d.traces}</td>
                        <td className="num">{ms(d.last.ms)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Attempt({ trace }: { trace: TraceView }) {
    return (
        <div className="attempt">
            <div className="attempt-head">
                <span className={statusClass(trace.status)}>{trace.status}</span>
                <span className="mono">v{trace.version}</span>
                <span className="muted">
                    {OP_LABEL[trace.op]} · {where(trace)} · attempt {trace.attempt} ·{" "}
                    {ms(trace.ms)} · {time(trace.started_at)}
                </span>
            </div>
            <div className="attempt-route mono">{route(trace)}</div>
            {trace.error && <p className="error">{trace.error}</p>}
            <ol className="steps">
                {trace.steps.map((s, i) => (
                    <li key={i} className={stepClass(s)}>
                        <span className="step-name">{s.step}</span>
                        <span className="step-ms">{ms(s.ms)}</span>
                        <span className="step-detail">{s.detail}</span>
                    </li>
                ))}
            </ol>
        </div>
    );
}
