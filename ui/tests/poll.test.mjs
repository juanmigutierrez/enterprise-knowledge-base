import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as wait } from "node:timers/promises";
import { poll } from "../src/poll.ts";

test("slow requests never overlap and stopping cancels the next poll", async () => {
    let calls = 0;
    let finish;
    let next;
    const second = new Promise(resolve => { next = resolve; });
    const stop = poll(() => {
        calls++;
        if (calls === 2) next();
        return new Promise(resolve => { finish = resolve; });
    }, 1);
    try {
        await wait(20);
        assert.equal(calls, 1);
        finish();
        await second;
        stop();
        finish();
        await wait(20);
        assert.equal(calls, 2);
    } finally {
        stop();
    }
});
