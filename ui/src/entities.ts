import type { EntityLinkView, MentionView } from "./api/client";

/** `person` or `team`: the kind is the prefix of every entity ID (S9). */
export function kindOf(entityId: string): string {
    return entityId.split(":", 1)[0] ?? "";
}

/** The chip's class: one colour per kind of entity. */
export function entityClass(entityId: string): string {
    return `entity entity-${kindOf(entityId)}`;
}

/** `'People team' (team rule)`: the words behind a link, and the rule that found them. */
export function mentionLabel(mention: Pick<MentionView, "text" | "rule">): string {
    return `'${mention.text}' (${mention.rule} rule)`;
}

/** `3 links`, `1 link`, `no links`. */
export function linkCount(n: number): string {
    return n === 0 ? "no links" : `${n} link${n === 1 ? "" : "s"}`;
}

export interface DocumentLinks {
    document_key: string;
    title: string;
    links: EntityLinkView[];
}

/** An entity's links, one group per document, in the order they came. */
export function byDocument(links: readonly EntityLinkView[]): DocumentLinks[] {
    const groups = new Map<string, DocumentLinks>();
    for (const link of links) {
        let group = groups.get(link.document_key);
        if (!group) {
            group = { document_key: link.document_key, title: link.title, links: [] };
            groups.set(link.document_key, group);
        }
        group.links.push(link);
    }
    return [...groups.values()];
}
