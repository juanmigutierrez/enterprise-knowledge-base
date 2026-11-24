import assert from "node:assert/strict";
import test from "node:test";
import { kindSummary, listedDetail, sectionLabels, trail } from "../src/parse.ts";

test("deleted and re-created rows cannot display a previous parse", () => {
    const key = "hr_docs:policy";
    const summary = { document_key: key, version: "1.1", parsed_at: "2026-11-24T10:00:00Z" };
    const detail = { summary, text: "old policy" };
    assert.equal(listedDetail(key, [summary], detail), detail);
    assert.equal(listedDetail(key, [], detail), null);
    assert.equal(listedDetail(key, [{ ...summary, parsed_at: "2026-11-24T11:00:00Z" }], detail), null);
    assert.equal(listedDetail(null, [summary], detail), null);
});

const block = (kind, text, section, extra = {}) => ({
    kind,
    text,
    section,
    level: kind === "heading" ? section.length + 1 : 0,
    marker: "",
    rows: [],
    ...extra,
});

const policy = ["Annual leave policy"];
const booking = [...policy, "Booking leave"];
const carrying = [...policy, "Carrying leave over"];

test("a trail joins the headings above a block, outermost first", () => {
    assert.equal(trail(booking), "Annual leave policy › Booking leave");
    assert.equal(trail([]), "top of the page");
});

test("the section is shown once, above the first block of each section", () => {
    const blocks = [
        block("heading", "Annual leave policy", []),
        block("heading", "Booking leave", policy),
        block("list_item", "Request leave", booking, { level: 1, marker: "1." }),
        block("list_item", "Manager approves", booking, { level: 1, marker: "2." }),
        block("heading", "Carrying leave over", policy),
        block("paragraph", "Up to 5 days", carrying),
    ];
    assert.deepEqual(sectionLabels(blocks), [
        null,
        null,
        "Annual leave policy › Booking leave",
        null,
        null,
        "Annual leave policy › Carrying leave over",
    ]);
});

test("text before any heading is labelled as the top of the page", () => {
    assert.deepEqual(sectionLabels([block("paragraph", "Intro", [])]), [
        "top of the page",
    ]);
});

test("block kinds are summarised in the order they first appear", () => {
    assert.equal(
        kindSummary({ heading: 5, paragraph: 2, list_item: 6 }),
        "5 heading · 2 paragraph · 6 list item",
    );
    assert.equal(kindSummary({}), "");
});
