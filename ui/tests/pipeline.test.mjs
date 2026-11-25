import assert from "node:assert/strict";
import test from "node:test";
import {
    STATUSES,
    ms,
    route,
    sourceId,
    statusClass,
    stepClass,
    stepKind,
    where,
} from "../src/pipeline.ts";

test("every status has a meaning and a badge", () => {
    assert.deepEqual(
        STATUSES.map((s) => s.status),
        ["published", "readers", "record", "skipped", "purged", "failed", "parked", "rebuilt"],
    );
    assert.equal(statusClass("parked"), "trace trace-parked");
});

test("steps are coloured by outcome and by part of the line", () => {
    assert.equal(stepKind("T8 readers first"), "t8");
    assert.equal(stepKind("T3 parse"), "t3");
    assert.equal(stepKind("purge search"), "purge");
    assert.equal(stepKind("dead letters"), "dead");
    assert.equal(stepKind("permission store"), "store");
    assert.equal(
        stepClass({ step: "T7 embed", outcome: "failed" }),
        "step step-failed step-t7",
    );
});

test("an attempt says where it sat in the bus, or that it was a rebuild", () => {
    assert.equal(where({ partition: 0, offset: 15 }), "p0@15");
    assert.equal(where({ partition: null, offset: null }), "rebuild");
});

test("times are precise when small and rounded when not", () => {
    assert.equal(ms(0.4213), "0.42 ms");
    assert.equal(ms(26.2), "26 ms");
});

test("the route names T2's decision and shortens the transformations", () => {
    const step = (name) => ({ step: name, outcome: "done", detail: "", ms: 0 });
    const trace = {
        action: "process",
        steps: ["T2 diff", "T8 readers first", "T3 parse", "T7 embed", "T8 publish"].map(step),
    };
    assert.equal(route(trace), "T2 process → T8 readers first → T3 → T7 → T8 publish");
    assert.equal(route({ action: null, steps: [step("dead letters")] }), "dead letters");
});

test("a document key reads as the source's own ID", () => {
    assert.equal(sourceId("hr_docs:HR-PL-05"), "HR-PL-05");
    assert.equal(sourceId("identity:users/alice"), "users/alice");
});
