import type { ChunkedDocumentView, ChunkSummaryView, ChunkView } from "./api/client";

/** The list and detail poll independently. Only display the listed chunking. */
export function listedChunks(
    selected: string | null,
    documents: ChunkSummaryView[],
    detail: ChunkedDocumentView | null,
) {
    if (!detail || detail.summary.document_key !== selected) return null;
    const listed = documents.find((doc) => doc.document_key === selected);
    if (
        listed?.version !== detail.summary.version ||
        listed.chunked_at !== detail.summary.chunked_at
    )
        return null;
    return detail;
}

/** `38–59 tokens`, or `7 tokens` when every chunk is the same size. */
export function sizeRange(doc: Pick<ChunkSummaryView, "smallest" | "largest">): string {
    return doc.smallest === doc.largest
        ? `${doc.smallest} tokens`
        : `${doc.smallest}–${doc.largest} tokens`;
}

/**
 * How full a chunk is, as a share of the chunker's limit (0–100), for the
 * size bar. A chunk over the limit (a table too big to split) shows full and
 * is flagged separately.
 */
export function sizeShare(tokens: number, maxTokens: number): number {
    if (maxTokens <= 0) return 100;
    return Math.min(100, Math.max(0, Math.round((tokens / maxTokens) * 100)));
}

/** `4 chunks`, `1 chunk`. */
export function chunkCount(n: number): string {
    return `${n} chunk${n === 1 ? "" : "s"}`;
}

/** T5's levels, least to most strict (S8). */
export const SENSITIVITIES = ["public", "internal", "confidential", "restricted"] as const;

/** `3 internal, 1 confidential`: chunks per sensitivity, least strict first; `—` if none. */
export function sensitivityCounts(counts: Record<string, number>): string {
    const known = SENSITIVITIES.filter((level) => counts[level]).map(
        (level) => `${counts[level]} ${level}`,
    );
    const other = Object.keys(counts)
        .filter((level) => !(SENSITIVITIES as readonly string[]).includes(level) && counts[level])
        .map((level) => `${counts[level]} ${level}`);
    return [...known, ...other].join(", ") || "—";
}

/** The badge's class: one colour per level, a neutral one before T5 ran. */
export function sensitivityClass(level: string | null | undefined): string {
    return level ? `sens sens-${level}` : "sens";
}

/** `PII: email, phone`, or `""` when the chunk holds none. */
export function piiLabel(kinds: readonly string[]): string {
    return kinds.length ? `PII: ${kinds.join(", ")}` : "";
}

/**
 * What T7 made of a chunk (S10): `1024 numbers`, `no vector yet`, or a warning
 * that another model made it (a stale vector must be embedded again before search).
 */
export function vectorLabel(
    chunk: Pick<ChunkView, "dimensions" | "vector_state">,
): string {
    if (chunk.vector_state === "none" || chunk.dimensions == null) return "no vector yet";
    const size = `${chunk.dimensions} numbers`;
    return chunk.vector_state === "stale" ? `${size}, stale: embed again` : size;
}

/** The vector's first numbers, then how many are left: `[0.0123, -0.0456, … 1022 more]`. */
export function vectorPreview(values: readonly number[], dimensions: number | null): string {
    if (!values.length || dimensions == null) return "";
    const shown = values.map((v) => v.toFixed(4)).join(", ");
    const left = dimensions - values.length;
    return left > 0 ? `[${shown}, … ${left} more]` : `[${shown}]`;
}

/** The vector badge's class: neutral before T7, a warning colour when stale. */
export function vectorClass(state: string): string {
    return state === "stale" ? "gate vec-stale" : state === "current" ? "gate vec" : "gate";
}
