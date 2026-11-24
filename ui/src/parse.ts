import type { BlockKind, BlockView, ParsedDocumentView, ParsedSummaryView } from "./api/client";

/** The list and detail poll independently. Only display the listed parse. */
export function listedDetail(
    selected: string | null,
    documents: ParsedSummaryView[],
    detail: ParsedDocumentView | null,
) {
    if (!detail || detail.summary.document_key !== selected) return null;
    const listed = documents.find((doc) => doc.document_key === selected);
    if (listed?.version !== detail.summary.version || listed.parsed_at !== detail.summary.parsed_at)
        return null;
    return detail;
}

export const KIND_LABEL: Record<BlockKind, string> = {
    heading: "heading",
    paragraph: "paragraph",
    list_item: "list item",
    table: "table",
    code: "code",
};

/** `Annual leave policy › Booking leave`: the headings a block sits under. */
export function trail(section: string[]): string {
    return section.length ? section.join(" › ") : "top of the page";
}

/**
 * Where to show a block's section: above the first block of each section, so
 * the trail every block carries is visible without repeating it on every line.
 * Headings show none: they start the section the blocks after them belong to.
 */
export function sectionLabels(blocks: BlockView[]): (string | null)[] {
    let shown: string | null = null;
    return blocks.map((block) => {
        if (block.kind === "heading") return null;
        const label = trail(block.section);
        if (label === shown) return null;
        shown = label;
        return label;
    });
}

/** `5 heading · 2 paragraph · 6 list item`, in the order the kinds first appear. */
export function kindSummary(kinds: Record<string, number>): string {
    return Object.entries(kinds)
        .map(([kind, n]) => `${n} ${KIND_LABEL[kind as BlockKind] ?? kind}`)
        .join(" · ");
}
