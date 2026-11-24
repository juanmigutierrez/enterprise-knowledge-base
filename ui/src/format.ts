import type { AclView, ChangeOp } from "./api/client";

export function readers(acl: AclView): string {
    const names = [...acl.groups, ...acl.users.map((user) => `user:${user}`)];
    return names.length ? names.join(", ") : "—";
}

export function shortHash(hash: string | null | undefined): string {
    return hash ? hash.replace("sha256:", "").slice(0, 10) : "—";
}

export function time(iso: string): string {
    return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export function orDash(value: string | null | undefined): string {
    return value ?? "—";
}

/** Where an event sits in the bus: `p0@7` is partition 0, offset 7. */
export function place(at: { partition: number; offset: number }): string {
    return `p${at.partition}@${at.offset}`;
}

export function bytes(size: number): string {
    return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`;
}

export const OP_LABEL: Record<ChangeOp, string> = {
    upsert: "upsert",
    delete: "delete",
    acl_change: "acl change",
};
