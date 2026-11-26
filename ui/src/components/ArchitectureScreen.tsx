import { useState } from "react";

// Bounds in diagram coordinates. The overlay and the original image scale together.
const boxes = {
    docs: [74, 328, 446, 190], hr: [74, 653, 446, 191], identity: [74, 1304, 446, 190],
    docsConnector: [616, 328, 411, 190], hrConnector: [616, 653, 411, 191],
    identityConnector: [616, 1304, 411, 190], normalize: [1091, 328, 367, 1166],
    registry: [1566, 314, 468, 191], bus: [1566, 620, 468, 191], originals: [1566, 976, 468, 191],
    diff: [2204, 309, 750, 143], parse: [2204, 494, 750, 143], chunk: [2204, 679, 750, 143],
    classify: [2204, 864, 750, 144], entities: [2204, 1050, 750, 143], embed: [2204, 1235, 750, 142],
    publish: [2204, 1420, 750, 143], people: [3156, 555, 735, 176], search: [3156, 993, 735, 176],
    permissions: [3156, 1414, 735, 176], recheck: [3572, 2110, 384, 141],
} as const;

type Node = keyof typeof boxes;
const pageWrite: Node[] = ["docs", "docsConnector", "normalize", "registry", "bus", "originals",
    "diff", "parse", "chunk", "classify", "entities", "embed", "publish", "search", "permissions"];

const routes: Record<string, { title: string; nodes: Node[]; explanation: string }> = {
    add_page: { title: "1. Add a page", nodes: pageWrite,
        explanation: "The docs connector records the original and emits a change. The worker turns the page into passages. T8 publishes them with their readers so Alice can cite the policy." },
    edit_page: { title: "2. Edit the page", nodes: [...pageWrite, "recheck"],
        explanation: "The webhook delivers a new version. T2 detects the content change, the worker updates the passages, and the source recheck catches the version mismatch on an earlier answer." },
    revoke_page: { title: "3. Revoke on the page", nodes: ["docs", "docsConnector", "normalize", "registry", "bus", "diff", "publish", "permissions", "search", "recheck"],
        explanation: "Q4 checks the source and immediately withholds the page from Alice. On the write path, T2 routes the reader change straight to T8 to update access without re-embedding the text." },
    remove_from_group: { title: "4. Revoke by group", nodes: ["identity", "identityConnector", "normalize", "bus", "permissions", "recheck"],
        explanation: "Q4 checks Lucía’s current membership on her next request. The identity connector then updates the stored group membership. The indexed policy text stays the same." },
    hire_record: { title: "5. A hire, in HR", nodes: ["hr", "hrConnector", "normalize", "bus", "diff", "entities"],
        explanation: "The HR connector sees Sara’s employee record. Entity resolution needs a matching identity account before the worker can link her to the mentoring policy." },
    hire_account: { title: "6. A hire, her account", nodes: ["identity", "identityConnector", "hr", "hrConnector", "normalize", "bus", "entities", "people", "search"],
        explanation: "The identity sync and HR full listing let T6 resolve Sara. The worker records her canonical ID and re-links the existing mentoring passages to her." },
    leaver: { title: "7. A leaver", nodes: ["hr", "hrConnector", "normalize", "bus", "diff", "entities", "people", "search"],
        explanation: "A full HR listing discovers Lucía’s missing record. The worker removes her entity and refreshes the links in passages that mention her." },
    delete_page: { title: "8. Delete a page", nodes: ["docs", "docsConnector", "normalize", "registry", "bus", "originals", "diff", "permissions", "search", "recheck"],
        explanation: "Q4 immediately withholds the deleted travel page. T2 routes the delete to purge its originals and searchable passages. The change also invalidates cached answers that used it." },
};

export function ArchitectureScreen({ step, onSelect, onReturn }: {
    step: string; onSelect: (id: string) => void; onReturn: () => void;
}) {
    const [zoom, setZoom] = useState(false);
    const route = routes[step];
    return (
        <section className="pane architecture">
            <div className="toolbar">
                <label>Lifecycle step{" "}
                    <select value={step} onChange={(event) => onSelect(event.target.value)}>
                        <option value="">Full architecture</option>
                        {Object.entries(routes).map(([id, item]) =>
                            <option key={id} value={id}>{item.title}</option>)}
                    </select>
                </label>
                <button aria-pressed={zoom} onClick={() => setZoom(!zoom)}>
                    {zoom ? "Fit diagram" : "Zoom in"}
                </button>
                <button onClick={onReturn}>{route ? "Open lifecycle step" : "Open lifecycle tutorial"}</button>
            </div>
            <p className="diagram-caption" aria-live="polite">{route?.explanation ??
                "Read layers 1–5 from left to right to follow a source change. Along the bottom, follow an agent’s question through the access checks in layer 6."}</p>
            <div className="scroll diagram-scroll" tabIndex={0} aria-label="Architecture diagram">
                <div className={zoom ? "diagram zoomed" : "diagram"}>
                    <img src="./local-compose.png"
                        alt="Local Docker Compose architecture. Sources feed connectors, a change log, worker and knowledge stores. Agents query the knowledge API. Operations span all layers." />
                    <svg viewBox="0 0 4056 2464" aria-hidden="true">
                        {route?.nodes.map((node) => {
                            const [x, y, width, height] = boxes[node];
                            return <rect key={node} x={x} y={y} width={width} height={height} rx="8" />;
                        })}
                    </svg>
                </div>
            </div>
        </section>
    );
}
