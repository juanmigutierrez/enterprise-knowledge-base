import assert from "node:assert/strict";
import test from "node:test";
import {
    chunkCount,
    listedChunks,
    piiLabel,
    SENSITIVITIES,
    sensitivityClass,
    sensitivityCounts,
    sizeRange,
    sizeShare,
} from "../src/chunk.ts";

test("deleted and re-created rows cannot display a previous chunking", () => {
    const key = "hr_docs:policy";
    const summary = { document_key: key, version: "1.1", chunked_at: "2026-11-24T10:00:00Z" };
    const detail = { summary, chunks: [] };
    assert.equal(listedChunks(key, [summary], detail), detail);
    assert.equal(listedChunks(key, [], detail), null);
    assert.equal(
        listedChunks(key, [{ ...summary, chunked_at: "2026-11-24T11:00:00Z" }], detail),
        null,
    );
    assert.equal(listedChunks(key, [{ ...summary, version: "2.1" }], detail), null);
    assert.equal(listedChunks(null, [summary], detail), null);
});

test("sizes read as a range, or one number when the chunks are the same size", () => {
    assert.equal(sizeRange({ smallest: 38, largest: 59 }), "38–59 tokens");
    assert.equal(sizeRange({ smallest: 7, largest: 7 }), "7 tokens");
});

test("the size bar is a share of the limit, full when over it", () => {
    assert.equal(sizeShare(100, 400), 25);
    assert.equal(sizeShare(0, 400), 0);
    assert.equal(sizeShare(900, 400), 100);
    assert.equal(sizeShare(10, 0), 100);
});

test("counts say chunk or chunks", () => {
    assert.equal(chunkCount(1), "1 chunk");
    assert.equal(chunkCount(4), "4 chunks");
    assert.equal(chunkCount(0), "0 chunks");
});

test("sensitivity counts read least strict first, and a dash when nothing is labelled", () => {
    assert.equal(sensitivityCounts({ confidential: 1, internal: 3 }), "3 internal, 1 confidential");
    assert.equal(sensitivityCounts({ restricted: 4 }), "4 restricted");
    assert.equal(sensitivityCounts({}), "—");
    assert.equal(sensitivityCounts({ internal: 0, secret: 2 }), "2 secret");
});

test("each sensitivity has its own badge class; an unlabelled chunk a neutral one", () => {
    assert.equal(sensitivityClass("restricted"), "sens sens-restricted");
    assert.equal(sensitivityClass(null), "sens");
    assert.deepEqual([...SENSITIVITIES], ["public", "internal", "confidential", "restricted"]);
});

test("personal data reads as a list, or nothing", () => {
    assert.equal(piiLabel(["email", "phone"]), "PII: email, phone");
    assert.equal(piiLabel([]), "");
});
