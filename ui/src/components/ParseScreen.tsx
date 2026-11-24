import { useState } from "react";
import {
    api,
    type BlockView,
    type ParsedDocumentView,
    type ParsedSummaryView,
} from "../api/client";
import { time } from "../format";
import { KIND_LABEL, kindSummary, listedDetail, sectionLabels } from "../parse";
import { usePolling } from "../usePolling";

type View = "blocks" | "text";

/**
 * T3 Parse (S5): what each document looks like after parsing.
 * Left: every parsed document, with its block counts and how many times T3
 * parsed it. Right: one document's blocks in reading order, each with its
 * kind and the section it sits under, or the same document as clean text.
 */
export function ParseScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [selected, setSelected] = useState<string | null>(null);
    const [view, setView] = useState<View>("blocks");
    const list = usePolling(
        () => api.t3Parsed({ source: source || null }),
        3000,
        [source],
    );
    const detail = usePolling(
        () =>
            selected
                ? api.t3Document({ document_key: selected })
                : Promise.resolve(null),
        3000,
        [selected],
    );
    const documents = list.data?.documents ?? [];
    const records = list.data?.records ?? [];
    const runs = documents.reduce((total, d) => total + d.runs, 0);
    const shown = detail.error || list.error ? null : listedDetail(selected, documents, detail.data);

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>T3 · Parse the original into clean, structured text</h2>
                <p className="muted">
                    T3 runs only where T2 decided “process”. It reads the
                    stored original, takes the document out of the source’s
                    envelope and splits it into blocks: headings, paragraphs,
                    list items, tables and code, each knowing the headings
                    above it. T4 will cut chunks along those sections.
                </p>
                {list.error && <p className="error">{list.error}</p>}
                <div className="counts">
                    <div className="count">
                        <strong className="num">{documents.length}</strong>
                        <span className="muted">documents parsed</span>
                    </div>
                    <div className="count">
                        <strong className="num">{runs}</strong>
                        <span className="muted">T3 runs</span>
                    </div>
                    {records.map((r) => (
                        <div key={r.source} className="count">
                            <strong className="num">{r.items}</strong>
                            <span className="muted">
                                {r.source} records, not parsed (queried, D7)
                            </span>
                        </div>
                    ))}
                </div>
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Parsed documents</strong>
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
                        <ParsedTable
                            rows={documents}
                            selected={selected}
                            onSelect={setSelected}
                        />
                    </div>
                </div>
                <aside className="pane">
                    <div className="toolbar">
                        <strong className="mono">
                            {selected ?? "Parsed document"}
                        </strong>
                        <span className="spacer" />
                        {(["blocks", "text"] as View[]).map((v) => (
                            <button
                                key={v}
                                className={view === v ? "on" : ""}
                                onClick={() => setView(v)}
                            >
                                {v === "blocks" ? "Blocks" : "Clean text"}
                            </button>
                        ))}
                    </div>
                    <div className="scroll">
                        {!selected && (
                            <p className="muted">
                                Click a document to see its blocks.
                            </p>
                        )}
                        {detail.error && (
                            <p className="error">{detail.error}</p>
                        )}
                        {selected && detail.data === null && !detail.error && (
                            <p className="muted">
                                Not parsed: deleted, or not processed yet.
                            </p>
                        )}
                        {shown && <ParsedDocument doc={shown} view={view} />}
                    </div>
                </aside>
            </div>
        </section>
    );
}

function ParsedTable({
    rows,
    selected,
    onSelect,
}: {
    rows: ParsedSummaryView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">Nothing parsed yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>item</th>
                    <th>version</th>
                    <th>title</th>
                    <th>lang</th>
                    <th>blocks</th>
                    <th>runs</th>
                    <th>parsed at</th>
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
                        <td>{d.language ?? "—"}</td>
                        <td title={kindSummary(d.kinds)}>{d.blocks}</td>
                        <td className="num">{d.runs}</td>
                        <td>{time(d.parsed_at)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function ParsedDocument({ doc, view }: { doc: ParsedDocumentView; view: View }) {
    const { summary } = doc;
    const labels = sectionLabels(doc.blocks);
    return (
        <div>
            <h3 className="doc-title">{summary.title}</h3>
            <p className="muted">
                v{summary.version} · {summary.media_type} ·{" "}
                {summary.language ?? "no language"} · parsed by{" "}
                <span className="mono">{summary.parser_id}</span> ·{" "}
                {kindSummary(summary.kinds)}
            </p>
            {view === "text" ? (
                <pre className="clean-text">{doc.text}</pre>
            ) : (
                <div className="blocks">
                    {doc.blocks.map((block, i) => (
                        <BlockRow key={i} block={block} label={labels[i] ?? null} />
                    ))}
                </div>
            )}
        </div>
    );
}

function BlockRow({ block, label }: { block: BlockView; label: string | null }) {
    return (
        <>
            {label && <div className="section-label">§ {label}</div>}
            <div className={`blk blk-${block.kind}`}>
                <span className="kind">
                    {block.kind === "heading"
                        ? `h${block.level}`
                        : KIND_LABEL[block.kind]}
                </span>
                <BlockBody block={block} />
            </div>
        </>
    );
}

function BlockBody({ block }: { block: BlockView }) {
    switch (block.kind) {
        case "heading":
            return <strong className={`h h${block.level}`}>{block.text}</strong>;
        case "list_item":
            return (
                <span style={{ paddingLeft: (block.level - 1) * 18 }}>
                    <span className="marker">{block.marker}</span> {block.text}
                </span>
            );
        case "table":
            return (
                <table className="grid cells">
                    <thead>
                        <tr>
                            {block.rows[0]?.map((cell, i) => (
                                <th key={i}>{cell}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {block.rows.slice(1).map((row, r) => (
                            <tr key={r}>
                                {row.map((cell, i) => (
                                    <td key={i}>{cell}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            );
        case "code":
            return <pre className="code">{block.text}</pre>;
        default:
            return <span>{block.text}</span>;
    }
}
