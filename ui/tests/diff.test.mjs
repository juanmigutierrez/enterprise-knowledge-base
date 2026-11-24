import assert from "node:assert/strict";
import test from "node:test";
import { ACTIONS, avoidedRuns, totalDecisions } from "../src/diff.ts";
import { versionStep } from "../src/format.ts";

const counts = { process: 14, acl_only: 1, skip: 2, purge: 2 };

test("T3-T7 runs avoided are the fast-tracked and skipped events, never the deletes", () => {
    assert.equal(avoidedRuns(counts), 3);
    assert.equal(totalDecisions(counts), 19);
});

test("every action is listed once, in pipeline order", () => {
    assert.deepEqual(
        ACTIONS.map((a) => a.action),
        ["process", "acl_only", "skip", "purge"],
    );
});

test("a version step says when the item was new to T2", () => {
    assert.equal(versionStep(null, "1.1"), "new → 1.1");
    assert.equal(versionStep("1.1", "2.1"), "1.1 → 2.1");
});
