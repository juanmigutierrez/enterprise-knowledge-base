import type { DiffAction, DiffCounts } from "./api/client";

/** The four T2 actions in pipeline order, with what each one sends onwards. */
export const ACTIONS: { action: DiffAction; next: string }[] = [
    { action: "process", next: "T3 parse → … → T8 permissions" },
    { action: "acl_only", next: "T8 permissions only (fast lane)" },
    { action: "skip", next: "nothing: already processed" },
    { action: "purge", next: "removed from every store" },
];

/**
 * Runs of T3–T7 (parse, chunk, classify, entities, embed) that T2 saved:
 * every non-delete event would run them without T2; only `process` does with it.
 */
export function avoidedRuns(counts: DiffCounts): number {
    return counts.acl_only + counts.skip;
}

export function totalDecisions(counts: DiffCounts): number {
    return counts.process + counts.acl_only + counts.skip + counts.purge;
}
