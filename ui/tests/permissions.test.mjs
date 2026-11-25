import assert from "node:assert/strict";
import test from "node:test";
import {
    byActive,
    canRead,
    peopleCount,
    readersLabel,
    readersNewer,
    shortKey,
    statusClass,
    statusLabel,
} from "../src/permissions.ts";

test("a document key reads as the source's own ID", () => {
    assert.equal(shortKey("hr_docs:HR-PL-05"), "HR-PL-05");
    assert.equal(shortKey("HR-PL-05"), "HR-PL-05");
});

test("each publication status has a label and a badge", () => {
    assert.equal(statusLabel("published"), "published");
    assert.equal(statusLabel("readers_only"), "readers only");
    assert.equal(statusLabel("waiting"), "waiting for T8");
    assert.equal(statusClass("readers_only"), "pub pub-readers_only");
});

test("readers read as groups then users, nobody, or not set", () => {
    assert.equal(readersLabel({ groups: ["hr-team"], users: ["bob"] }), "hr-team, user:bob");
    assert.equal(readersLabel({ groups: [], users: [] }), "nobody");
    assert.equal(readersLabel(null), "not set");
});

test("a grid cell is whether the document's readers admit the user today", () => {
    const doc = { readers: ["bob", "dana"] };
    assert.equal(canRead(doc, "bob"), true);
    assert.equal(canRead(doc, "alice"), false);
});

test("counts say no one, person or people", () => {
    assert.equal(peopleCount(0), "no one");
    assert.equal(peopleCount(1), "1 person");
    assert.equal(peopleCount(12), "12 people");
});

test("the fast lane shows as readers newer than the text", () => {
    assert.equal(readersNewer({ text_version: "1.1", acl_version: "1.2" }), true);
    assert.equal(readersNewer({ text_version: "1.1", acl_version: "1.1" }), false);
    assert.equal(readersNewer({ text_version: null, acl_version: "1.2" }), false);
});

test("leavers are listed after the active users, each group in its order", () => {
    const user = (user_id, active) => ({
        user_id,
        active,
        groups: [],
        source_version: "0",
        documents: 0,
    });
    const listed = byActive([user("alice", true), user("olivia", false), user("bob", true)]);
    assert.deepEqual(
        listed.map((u) => u.user_id),
        ["alice", "bob", "olivia"],
    );
});
