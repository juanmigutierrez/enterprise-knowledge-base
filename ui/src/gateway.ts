import type { CallView, GatewayQuotaView } from "./api/client";

export type Outcome = CallView["outcome"];

export const OUTCOME_LABEL: Record<Outcome, string> = {
    hit: "cache hit",
    ok: "ok",
    rate_limited: "rate limited",
    unavailable: "unavailable",
    invalid: "invalid",
};

type DayUse = Pick<GatewayQuotaView, "used_today" | "per_day">;

/** How much of a daily quota is used, 0–100, for its bar. No daily cap: 0. */
export function dayShare(quota: DayUse): number {
    if (quota.per_day === null) return 0;
    if (quota.per_day <= 0) return 100;
    return Math.min(100, Math.round((quota.used_today / quota.per_day) * 100));
}

/** `12/50 today`, or `12 today (no daily cap)`. */
export function dayLabel(quota: DayUse): string {
    return quota.per_day === null
        ? `${quota.used_today} today (no daily cap)`
        : `${quota.used_today}/${quota.per_day} today`;
}

/** Free-tier calls cost nothing; a paid one costs fractions of a cent, so six decimals. */
export function money(usd: number | null): string {
    if (usd === null) return "Pending reconciliation";
    return usd === 0 ? "$0" : `$${usd.toFixed(6)}`;
}

/** `ollama:gemma4:e4b` → `["ollama", "gemma4:e4b"]`: only the first colon separates. */
export function splitRef(ref: string): [string, string] {
    const at = ref.indexOf(":");
    return at < 0 ? [ref, ""] : [ref.slice(0, at), ref.slice(at + 1)];
}

/** The cost log in reverse, newest call first, at most `limit` rows. */
export function latestFirst(calls: CallView[], limit = 200): CallView[] {
    return [...calls].sort((a, b) => b.seq - a.seq).slice(0, limit);
}
