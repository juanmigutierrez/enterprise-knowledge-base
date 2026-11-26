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

const lessons: Record<string, { question: string; expected: string }> = {
    add_page: {
        question: "When can Alice first cite a new policy?",
        expected: "Compare Alice’s mentoring answer before and after publication. Once the worker publishes the page with its readers, her answer cites HR-PL-11. The page links to Lucía and the People team.",
    },
    edit_page: {
        question: "Does an answer change when its source is edited?",
        expected: "Alice keeps access to HR-PL-11. After the worker runs, the policy says three meetings a month instead of two. The citation can stay the same. Look for the worker processing a new version of the same page.",
    },
    revoke_page: {
        question: "What happens if the page owner withdraws Alice’s access?",
        expected: "In Right after, Alice has already lost HR-PL-11, even though the worker has not updated the index. Lucía still has access through the HR team. The worker updates the readers without rebuilding the vectors.",
    },
    remove_from_group: {
        question: "Can someone lose access even when the page itself stays the same?",
        expected: "Compare Lucía and Bob. Lucía loses the mentoring citation as soon as she leaves the HR group. Bob keeps it. The page’s chunks need no edits.",
    },
    hire_record: {
        question: "Is a new HR record enough to link a name to a person?",
        expected: "Check the links on HR-PL-11 under Now. Sara is mentioned on the page, but her HR record has no matching login yet. The links stay the same.",
    },
    hire_account: {
        question: "Will an existing page recognize Sara once her account exists?",
        expected: "After the identity sync and full HR listing, person:sara appears in the links on HR-PL-11. Look for a re-link in the worker history. Nobody edited the mentoring policy.",
    },
    leaver: {
        question: "How does the system discover that someone has left?",
        expected: "The next full HR listing detects Lucía’s missing record. Watch person:lucia disappear from the mentoring page’s links while the page itself remains available to its readers.",
    },
    delete_page: {
        question: "Can an old answer still cite a policy after its owner deletes it?",
        expected: "In Alice’s hotel answer, HR-PL-99 disappears in Right after. Once the worker purges the old policy and its cached answers, the current travel policy HR-PL-06 can still be cited.",
    },
};

/**
 * Lifecycle (S16): the self-maintenance story, one change at a time. Each button
 * makes one scripted change in a mock source, as its owner would (the only thing
 * the inspector ever changes, and never the knowledge base itself). The story's
 * questions are then shown three times: just before the change, right after it
 * (the sync loop was held, so the worker had not run and the index was stale:
 * a revocation or a delete must already hold here), and now, once the worker
 * caught up. Below each change: what the worker did with it.
 */
export function LifecycleScreen({ initialStep, onDiagram }: {
    initialStep: string; onDiagram: (id: string) => void;
}) {
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
                <h2>Try the changes in order</h2>
                <p className="muted">
                    Opening a step only shows its instructions. Choose <strong>Apply change</strong> when
                    you are ready. In the results below, <strong>Before</strong> is the original
                    answer, <strong>Right after</strong> captures the moment before the worker
                    runs, and <strong>Now</strong> follows the current state as you continue.
                </p>
                {view.error && <p className="error">{view.error}</p>}
                {data && !data.wired && <p className="note">The sources are not running yet.</p>}
                {failure && <p className="error">{failure}</p>}
                <ol className="actions">
                    {(data?.actions ?? []).map((action, index) => {
                        const lesson = lessons[action.id];
                        return (
                            <li key={action.id}>
                                <details open={action.id === initialStep || undefined}>
                                    <summary>
                                        <strong>{index + 1}. {action.title}</strong>
                                        <span className="small muted">{actionState(action)}</span>
                                    </summary>
                                    <div className="lesson">
                                        <dl>
                                            <dt>What you’re testing</dt>
                                            <dd>{lesson?.question ?? action.title}</dd>
                                            <dt>What you’ll change</dt>
                                            <dd>{action.change}</dd>
                                            <dt>What to expect</dt>
                                            <dd>{lesson?.expected ?? action.expect}</dd>
                                        </dl>
                                        {action.blocked && !action.applied && (
                                            <p className="small muted">{action.blocked}</p>
                                        )}
                                        <button
                                            disabled={busy || !data?.wired || action.applied || Boolean(action.blocked)}
                                            onClick={() => apply(action)}
                                            aria-label={`Apply change: ${action.title}`}
                                        >
                                            {action.applied ? "Applied" : "Apply change"}
                                        </button>
                                        <button className="diagram-link" onClick={() => onDiagram(action.id)}>
                                            View architecture
                                        </button>
                                    </div>
                                </details>
                            </li>
                        );
                    })}
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
                            <p className="empty">Open “1. Add a page” and apply the change to see the first comparison.</p>
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
                        Detected by {action.caught_by}.
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
