import assert from "node:assert/strict";
import test from "node:test";
import { loadOriginals, originalsFor } from "../src/originals.ts";

test("switching items hides prior originals through loading, failure and empty recovery", async () => {
    const first = await loadOriginals("A", async () => [{ pointer: "original-A" }]);
    assert.equal(originalsFor("A", first).data[0].pointer, "original-A");
    assert.equal(originalsFor("B", first), null);
    const failed = await loadOriginals("B", async () => { throw Error("offline"); });
    assert.match(originalsFor("B", failed).error, /offline/);
    assert.equal(originalsFor("B", failed).data, null);
    assert.equal(originalsFor("A", failed), null);
    const empty = await loadOriginals("B", async () => []);
    assert.deepEqual(originalsFor("B", empty).data, []);
    assert.equal(originalsFor("B", empty).error, null);
});
