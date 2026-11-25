import { useState } from "react";
import {
    api,
    type ChunkedDocumentView,
    type ChunkSummaryView,
    type ChunkView,
} from "../api/client";
import {
    chunkCount,
    listedChunks,
    piiLabel,
    sensitivityClass,
    sensitivityCounts,
    sizeRange,
    sizeShare,
} from "../chunk";
import { time } from "../format";
import { trail } from "../parse";
import { usePolling } from "../usePolling";

/**
 * T4 Chunk (S6) and its labels (S8): the passages each document was cut into.
 * Left: every chunked document, with its number of chunks, their sizes, its
 * chunks per sensitivity and how many times T4 chunked it. Right: one
 * document's chunks in order, each with its size against the chunker's limit,
 * the section it belongs to, its context header (T4), its labels and why (T5)
 * and its text. None is publishable yet: that is T8's gate.
 */
export function ChunkScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [selected, setSelected] = useState<string | null>(null);
    const list = usePolling(
        () => api.t4Chunks({ source: source || null }),
        3000,
        [source],
    );
    const detail = usePolling(
        () =>
            selected
                ? api.t4Document({ document_key: selected })
                : Promise.resolve(null),
        3000,
        [selected],
    );
    const documents = list.data?.documents ?? [];
    const runs = documents.reduce((total, d) => total + d.runs, 0);
    const chunkers = [...new Set(documents.map((d) => d.chunker_id))];
    const shown =
        detail.error || list.error ? null : listedChunks(selected, documents, detail.data);

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>T4 · Chunk the parsed text into searchable passages</h2>
                <p className="muted">
                    T4 runs right after T3, so only on new or changed text. It
                    cuts each document along its sections. Long sections can
                    span several chunks, each carrying the same heading trail.
                    Tables and individual list items stay whole.
                    A model then writes each chunk a context header, one call
                    per document through the gateway, and T5 labels every chunk
                    with a department, a sensitivity and the personal data it
                    holds. T7 and T8 embed and secure them later; until then no
                    chunk is publishable.
                </p>
                {list.error && <p className="error">{list.error}</p>}
                <div className="counts">
                    <div className="count">
                        <strong className="num">{documents.length}</strong>
                        <span className="muted">documents chunked</span>
                    </div>
                    <div className="count">
                        <strong className="num">{list.data?.chunks ?? 0}</strong>
                        <span className="muted">chunks</span>
                    </div>
                    <div className="count">
                        <strong className="num">{runs}</strong>
                        <span className="muted">T4 runs</span>
                    </div>
                    {chunkers.map((id) => (
                        <div key={id} className="count">
                            <strong className="mono">{id}</strong>
                            <span className="muted">chunker</span>
                        </div>
                    ))}
                </div>
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Chunked documents</strong>
                        <select
                            value={source}
                            onChange={(e) => {
                                setSource(e.target.value);
                                setSelected(null);
                            }}
                        >
                            <option value="">All sources</option>
                            {sources.map((s) => (
                                <option key={s}>{s}</option>
                            ))}
                        </select>
                    </div>
                    <div className="scroll">
                        <ChunkTable
                            rows={documents}
                            selected={selected}
                            onSelect={setSelected}
                        />
                    </div>
                </div>
                <aside className="pane">
                    <div className="toolbar">
                        <strong className="mono">{selected ?? "Chunks"}</strong>
                    </div>
                    <div className="scroll">
                        {!selected && (
                            <p className="muted">
                                Click a document to see its chunks.
                            </p>
                        )}
                        {detail.error && <p className="error">{detail.error}</p>}
                        {selected && detail.data === null && !detail.error && (
                            <p className="muted">
                                No chunks: deleted, or not processed yet.
                            </p>
                        )}
                        {shown && <ChunkedDocument doc={shown} />}
                    </div>
                </aside>
            </div>
        </section>
    );
}

function ChunkTable({
    rows,
    selected,
    onSelect,
}: {
    rows: ChunkSummaryView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">Nothing chunked yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>item</th>
                    <th>version</th>
                    <th>title</th>
                    <th>chunks</th>
                    <th>sizes</th>
                    <th>sensitivity</th>
                    <th>runs</th>
                    <th>chunked at</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((d) => (
                    <tr
                        key={d.document_key}
                        className={d.document_key === selected ? "on" : ""}
                        onClick={() => onSelect(d.document_key)}
                    >
                        <td className="mono">{d.document_key}</td>
                        <td className="mono">{d.version}</td>
                        <td>{d.title}</td>
                        <td className="num">{d.chunks}</td>
                        <td title={`${d.tokens} tokens in all`}>
                            {sizeRange(d)}
                            {d.over_limit > 0 && ` (${d.over_limit} over)`}
                        </td>
                        <td>{sensitivityCounts(d.sensitivity)}</td>
                        <td className="num">{d.runs}</td>
                        <td>{time(d.chunked_at)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function ChunkedDocument({ doc }: { doc: ChunkedDocumentView }) {
    const { summary } = doc;
    return (
        <div>
            <h3 className="doc-title">{summary.title}</h3>
            <p className="muted">
                v{summary.version} · {chunkCount(summary.chunks)},{" "}
                {sizeRange(summary)} · limit {summary.max_tokens} · cut by{" "}
                <span className="mono">{summary.chunker_id}</span>
            </p>
            {summary.header_note && (
                <p className="muted">Headers: {summary.header_note}</p>
            )}
            {summary.classifier_note && (
                <p className="muted">Labels: {summary.classifier_note}</p>
            )}
            <div className="chunks">
                {doc.chunks.map((chunk) => (
                    <ChunkCard
                        key={chunk.chunk_id}
                        chunk={chunk}
                        maxTokens={summary.max_tokens}
                    />
                ))}
            </div>
        </div>
    );
}

function ChunkCard({ chunk, maxTokens }: { chunk: ChunkView; maxTokens: number }) {
    const over = chunk.tokens > maxTokens;
    return (
        <div className="chunk">
            <div className="chunk-head">
                <span className="mono">#{chunk.position}</span>
                <span className={over ? "bar over" : "bar"} title={`${chunk.tokens} of ${maxTokens}`}>
                    <span style={{ width: `${sizeShare(chunk.tokens, maxTokens)}%` }} />
                </span>
                <span>
                    {chunk.tokens} tokens{over && " — over the limit, kept whole"}
                </span>
                <span className="spacer" />
                <span className={sensitivityClass(chunk.sensitivity)} title={chunk.why}>
                    {chunk.sensitivity ?? "not labelled"}
                </span>
                {chunk.department && <span className="gate">{chunk.department}</span>}
                {chunk.pii.length > 0 && <span className="gate pii">{piiLabel(chunk.pii)}</span>}
                <span
                    className="gate"
                    title="Published only once T5, T7 and T8 have set sensitivity, vector and ACL"
                >
                    {chunk.publishable ? "publishable" : "not publishable yet"}
                </span>
            </div>
            <div className="section-label">§ {trail(chunk.section)}</div>
            {chunk.context_header ? (
                <p className="header-line" title={chunk.header_id}>
                    {chunk.context_header}
                </p>
            ) : (
                chunk.header_id && <p className="muted">no header</p>
            )}
            {chunk.why && <p className="why">{chunk.why}</p>}
            <pre className="clean-text">{chunk.text}</pre>
        </div>
    );
}
