import assert from "node:assert/strict";
import test from "node:test";
import {
    SUGGESTED,
    fused,
    legClass,
    legsLabel,
    otherCanRead,
    planLabel,
    redactionsLabel,
    releasedLabel,
    releasedParts,
    searchFor,
    searchedLabel,
    snippet,
    trail,
    verdictClass,
    verdictLabel,
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

test("a column says what Q1 searched and what Q4 cited or held back", () => {
    assert.equal(planLabel({ searches: ["annual leave"] }), "1 search");
    assert.equal(planLabel({ searches: ["leave?", "remote?"] }), "2 searches: “leave?” · “remote?”");
    assert.equal(releasedLabel({ released: 3, held_back: 2 }), "Q4: 3 cited, 2 held back");
    assert.equal(releasedLabel({ released: 5, held_back: 0 }), "Q4: 5 cited");
    assert.equal(releasedLabel({ released: 0, held_back: 0 }), "Q4: nothing to check");
});

test("a hit says whether Q4 cited it or held it back, and why", () => {
    assert.equal(verdictLabel({ verdict: "allowed", citation: 1 }), "[1] cited");
    assert.equal(verdictLabel({ verdict: "denied", citation: null }), "held back: denied");
    assert.equal(verdictLabel({ verdict: "unavailable", citation: null }), "held back: unavailable");
    assert.equal(verdictClass({ citation: 2 }), "q4 q4-cited");
    assert.equal(verdictClass({ citation: null }), "q4 q4-held");
});

test("removed personal data is marked in the released text and counted", () => {
    assert.deepEqual(releasedParts("Sent to IBAN [IBAN removed] after."), [
        { text: "Sent to IBAN ", removed: false },
        { text: "[IBAN removed]", removed: true },
        { text: " after.", removed: false },
    ]);
    assert.deepEqual(releasedParts("[card number removed]"), [
        { text: "[card number removed]", removed: true },
    ]);
    assert.deepEqual(releasedParts("nothing [to see] here"), [
        { text: "nothing [to see] here", removed: false },
    ]);
    assert.deepEqual(releasedParts(""), []);
    assert.equal(
        redactionsLabel([
            { kind: "iban", count: 2 },
            { kind: "es_national_id", count: 1 },
        ]),
        "2 IBAN removed, 1 ID number removed",
    );
    assert.equal(redactionsLabel([]), "");
});
