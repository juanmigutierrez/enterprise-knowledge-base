import type { ChunkedDocumentView, ChunkSummaryView } from "./api/client";

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
