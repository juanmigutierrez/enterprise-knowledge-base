import {
    api,
    type CallTotalsView,
    type CallView,
    type GatewayProviderView,
    type GatewayQuotaView,
    type GatewayRouteView,
} from "../api/client";
import { time } from "../format";
import { dayLabel, dayShare, latestFirst, money, OUTCOME_LABEL, splitRef } from "../gateway";
import { usePolling } from "../usePolling";

/**
 * Model gateway (S7): every model call, from the gateway's cost log.
 * Top: requests, cache hits, failed attempts and spend. Left: the calls,
 * newest first: which task, which model answered (or failed), tokens, cost,
 * latency. Right: which providers have a key, where each free quota stands,
 * the route of every task, and totals per task and model. The log holds no
 * prompt and no reply, so neither does this screen.
 */
export function GatewayScreen() {
    const overview = usePolling(api.gateway, 3000);
    const calls = usePolling(() => api.gatewayCalls(), 3000);
    const view = overview.data;
    const totals = calls.data?.totals ?? [];
    const sum = (pick: (row: CallTotalsView) => number) =>
        totals.reduce((total, row) => total + pick(row), 0);
    const ready = new Set(
        (view?.providers ?? []).filter((p) => p.ready).map((p) => p.provider),
    );

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>Model gateway · every model call routed, reused, guarded and costed</h2>
                <p className="muted">
                    A step names a task; the gateway tries that task's models in
                    order, skipping any without a key, past its free quota or
                    recently rate-limited, and answers from the cache when exactly
                    the same call was made before. Every call is logged with its
                    tokens and cost, never its prompt or reply. Calls come from{" "}
                    <span className="mono">ekb demo gateway</span> and, from S8,
                    from the pipeline.
                </p>
                {overview.error && <p className="error">{overview.error}</p>}
                {calls.error && <p className="error">{calls.error}</p>}
                <div className="counts">
                    <Count value={sum((r) => r.requests)} label="requests sent" />
                    <Count value={sum((r) => r.hits)} label="cache hits" />
                    <Count value={sum((r) => r.failures)} label="failed attempts" />
                    <Count
                        value={`${money(view ? view.spent_usd : 0)} of $${(view?.max_spend_usd ?? 0).toFixed(2)}`}
                        label="spent on paid models"
                    />
                    {view && (
                        <Count
                            value={`${view.cache} · ${view.cost_log}`}
                            label="cache · cost log"
                            mono
                        />
                    )}
                </div>
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Calls</strong>
                        <span className="muted">newest first</span>
                    </div>
                    <div className="scroll">
                        <CallTable calls={latestFirst(calls.data?.calls ?? [])} />
                    </div>
                </div>
                <aside className="pane">
                    <div className="scroll">
                        <h3>Who can answer</h3>
                        <Providers providers={view?.providers ?? []} />
                        <h3>Free quotas</h3>
                        <Quotas quotas={view?.quotas ?? []} />
                        <h3>Routes</h3>
                        <Routes routes={view?.routes ?? []} ready={ready} />
                        <h3>Totals per task and model</h3>
                        <Totals rows={totals} />
                    </div>
                </aside>
            </div>
        </section>
    );
}

function Count({
    value,
    label,
    mono = false,
}: {
    value: number | string;
    label: string;
    mono?: boolean;
}) {
    return (
        <div className="count">
            <strong className={mono ? "mono" : "num"}>{value}</strong>
            <span className="muted">{label}</span>
        </div>
    );
}

function CallTable({ calls }: { calls: CallView[] }) {
    if (!calls.length)
        return (
            <p className="empty">
                No calls yet. Run <span className="mono">ekb demo gateway</span>.
            </p>
        );
    return (
        <table className="grid">
            <thead>
                <tr>
                    <th>#</th>
                    <th>at</th>
                    <th>task</th>
                    <th>model</th>
                    <th>outcome</th>
                    <th>tokens in/out</th>
                    <th>cost</th>
                    <th>ms</th>
                    <th>document</th>
                </tr>
            </thead>
            <tbody>
                {calls.map((c) => (
                    <tr key={c.seq} className={c.outcome === "hit" ? "skipped" : ""}>
                        <td className="num">{c.seq}</td>
                        <td>{time(c.at)}</td>
                        <td className="mono">{c.task}</td>
                        <td className="mono" title={`prompt ${c.prompt_version} · key ${c.key}`}>
                            {c.candidate}
                        </td>
                        <td title={c.error ?? ""}>
                            <span className={`out out-${c.outcome}`}>
                                {OUTCOME_LABEL[c.outcome]}
                            </span>
                        </td>
                        <td className="num">
                            {c.input_tokens}/{c.output_tokens}
                        </td>
                        <td className="num">{money(c.cost_usd)}</td>
                        <td className="num">{c.outcome === "hit" ? "—" : c.latency_ms}</td>
                        <td className="mono">{c.document_key ?? "—"}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Providers({ providers }: { providers: GatewayProviderView[] }) {
    if (!providers.length) return <p className="empty">No task routes to a model.</p>;
    return (
        <table className="grid">
            <tbody>
                {providers.map((p) => (
                    <tr key={p.provider}>
                        <td className="mono">{p.provider}</td>
                        <td className="mono">{p.key_env ?? "no key needed"}</td>
                        <td className={p.ready ? "" : "error"}>
                            {p.ready ? "ready" : "key not set: skipped"}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Quotas({ quotas }: { quotas: GatewayQuotaView[] }) {
    if (!quotas.length) return <p className="empty">No free-tier quotas.</p>;
    return (
        <table className="grid">
            <tbody>
                {quotas.map((q) => (
                    <tr key={q.name}>
                        <td className="mono">{q.name}</td>
                        <td>
                            <div className={`bar${dayShare(q) >= 100 ? " over" : ""}`}>
                                <span style={{ width: `${dayShare(q)}%` }} />
                            </div>
                        </td>
                        <td>{dayLabel(q)}</td>
                        <td className="muted">
                            {q.per_minute === null
                                ? "no per-minute cap"
                                : `${q.used_last_minute}/${q.per_minute} this minute`}
                        </td>
                        <td className="muted" title={`midnight ${q.resets}`}>
                            resets {time(q.resets_at)}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function Routes({ routes, ready }: { routes: GatewayRouteView[]; ready: Set<string> }) {
    if (!routes.length) return <p className="empty">No routes configured.</p>;
    return (
        <div className="routes">
            {routes.map((route) => (
                <div key={route.task} className="route">
                    <span className="mono">{route.task}</span>
                    <div className="chain">
                        {route.candidates.map((ref, i) => (
                            <span
                                key={ref}
                                className={`cand${ready.has(splitRef(ref)[0]) ? "" : " off"}`}
                                title={ready.has(splitRef(ref)[0]) ? "ready" : "no key: skipped"}
                            >
                                {i + 1}. {ref}
                            </span>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}

function Totals({ rows }: { rows: CallTotalsView[] }) {
    if (!rows.length) return <p className="empty">Nothing logged yet.</p>;
    return (
        <table className="grid">
            <thead>
                <tr>
                    <th>task</th>
                    <th>model</th>
                    <th>requests</th>
                    <th>hits</th>
                    <th>failed</th>
                    <th>tokens</th>
                    <th>cost</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((r) => (
                    <tr key={`${r.task} ${r.candidate}`}>
                        <td className="mono">{r.task}</td>
                        <td className="mono">{r.candidate}</td>
                        <td className="num">{r.requests}</td>
                        <td className="num">{r.hits}</td>
                        <td className="num">{r.failures}</td>
                        <td className="num">
                            {r.input_tokens}/{r.output_tokens}
                        </td>
                        <td className="num">{money(r.cost_usd)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
