import assert from "node:assert/strict";
import test from "node:test";
import { dayLabel, dayShare, latestFirst, money, OUTCOME_LABEL, splitRef } from "../src/gateway.ts";

test("a quota's bar is the share of its daily cap used, full when used up", () => {
    assert.equal(dayShare({ used_today: 12, per_day: 50 }), 24);
    assert.equal(dayShare({ used_today: 50, per_day: 50 }), 100);
    assert.equal(dayShare({ used_today: 60, per_day: 50 }), 100);
    assert.equal(dayShare({ used_today: 0, per_day: 0 }), 100);
    assert.equal(dayShare({ used_today: 9, per_day: null }), 0);
});

test("a quota reads as used of cap, or says there is no daily cap", () => {
    assert.equal(dayLabel({ used_today: 12, per_day: 50 }), "12/50 today");
    assert.equal(dayLabel({ used_today: 3, per_day: null }), "3 today (no daily cap)");
});

test("free calls cost $0; paid ones show fractions of a cent", () => {
    assert.equal(money(null), "Pending reconciliation");
    assert.equal(money(0), "$0");
    assert.equal(money(0.0001234), "$0.000123");
});

test("only the first colon separates the provider from the model", () => {
    assert.deepEqual(splitRef("ollama:gemma4:e4b"), ["ollama", "gemma4:e4b"]);
    assert.deepEqual(splitRef("openrouter:openrouter/free"), ["openrouter", "openrouter/free"]);
    assert.deepEqual(splitRef("nocolon"), ["nocolon", ""]);
});

test("calls are shown newest first, at most the limit", () => {
    const calls = [1, 2, 3].map((seq) => ({ seq }));
    assert.deepEqual(
        latestFirst(calls).map((c) => c.seq),
        [3, 2, 1],
    );
    assert.deepEqual(
        latestFirst(calls, 2).map((c) => c.seq),
        [3, 2],
    );
    assert.deepEqual(
        calls.map((c) => c.seq),
        [1, 2, 3],
    ); // not sorted in place
});

test("every outcome has a label", () => {
    assert.deepEqual(Object.keys(OUTCOME_LABEL).sort(), [
        "hit",
        "invalid",
        "ok",
        "rate_limited",
        "unavailable",
    ]);
});
