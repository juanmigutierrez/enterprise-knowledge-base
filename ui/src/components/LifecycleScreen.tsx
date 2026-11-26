import { useState } from "react";
import {
    api,
    type LifecycleActionView,
    type LifecycleProbeView,
    type LifecycleStepView,
} from "../api/client";
import {
    actionState,
    answerClass,
    answerLabel,
    change,
    pageName,
    probeKey,
    routeOf,
    sameProbe,
    workDone,
} from "../lifecycle";
import { statusClass } from "../pipeline";
import { usePolling } from "../usePolling";

/**
 * Lifecycle (S16): the self-maintenance story, one change at a time. Each button
 * makes one scripted change in a mock source, as its owner would (the only thing
 * the inspector ever changes, and never the knowledge base itself). The story's
 * questions are then shown three times: just before the change, right after it
 * (the sync loop was held, so the worker had not run and the index was stale:
 * a revocation or a delete must already hold here), and now, once the worker
 * caught up. Below each change: what the worker did with it.
 */
export function LifecycleScreen() {
    const view = usePolling(api.lifecycle, 3000);
    const [busy, setBusy] = useState(false);
    const [failure, setFailure] = useState<string | null>(null);
    const data = view.data;

    const apply = (action: LifecycleActionView) => {
        setBusy(true);
        setFailure(null);
        api.applyLifecycle(action.id)
            .catch((reason: unknown) => setFailure(String(reason)))
            .finally(() => setBusy(false));
    };

    return (
        <section className="stack flow">
            <div className="pane compact">
                <h2>Lifecycle · Changes in the sources reach the answers with no manual step</h2>
                <p className="muted">
                    Each button makes one change in a mock source, as its owner would, through
                    the source’s own API. Nothing here writes to the knowledge base: the
                    connectors notice the change, the worker applies it, and the answers
                    follow. “Right after” is asked before the worker runs, while the index is
                    stale: that is where a revocation or a delete must already hold. Changes
                    only a full listing can see make the next round run the listings.
                </p>
                {view.error && <p className="error">{view.error}</p>}
                {data && !data.wired && <p className="note">The sources are not running yet.</p>}
                {failure && <p className="error">{failure}</p>}
                <ol className="actions">
                    {(data?.actions ?? []).map((action, index) => (
                        <li key={action.id} className={`action action-${actionState(action)}`}>
                            <button
                                disabled={busy || !data?.wired || Boolean(action.blocked)}
                                onClick={() => apply(action)}
                                title={action.blocked || action.change}
                            >
                                {index + 1}. {action.title}
                            </button>
                            <span className="small muted">
                                {action.actor} · {action.source}
                                {action.listing ? " · full listing" : ""}
                            </span>
                        </li>
                    ))}
                </ol>
            </div>
            <div className="split even">
                <div className="pane">
                    <h2>Now</h2>
                    <div className="scroll">
                        <Answers rows={data?.now ?? []} />
                        {data?.watched && (
                            <p className="small">
                                <span className="muted">
                                    Links on {pageName(data.watched)} now:{" "}
                                </span>
                                <span className="mono">
                                    {data.links.length
                                        ? data.links.join(", ")
                                        : "none (not published)"}
                                </span>
                            </p>
                        )}
                    </div>
                </div>
                <div className="pane">
                    <h2>Changes applied, newest first</h2>
                    <div className="scroll">
                        {!data?.steps.length && (
                            <p className="empty">No change yet: press the first button.</p>
                        )}
                        {(data?.steps ?? []).map((step) => (
                            <Step
                                key={step.number}
                                step={step}
                                action={data?.actions.find((a) => a.id === step.action_id)}
                                now={data?.now ?? []}
                            />
                        ))}
                    </div>
                </div>
            </div>
        </section>
    );
}

function Answers({ rows }: { rows: LifecycleProbeView[] }) {
    return (
        <table className="grid">
            <thead>
                <tr>
                    <th>Who</th>
                    <th>Question</th>
                    <th>Answer</th>
                    <th>Cites</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((row) => (
                    <tr key={probeKey(row)}>
                        <td className="mono">{row.user_id}</td>
                        <td>{row.question}</td>
                        <td>
                            <span className={answerClass(row)}>{answerLabel(row)}</span>
                        </td>
                        <td className="mono small">{row.pages.map(pageName).join(", ")}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Step({
    step,
    action,
    now,
}: {
    step: LifecycleStepView;
    action: LifecycleActionView | undefined;
    now: LifecycleProbeView[];
}) {
    const work = workDone(step);
    return (
        <div className="attempt">
            <div className="hit-head">
                <strong>
                    {step.number}. {step.title}
                </strong>
                <span className="small muted">
                    {new Date(step.applied_at).toLocaleTimeString()}
                </span>
            </div>
            {action && (
                <>
                    <p className="small">{action.change}</p>
                    <p className="small muted">
                        Caught by {action.caught_by}. Promised: {action.expect}.
                    </p>
                </>
            )}
            <table className="grid">
                <thead>
                    <tr>
                        <th>Who asks</th>
                        <th>Before</th>
                        <th>Right after (worker not run)</th>
                        <th>Now</th>
                    </tr>
                </thead>
                <tbody>
                    {step.before.map((before) => {
                        const after = sameProbe(step.right_after, before);
                        const current = sameProbe(now, before);
                        const moved = change(before, after);
                        return (
                            <tr key={probeKey(before)}>
                                <td>
                                    <span className="mono">{before.user_id}</span>{" "}
                                    <span className="small muted">{before.question}</span>
                                </td>
                                <td>
                                    <span className={answerClass(before)}>
                                        {answerLabel(before)}
                                    </span>
                                </td>
                                <td>
                                    <span className={answerClass(after)}>{answerLabel(after)}</span>
                                    {moved && (
                                        <span className={`moved moved-${moved}`}>{moved}</span>
                                    )}
                                </td>
                                <td>
                                    <span className={answerClass(current)}>
                                        {answerLabel(current)}
                                    </span>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <p className="small muted">The worker since this change:</p>
            {work.length === 0 && <p className="small empty">nothing yet</p>}
            <ul className="work">
                {work.map((trace) => (
                    <li key={trace.seq} className="small">
                        <span className="mono">{trace.document_key}</span>{" "}
                        <span className="muted">{routeOf(trace)}</span>{" "}
                        <span className={statusClass(trace.status)}>{trace.status}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
