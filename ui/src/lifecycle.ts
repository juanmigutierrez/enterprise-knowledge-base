import type {
    LifecycleActionView,
    LifecycleProbeView,
    LifecycleStepView,
    LifecycleTraceView,
} from "./api/client";

/** `HR-PL-11` from `hr_docs:HR-PL-11`. */
export function pageName(key: string): string {
    const at = key.indexOf(":");
    return at < 0 ? key : key.slice(at + 1);
}

/** One row per probe: the same person and question across the three moments. */
export function probeKey(probe: Pick<LifecycleProbeView, "user_id" | "question">): string {
    return `${probe.user_id}|${probe.question}`;
}

/** What one probe's answer says about the page it depends on, in a few words. */
export function answerLabel(probe: LifecycleProbeView | undefined): string {
    if (!probe) return "—";
    if (probe.reads_nothing) return "reads nothing";
    if (probe.cited) return `cites ${pageName(probe.page)}`;
    const held = probe.held_back.find((h) => h.document_key === probe.page);
    if (held) return `held back (${held.verdict})`;
    return "not cited";
}

/** The cell's class: cited, held back by Q4, or simply not found. */
export function answerClass(probe: LifecycleProbeView | undefined): string {
    if (!probe) return "life";
    if (probe.cited) return "life life-cited";
    if (probe.held_back.some((h) => h.document_key === probe.page)) return "life life-held";
    return "life life-absent";
}

/** `gained`, `lost` or `` between two moments: did the page appear or go? */
export function change(
    before: LifecycleProbeView | undefined,
    after: LifecycleProbeView | undefined,
): "gained" | "lost" | "" {
    if (!before || !after || before.cited === after.cited) return "";
    return after.cited ? "gained" : "lost";
}

/** `T2 acl_only → T8 fast lane`, `re-link → T8 readers first → T3 → … → T8 publish`. */
export function routeOf(trace: Pick<LifecycleTraceView, "action" | "steps">): string {
    return trace.steps
        .map((step) => {
            if (step === "T2 diff") return `T2 ${trace.action ?? "?"}`;
            const short = /^(T[3-7]) /.exec(step);
            return short ? short[1] : step;
        })
        .join(" → ");
}

/** What the worker did after a change, without the repeats T2 skipped. */
export function workDone(step: Pick<LifecycleStepView, "traces">): LifecycleTraceView[] {
    return step.traces.filter((trace) => trace.status !== "skipped");
}

/** `applied`, `ready` or `waiting`: where an action stands in the story. */
export function actionState(action: Pick<LifecycleActionView, "applied" | "blocked">): string {
    if (action.applied) return "applied";
    return action.blocked ? "waiting" : "ready";
}

/** The probe of `rows` asked by the same person with the same question as `probe`. */
export function sameProbe(
    rows: LifecycleProbeView[],
    probe: Pick<LifecycleProbeView, "user_id" | "question">,
): LifecycleProbeView | undefined {
    const key = probeKey(probe);
    return rows.find((row) => probeKey(row) === key);
}
