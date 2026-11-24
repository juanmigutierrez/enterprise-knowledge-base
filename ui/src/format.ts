import type { AclView, ChangeOp, DiffAction } from "./api/client";

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

export const ACTION_LABEL: Record<DiffAction, string> = {
    process: "process",
    acl_only: "acl only",
    skip: "skip",
    purge: "purge",
};

/** `1.1 → 2.1`, or `new → 1.1` when T2 had never processed the item. */
export function versionStep(previous: string | null, version: string): string {
    return `${previous ?? "new"} → ${version}`;
}
