import assert from "node:assert/strict";
import test from "node:test";
import { byDocument, entityClass, kindOf, linkCount, mentionLabel } from "../src/entities.ts";

test("the kind is read from the entity ID, and gives the chip its colour", () => {
    assert.equal(kindOf("person:bob"), "person");
    assert.equal(kindOf("team:T-PPL"), "team");
    assert.equal(entityClass("team:T-PPL"), "entity entity-team");
});

test("a mention reads as its words and its rule", () => {
    assert.equal(mentionLabel({ text: "People team", rule: "team" }), "'People team' (team rule)");
});

test("counts say link or links, and none", () => {
    assert.equal(linkCount(0), "no links");
    assert.equal(linkCount(1), "1 link");
    assert.equal(linkCount(3), "3 links");
});

test("links are grouped by document, keeping their order", () => {
    const link = (document_key, chunk_id) => ({
        entity_id: "team:T-PPL",
        document_key,
        title: document_key,
        chunk_id,
        text: "People team",
        rule: "team",
    });
    const groups = byDocument([link("a", "a#1"), link("b", "b#0"), link("a", "a#3")]);
    assert.deepEqual(
        groups.map((g) => [g.document_key, g.links.map((l) => l.chunk_id)]),
        [
            ["a", ["a#1", "a#3"]],
            ["b", ["b#0"]],
        ],
    );
    assert.deepEqual(byDocument([]), []);
});
