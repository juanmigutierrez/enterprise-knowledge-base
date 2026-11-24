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

export const OP_LABEL: Record<ChangeOp, string> = {
    upsert: "upsert",
    delete: "delete",
    acl_change: "acl change",
};
