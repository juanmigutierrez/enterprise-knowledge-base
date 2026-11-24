import { useState } from "react";
import {
    api,
    type ChangeLogView,
    type GroupView,
    type RecordView,
} from "../api/client";
import { bytes, orDash, readers, shortHash, time } from "../format";
import { loadOriginals, originalsFor } from "../originals";
import { usePolling } from "../usePolling";

/**
 * Box 3 (S3): the event bus, the document registry and the raw store.
 * Top: how many events each partition holds and how far each consumer group
 * has read. Below: what the registry remembers per item (tombstones struck
 * through) and, for the selected item, every original the raw store keeps.
 */
export function ChangeLogScreen({ sources }: { sources: string[] }) {
    const [source, setSource] = useState("");
    const [selected, setSelected] = useState<string | null>(null);
    const bus = usePolling(api.changelog, 3000);
    const records = usePolling(
        () => api.registry({ source: source || null }),
        3000,
        [source],
    );
    const request = usePolling(
        () => selected ? loadOriginals(selected, api.originals) : Promise.resolve(null),
        3000,
        [selected],
    );
    const originals = originalsFor(selected, request.data);
    const rows = records.data ?? [];
    const live = rows.filter((r) => !r.deleted).length;

    return (
        <section className="stack">
            <div className="pane compact">
                <h2>Event bus</h2>
                {bus.error && <p className="error">{bus.error}</p>}
                {bus.data && <BusTable bus={bus.data} />}
            </div>
            <div className="split">
                <div className="pane">
                    <div className="toolbar">
                        <strong>Document registry</strong>
                        <select
                            value={source}
                            onChange={(e) => {
                                setSource(e.target.value);
                                setSelected(null);
                            }}
                        >
                            <option value="">All sources</option>
                            {sources.map((s) => (
                                <option key={s}>{s}</option>
                            ))}
                        </select>
                        <span className="muted">
                            {live} live · {rows.length - live} deleted
                        </span>
                    </div>
                    {records.error && <p className="error">{records.error}</p>}
                    <div className="scroll">
                        <RegistryTable
                            rows={rows}
                            selected={selected}
                            onSelect={setSelected}
                        />
                    </div>
                </div>
                <aside className="pane history">
                    <h2>{selected ?? "Raw store"}</h2>
                    <div className="scroll">
                        {!selected && (
                            <p className="muted">
                                Click an item to see the originals the raw store
                                keeps for it, oldest first.
                            </p>
                        )}
                        {selected && !originals && <p className="muted">Loading originals...</p>}
                        {originals?.error && <p className="error">{originals.error}</p>}
                        {originals?.data?.length === 0 && (
                            <p className="empty">
                                No originals: a permissions-only item, or not
                                stored yet.
                            </p>
                        )}
                        {!!originals?.data?.length && (
                            <table className="grid">
                                <thead>
                                    <tr>
                                        <th>version</th>
                                        <th>size</th>
                                        <th>checksum</th>
                                        <th>stored</th>
                                        <th>pointer</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {originals.data.map((o) => (
                                        <tr key={o.pointer}>
                                            <td className="mono">
                                                {o.version}
                                            </td>
                                            <td className="num">
                                                {bytes(o.size)}
                                            </td>
                                            <td className="mono">
                                                {shortHash(o.checksum)}
                                            </td>
                                            <td>{time(o.stored_at)}</td>
                                            <td className="mono">
                                                {o.pointer}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </aside>
            </div>
        </section>
    );
}

/** One row per partition; per consumer group, its committed offset there. */
function BusTable({ bus }: { bus: ChangeLogView }) {
    const partitions = bus.end_offsets.map((end, partition) => ({
        partition,
        end,
    }));
    return (
        <>
            <p className="muted">
                {bus.events} events in {bus.partitions} partitions. A document
                always goes to the same partition, so its events stay in order.
                Each consumer group reads at its own pace; a group commits an
                event only after handling it.
            </p>
            <table className="grid">
                <thead>
                    <tr>
                        <th>partition</th>
                        <th>events</th>
                        {bus.groups.map((g) => (
                            <th key={g.group}>{g.group}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {partitions.map(({ partition, end }) => (
                        <tr key={partition}>
                            <td className="mono">p{partition}</td>
                            <td className="num">{end}</td>
                            {bus.groups.map((g) => (
                                <td key={g.group} className="num">
                                    <Position
                                        group={g}
                                        partition={partition}
                                        end={end}
                                    />
                                </td>
                            ))}
                        </tr>
                    ))}
                    <tr>
                        <td className="muted">pending</td>
                        <td />
                        {bus.groups.map((g) => (
                            <td
                                key={g.group}
                                className={g.lag ? "num pending" : "num"}
                            >
                                {g.lag}
                            </td>
                        ))}
                    </tr>
                </tbody>
            </table>
        </>
    );
}

function Position({
    group,
    partition,
    end,
}: {
    group: GroupView;
    partition: number;
    end: number;
}) {
    const committed = group.committed[partition] ?? 0;
    const pending = end - committed;
    return (
        <span title={`next pending offset: ${committed}`}>
            {committed}
            {pending > 0 && <span className="pending"> (+{pending})</span>}
        </span>
    );
}

function RegistryTable({
    rows,
    selected,
    onSelect,
}: {
    rows: RecordView[];
    selected: string | null;
    onSelect: (documentKey: string) => void;
}) {
    if (!rows.length) return <p className="empty">Nothing recorded yet.</p>;
    return (
        <table className="grid selectable">
            <thead>
                <tr>
                    <th>item</th>
                    <th>state</th>
                    <th>version</th>
                    <th>readers</th>
                    <th>hash</th>
                    <th>original</th>
                    <th>originals</th>
                    <th>events</th>
                    <th>changed by</th>
                    <th>at</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((r) => (
                    <tr
                        key={r.document_key}
                        className={[
                            r.deleted ? "deleted" : "",
                            r.document_key === selected ? "on" : "",
                        ].join(" ")}
                        onClick={() => onSelect(r.document_key)}
                    >
                        <td className="mono">{r.document_key}</td>
                        <td>{r.deleted ? "tombstone" : "live"}</td>
                        <td className="mono">{r.version}</td>
                        <td>{r.deleted ? "—" : readers(r.acl)}</td>
                        <td className="mono">{shortHash(r.content_hash)}</td>
                        <td className="mono">
                            {orDash(r.raw_pointer?.split("/").pop())}
                        </td>
                        <td className="num">{r.originals}</td>
                        <td className="num">{r.events}</td>
                        <td>{orDash(r.changed_by)}</td>
                        <td>{time(r.updated_at)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
