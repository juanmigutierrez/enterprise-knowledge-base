import type { AclView, RedactionView, SearchHitView, SearchView } from "./api/client";

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

/** `1 search`, or `2 searches: "…" · "…"`: what Q1 planned (S14). */
export function planLabel(view: Pick<SearchView, "searches">): string {
    const count = view.searches.length;
    if (count <= 1) return "1 search";
    return `${count} searches: ${view.searches.map((s) => `“${s}”`).join(" · ")}`;
}

/** `Q4: 3 cited, 2 held back`, or `Q4: nothing to check`. */
export function releasedLabel(view: Pick<SearchView, "released" | "held_back">): string {
    if (!view.released && !view.held_back) return "Q4: nothing to check";
    const held = view.held_back ? `, ${view.held_back} held back` : "";
    return `Q4: ${view.released} cited${held}`;
}

/** `[1] cited`, or `held back: denied`: what Q4 decided about one hit. */
export function verdictLabel(hit: Pick<SearchHitView, "verdict" | "citation">): string {
    if (hit.citation !== null) return `[${hit.citation}] cited`;
    return `held back: ${hit.verdict}`;
}

/** The badge's class: released and cited, or held back. */
export function verdictClass(hit: Pick<SearchHitView, "citation">): string {
    return hit.citation !== null ? "q4 q4-cited" : "q4 q4-held";
}

/** `1 IBAN removed`, or an empty string. */
export function redactionsLabel(redactions: readonly RedactionView[]): string {
    const names: Record<string, string> = {
        iban: "IBAN",
        payment_card: "card number",
        it_fiscal_code: "fiscal code",
        es_national_id: "ID number",
    };
    return redactions
        .map((r) => `${r.count} ${names[r.kind] ?? r.kind.replace(/_/g, " ")} removed`)
        .join(", ");
}

/** A released text cut into plain pieces and the markers Q4 put where personal data was. */
export function releasedParts(text: string): { text: string; removed: boolean }[] {
    const parts: { text: string; removed: boolean }[] = [];
    const marker = /\[[^\]]+ removed\]/g;
    let cursor = 0;
    for (const match of text.matchAll(marker)) {
        const at = match.index ?? 0;
        if (at > cursor) parts.push({ text: text.slice(cursor, at), removed: false });
        parts.push({ text: match[0], removed: true });
        cursor = at + match[0].length;
    }
    if (cursor < text.length) parts.push({ text: text.slice(cursor), removed: false });
    return parts;
}
