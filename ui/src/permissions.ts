import type { AclView, MembershipView, PublicationStatus, PublicationView } from "./api/client";

/** `HR-PL-05` from `hr_docs:HR-PL-05`: the source's own ID, short enough for a grid column. */
export function shortKey(documentKey: string): string {
    const at = documentKey.indexOf(":");
    return at < 0 ? documentKey : documentKey.slice(at + 1);
}

/** What each publication status means, in a few words. */
export function statusLabel(status: PublicationStatus): string {
    switch (status) {
        case "published":
            return "published";
        case "readers_only":
            return "readers only";
        case "waiting":
            return "waiting for T8";
    }
}

/** The badge's class: one colour per publication status. */
export function statusClass(status: PublicationStatus): string {
    return `pub pub-${status}`;
}

/**
 * `hr-team, user:bob`, `nobody` (readers that admit no one) or `not set` (before T8).
 * Like `readers` in format.ts, but an empty list says what it means here. Kept free of
 * runtime imports, like the other helpers, so the Node tests can load it alone.
 */
export function readersLabel(acl: AclView | null | undefined): string {
    if (!acl) return "not set";
    const names = [...acl.groups, ...acl.users.map((u) => `user:${u}`)];
    return names.length ? names.join(", ") : "nobody";
}

/** Whether a document's readers admit this user today: the inspector's grid cell. */
export function canRead(document: Pick<PublicationView, "readers">, userId: string): boolean {
    return document.readers.includes(userId);
}

/** `12 people`, `1 person`, `no one`. */
export function peopleCount(n: number): string {
    if (n === 0) return "no one";
    return n === 1 ? "1 person" : `${n} people`;
}

/** Whether a fast lane changed the readers after the text: the two versions differ. */
export function readersNewer(document: Pick<PublicationView, "text_version" | "acl_version">): boolean {
    return (
        document.text_version !== null &&
        document.acl_version !== null &&
        document.text_version !== document.acl_version
    );
}

/** Active users first, in the order the API lists them; leavers last. */
export function byActive(users: readonly MembershipView[]): MembershipView[] {
    return [...users.filter((u) => u.active), ...users.filter((u) => !u.active)];
}
