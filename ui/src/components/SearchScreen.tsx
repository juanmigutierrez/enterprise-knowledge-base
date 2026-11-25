import { useState } from "react";
import { api, type SearchHitView, type SearchView } from "../api/client";
import { byActive, readersLabel, shortKey } from "../permissions";
import {
    SUGGESTED,
    fused,
    legClass,
    legsLabel,
    otherCanRead,
    searchFor,
    searchedLabel,
    snippet,
    trail,
    withheldLabel,
} from "../search";
import { usePolling } from "../usePolling";

/** A user ID the identity provider doesn't know: it reads nothing. */
const UNKNOWN = "mallory";

/**
 * Search as user (S13): the same question asked by two people, side by side.
 * Each column is the real query path, Q2 then Q3: the person's principals now,
 * then the chunks those admit, ranked by keywords (BM25) and by vectors, fused.
 * Each column also shows what the pre-filter kept out: an inspector's view
 * that no agent ever gets. Both columns ask again every few seconds, so a group
 * change or a revoke made in the mocks shows up without asking again.
 */
export function SearchScreen() {
    const people = usePolling(api.t8Permissions, 10000);
    const users = byActive(people.data?.users ?? []);
    const [draft, setDraft] = useState<string>(SUGGESTED[0]);
    const [question, setQuestion] = useState<string>(SUGGESTED[0]);
    const [left, setLeft] = useState("alice");
    const [right, setRight] = useState("bob");
    const a = usePolling(() => api.search({ user: left, q: question }), 5000, [left, question]);
    const b = usePolling(() => api.search({ user: right, q: question }), 5000, [right, question]);
    const leftView = searchFor(a.data, left, question);
    const rightView = searchFor(b.data, right, question);
    const ask = (text: string) => {
        setDraft(text);
        setQuestion(text);
    };

    return (
        <section className="stack flow">
            <div className="pane compact">
                <h2>Q2 + Q3 · The same question, asked by two people</h2>
                <p className="muted">
                    Q2 turns the person asking into the principals they read with now:
                    their user ID and the groups the identity provider lists today (none
                    for a leaver or a stranger). Q3 ranks only the chunks those admit, by
                    keywords (BM25) and by meaning (vectors), and fuses the two lists by
                    rank (RRF). A chunk someone may not read is never scored, so it can’t
                    take one of their places. The grey box is the inspector’s view only.
                </p>
                <form
                    className="ask"
                    onSubmit={(event) => {
                        event.preventDefault();
                        setQuestion(draft);
                    }}
                >
                    <input
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        placeholder="Ask a question"
                        aria-label="Question"
                    />
                    <button type="submit">Ask</button>
                </form>
                <div className="suggest">
                    {SUGGESTED.map((text) => (
                        <button
                            key={text}
                            className={text === question ? "on" : ""}
                            onClick={() => ask(text)}
                        >
                            {text}
                        </button>
                    ))}
                </div>
                {people.error && <p className="error">{people.error}</p>}
            </div>
            <div className="split even">
                <SearchColumn
                    user={left}
                    onUser={setLeft}
                    users={users.map((u) => [u.user_id, u.active] as const)}
                    view={leftView}
                    error={a.error}
                    other={rightView}
                />
                <SearchColumn
                    user={right}
                    onUser={setRight}
                    users={users.map((u) => [u.user_id, u.active] as const)}
                    view={rightView}
                    error={b.error}
                    other={leftView}
                />
            </div>
        </section>
    );
}

function SearchColumn({
    user,
    onUser,
    users,
    view,
    error,
    other,
}: {
    user: string;
    onUser: (user: string) => void;
    users: (readonly [string, boolean])[];
    view: SearchView | null;
    error: string | null;
    other: SearchView | null;
}) {
    return (
        <div className="pane">
            <div className="toolbar">
                <label>
                    asked by{" "}
                    <select value={user} onChange={(event) => onUser(event.target.value)}>
                        {users.map(([id, active]) => (
                            <option key={id} value={id}>
                                {active ? id : `${id} (has left)`}
                            </option>
                        ))}
                        <option value={UNKNOWN}>{UNKNOWN} (unknown)</option>
                    </select>
                </label>
                <span className="spacer" />
                {view && <span className="muted">{searchedLabel(view)}</span>}
            </div>
            <div className="scroll">
                {error && <p className="error">{error}</p>}
                {view && (
                    <>
                        <p className={view.reads_nothing ? "error" : "muted"}>{view.reason}</p>
                        {view.notes.map((note) => (
                            <p key={note} className="note">
                                {note}
                            </p>
                        ))}
                        <div className="withheld">
                            <strong>Pre-filter: {withheldLabel(view)}</strong>
                            {view.withheld.map((page) => (
                                <div key={page.document_key} className="mono">
                                    {shortKey(page.document_key)} · {page.chunks} · readers{" "}
                                    {readersLabel(page.readers)}
                                </div>
                            ))}
                            <span className="muted">
                                Chunks sharing a word with the question that {view.user_id} may
                                not read. Inspector only: {view.user_id} never learns they exist.
                            </span>
                        </div>
                        {!view.hits.length && <p className="empty">No hits.</p>}
                        <ol className="hits">
                            {view.hits.map((hit) => (
                                <HitCard
                                    key={hit.chunk_id}
                                    hit={hit}
                                    hidden={
                                        other && otherCanRead(hit.readers, other) === false
                                            ? other.user_id
                                            : null
                                    }
                                />
                            ))}
                        </ol>
                        <p className="muted small">
                            {view.prefilter_id} · {view.retriever_id} · {view.encoder} ·{" "}
                            {view.ms.toFixed(1)} ms
                        </p>
                    </>
                )}
            </div>
        </div>
    );
}

/** One hit; `hidden` names the other column's person when they may not read it. */
function HitCard({ hit, hidden }: { hit: SearchHitView; hidden: string | null }) {
    return (
        <li className="hit">
            <div className="hit-head">
                <strong className="num">{hit.rank}.</strong>
                <span className="mono" title={hit.title}>
                    {shortKey(hit.chunk_id)}
                </span>
                <span className={legClass(hit)}>{legsLabel(hit)}</span>
                {hidden && <span className="only">{hidden} can’t read this</span>}
                <span className="spacer" />
                <span className="muted mono">rrf {fused(hit.score)}</span>
            </div>
            <div className="section-label">{trail(hit.section)}</div>
            <p className="hit-text">{snippet(hit.text)}</p>
            <div className="muted small">
                readers {readersLabel(hit.readers)}
                {hit.sensitivity ? ` · ${hit.sensitivity}` : ""}
                {hit.keyword_score !== null ? ` · bm25 ${hit.keyword_score.toFixed(2)}` : ""}
                {hit.vector_score !== null ? ` · cosine ${hit.vector_score.toFixed(3)}` : ""}
            </div>
        </li>
    );
}
