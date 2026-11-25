import assert from "node:assert/strict";
import test from "node:test";
import {
    SUGGESTED,
    fused,
    legClass,
    legsLabel,
    otherCanRead,
    searchFor,
    searchedLabel,
    snippet,
    trail,
    withheldLabel,
} from "../src/search.ts";

test("changing the person or question hides the previous poll response", () => {
    const previous = { user_id: "bob", question: "salary bands" };
    assert.equal(searchFor(previous, "alice", "salary bands"), null);
    assert.equal(searchFor(previous, "bob", "annual leave"), null);
    assert.equal(searchFor(null, "alice", "annual leave"), null);
    assert.equal(searchFor(previous, "bob", "salary bands"), previous);
});

test("a hit says which legs found it and where", () => {
    assert.equal(legsLabel({ keyword_rank: 1, vector_rank: 3 }), "keyword 1 · vector 3");
    assert.equal(legsLabel({ keyword_rank: null, vector_rank: 2 }), "vector 2");
    assert.equal(legClass({ keyword_rank: 1, vector_rank: 3 }), "leg leg-both");
    assert.equal(legClass({ keyword_rank: 1, vector_rank: null }), "leg leg-keyword");
    assert.equal(legClass({ keyword_rank: null, vector_rank: 1 }), "leg leg-vector");
});

test("scores, trails and passages read well", () => {
    assert.equal(fused(1 / 61 + 1 / 62), "0.0325");
    assert.equal(trail(["Annual leave policy", "Booking leave"]), "Annual leave policy > Booking leave");
    assert.equal(trail([]), "(no section)");
    assert.equal(snippet("short\n  text"), "short text");
    const long = snippet("word ".repeat(100), 30);
    assert.ok(long.endsWith("…"));
    assert.ok(long.length <= 31);
});

test("a column says how much was searched and what the pre-filter kept out", () => {
    assert.equal(searchedLabel({ reads_nothing: false, searched: 36 }), "36 chunks searched");
    assert.equal(searchedLabel({ reads_nothing: false, searched: 1 }), "1 chunk searched");
    assert.equal(searchedLabel({ reads_nothing: true, searched: 0 }), "reads nothing");
    const page = { document_key: "hr_docs:HR-CMP-01", title: "", chunks: 4, readers: null };
    assert.equal(withheldLabel({ withheld: [page], withheld_chunks: 4 }), "kept out 4 chunks of 1 page");
    assert.equal(withheldLabel({ withheld: [], withheld_chunks: 0 }), "nothing kept out");
});

test("a hit says whether the other column's person may read it, by the store's rule", () => {
    const alice = { user_id: "alice", groups: ["all-staff", "it-employees"], reads_nothing: false };
    const pay = { users: [], groups: ["hr-compensation"] };
    assert.equal(otherCanRead(pay, alice), false);
    assert.equal(otherCanRead({ users: [], groups: ["all-staff"] }, alice), true);
    assert.equal(otherCanRead({ users: ["alice"], groups: [] }, alice), true);
    const olivia = { user_id: "olivia", groups: ["all-staff"], reads_nothing: true };
    assert.equal(otherCanRead({ users: [], groups: ["all-staff"] }, olivia), false);
    assert.equal(otherCanRead(pay, null), null);
});

test("the suggested questions cover a restricted page, a common one and an exact term", () => {
    assert.equal(SUGGESTED.length, 3);
    assert.ok(SUGGESTED[0].includes("salary"));
});
