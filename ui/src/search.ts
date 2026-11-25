import type { AclView, SearchHitView, SearchView } from "./api/client";

/** Polling keeps its last response while the next selection is loading. */
export function searchFor(view: SearchView | null, user: string, question: string): SearchView | null {
    return view?.user_id === user && view.question === question ? view : null;
}

/** The questions the screen suggests: one for the salary bands, one for everyone, one exact term. */
export const SUGGESTED = [
    "What are the salary bands for engineering in Italy?",
    "How many days of annual leave do I get?",
    "Ley 10/2021 acuerdo de teletrabajo",
] as const;

/** `keyword 1 · vector 3`: where each leg that found the hit ranked it. */
export function legsLabel(hit: Pick<SearchHitView, "keyword_rank" | "vector_rank">): string {
    const parts: string[] = [];
    if (hit.keyword_rank !== null) parts.push(`keyword ${hit.keyword_rank}`);
    if (hit.vector_rank !== null) parts.push(`vector ${hit.vector_rank}`);
    return parts.join(" · ");
}

/** The badge's class: found by both legs, or by one. */
export function legClass(hit: Pick<SearchHitView, "keyword_rank" | "vector_rank">): string {
    const keyword = hit.keyword_rank !== null;
    const vector = hit.vector_rank !== null;
    if (keyword && vector) return "leg leg-both";
    return keyword ? "leg leg-keyword" : "leg leg-vector";
}

/** `0.0325`: fused scores are small, four decimals tell them apart. */
export function fused(score: number): string {
    return score.toFixed(4);
}

/** `Annual leave policy > Booking leave`, or `(no section)`. */
export function trail(section: readonly string[]): string {
    return section.length ? section.join(" > ") : "(no section)";
}

/** The first `max` characters of a passage, cut at a word, with an ellipsis. */
export function snippet(text: string, max = 220): string {
    const flat = text.replace(/\s+/g, " ").trim();
    if (flat.length <= max) return flat;
    const cut = flat.slice(0, max);
    const space = cut.lastIndexOf(" ");
    return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** `36 chunks searched` or `reads nothing`. */
export function searchedLabel(view: Pick<SearchView, "reads_nothing" | "searched">): string {
    if (view.reads_nothing) return "reads nothing";
    return view.searched === 1 ? "1 chunk searched" : `${view.searched} chunks searched`;
}

/** `kept out 4 chunks of 1 page`, or `nothing kept out`. */
export function withheldLabel(view: Pick<SearchView, "withheld" | "withheld_chunks">): string {
    const pages = view.withheld.length;
    if (!pages) return "nothing kept out";
    const chunks = view.withheld_chunks === 1 ? "1 chunk" : `${view.withheld_chunks} chunks`;
    return `kept out ${chunks} of ${pages === 1 ? "1 page" : `${pages} pages`}`;
}

/**
 * Whether the person in the other column may read a hit, from its readers and their
 * groups now: the same rule as the search store's filter (`admits`). `null` while the
 * other column has no answer yet. A leaver or a stranger reads nothing.
 */
export function otherCanRead(
    readers: AclView | null | undefined,
    other: Pick<SearchView, "user_id" | "groups" | "reads_nothing"> | null | undefined,
): boolean | null {
    if (!other) return null;
    if (other.reads_nothing || !readers) return false;
    return (
        readers.users.includes(other.user_id) ||
        readers.groups.some((group) => other.groups.includes(group))
    );
}
