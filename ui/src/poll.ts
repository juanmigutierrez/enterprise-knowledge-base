/** Schedule the next request only after the current one settles. */
export function poll(load: () => Promise<unknown>, delay: number) {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
        try {
            await load();
        } finally {
            if (!stopped) timer = setTimeout(() => void tick(), delay);
        }
    }
    void tick();
    return () => {
        stopped = true;
        clearTimeout(timer);
    };
}
