import type { TraceStatus, TraceStepView, TraceView } from "./api/client";

/** Every status a worker attempt can end in, in the order the counters show them. */
export const STATUSES: { status: TraceStatus; meaning: string }[] = [
    { status: "published", meaning: "a new version is searchable, with its readers" },
    { status: "readers", meaning: "only the readers changed (fast lane)" },
    { status: "record", meaning: "a record: entity or permission store" },
    { status: "skipped", meaning: "nothing new" },
    { status: "purged", meaning: "deleted from every store" },
    { status: "failed", meaning: "a step raised; the event comes back" },
    { status: "parked", meaning: "set aside in the dead letters" },
    { status: "rebuilt", meaning: "republished from its stored original" },
];

/** The badge's class: one colour per status. */
export function statusClass(status: TraceStatus): string {
    return `trace trace-${status}`;
}

/** A step's row class: done or failed, and which part of the line it belongs to. */
export function stepClass(step: Pick<TraceStepView, "step" | "outcome">): string {
    return `step step-${step.outcome} step-${stepKind(step.step)}`;
}

/** `t2` … `t8` for the transformations, `store`, `purge` or `dead` for the rest. */
export function stepKind(name: string): string {
    const match = /^T(\d)/.exec(name);
    if (match) return `t${match[1]}`;
    if (name.startsWith("purge")) return "purge";
    if (name === "dead letters") return "dead";
    return "store";
}

/** `p0@15`, or `rebuild` for an attempt no event delivered. */
export function where(trace: Pick<TraceView, "partition" | "offset">): string {
    if (trace.partition === null || trace.offset === null) return "rebuild";
    return `p${trace.partition}@${trace.offset}`;
}

/** `0.42 ms`, `12 ms`: precise when small, rounded when not. */
export function ms(value: number): string {
    return value < 10 ? `${value.toFixed(2)} ms` : `${Math.round(value)} ms`;
}

/** `T2 process → T8 readers first → T3 → … → T8 publish`: the route an attempt took. */
export function route(trace: Pick<TraceView, "action" | "steps">): string {
    return trace.steps
        .map((s) => {
            if (s.step === "T2 diff") return `T2 ${trace.action ?? "?"}`;
            const short = /^(T[3-7]) /.exec(s.step);
            return short ? short[1] : s.step;
        })
        .join(" → ");
}

/** `HR-PL-05` from `hr_docs:HR-PL-05`, `users/alice` from `identity:users/alice`. */
export function sourceId(documentKey: string): string {
    const at = documentKey.indexOf(":");
    return at < 0 ? documentKey : documentKey.slice(at + 1);
}
