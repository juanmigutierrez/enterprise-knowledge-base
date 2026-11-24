import type { EventView } from "../api/client";
import { orDash, place, readers, shortHash, time } from "../format";
import { OpBadge } from "./OpBadge";

/** Events as rows: the same columns wherever events are shown, whatever the source. */
export function EventTable({
    events,
    showKey = true,
}: {
    events: EventView[];
    showKey?: boolean;
}) {
    if (!events.length) return <p className="empty">No events yet.</p>;
    return (
        <table className="grid">
            <thead>
                <tr>
                    <th>#</th>
                    <th>received</th>
                    <th title="partition@offset in the event bus">bus</th>
                    <th>op</th>
                    {showKey && <th>item</th>}
                    <th>version</th>
                    <th>person</th>
                    <th>changed by</th>
                    <th>readers</th>
                    <th>hash</th>
                </tr>
            </thead>
            <tbody>
                {events.map((event) => (
                    <tr key={event.seq}>
                        <td className="num">{event.seq}</td>
                        <td>{time(event.received_at)}</td>
                        <td className="mono">{place(event)}</td>
                        <td>
                            <OpBadge op={event.op} />
                        </td>
                        {showKey && (
                            <td className="mono">{event.document_key}</td>
                        )}
                        <td className="mono">{event.version}</td>
                        <td>{orDash(event.person_id)}</td>
                        <td>{orDash(event.changed_by)}</td>
                        <td>{readers(event.acl)}</td>
                        <td className="mono">
                            {shortHash(event.content_hash)}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
