import assert from "node:assert/strict";
import test from "node:test";
import {
    actionState,
    answerClass,
    answerLabel,
    change,
    pageName,
    probeKey,
    routeOf,
    sameProbe,
    workDone,
} from "../src/lifecycle.ts";

const probe = (cited, held = []) => ({
    user_id: "alice",
    question: "How often do mentoring groups meet?",
    page: "hr_docs:HR-PL-11",
    cited,
    pages: cited ? ["hr_docs:HR-PL-11"] : [],
    held_back: held,
    reads_nothing: false,
    reason: "reads as alice",
});

test("an answer says whether the page it depends on is cited, held back or absent", () => {
    assert.equal(answerLabel(probe(true)), "cites HR-PL-11");
    const denied = { document_key: "hr_docs:HR-PL-11", verdict: "denied", reason: "no" };
    const held = probe(false, [denied]);
    assert.equal(answerLabel(held), "held back (denied)");
    assert.equal(answerClass(held), "life life-held");
    assert.equal(answerLabel(probe(false)), "not cited");
    assert.equal(answerClass(probe(false)), "life life-absent");
    assert.equal(answerLabel({ ...probe(false), reads_nothing: true }), "reads nothing");
    assert.equal(answerLabel(undefined), "—");
});

test("a page appearing or going between two moments is named", () => {
    assert.equal(change(probe(true), probe(false)), "lost");
    assert.equal(change(probe(false), probe(true)), "gained");
    assert.equal(change(probe(true), probe(true)), "");
    assert.equal(change(undefined, probe(true)), "");
});

test("the worker's route reads like the pipeline's, and skips are left out", () => {
    assert.equal(
        routeOf({ action: "acl_only", steps: ["T2 diff", "T8 fast lane"] }),
        "T2 acl_only → T8 fast lane",
    );
    assert.equal(
        routeOf({ action: null, steps: ["re-link", "T3 parse", "T6 entities", "T8 publish"] }),
        "re-link → T3 → T6 → T8 publish",
    );
    const traces = [{ status: "skipped" }, { status: "relinked" }];
    assert.deepEqual(workDone({ traces }), [{ status: "relinked" }]);
});

test("actions and probes are told apart", () => {
    assert.equal(actionState({ applied: true, blocked: "already applied" }), "applied");
    assert.equal(actionState({ applied: false, blocked: "needs add_page first" }), "waiting");
    assert.equal(actionState({ applied: false, blocked: "" }), "ready");
    assert.equal(pageName("hr_docs:HR-PL-99"), "HR-PL-99");
    assert.equal(probeKey(probe(true)), "alice|How often do mentoring groups meet?");
    const rows = [probe(true), { ...probe(true), user_id: "lucia" }];
    assert.equal(sameProbe(rows, { user_id: "lucia", question: probe(true).question }), rows[1]);
    assert.equal(sameProbe(rows, { user_id: "bob", question: "x" }), undefined);
});
