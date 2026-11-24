// Typed access to the inspector API. Every type comes from schema.d.ts, which is
// generated from the API's OpenAPI schema (npm run gen:types): if the back end
// changes a field, this file stops compiling instead of failing at run time.
import type { components, operations } from "./schema";

type Schemas = components["schemas"];
export type AclView = Schemas["AclView"];
export type ChangeLogView = Schemas["ChangeLogView"];
export type DecisionPage = Schemas["DecisionPage"];
export type DecisionView = Schemas["DecisionView"];
export type DiffCounts = Schemas["DiffCounts"];
export type DocumentView = Schemas["DocumentView"];
export type EventPage = Schemas["EventPage"];
export type EventView = Schemas["EventView"];
export type GroupView = Schemas["GroupView"];
export type OriginalView = Schemas["OriginalView"];
export type RecordView = Schemas["RecordView"];
export type SourceView = Schemas["SourceView"];
export type ChangeOp = EventView["op"];
export type DiffAction = DecisionView["action"];

export type EventsQuery = NonNullable<
    operations["events_api_events_get"]["parameters"]["query"]
>;
type DocumentsQuery = NonNullable<
    operations["documents_api_documents_get"]["parameters"]["query"]
>;
type RegistryQuery = NonNullable<
    operations["registry_records_api_registry_get"]["parameters"]["query"]
>;
type OriginalsQuery =
    operations["originals_api_originals_get"]["parameters"]["query"];
export type DecisionsQuery = NonNullable<
    operations["t2_decisions_api_t2_decisions_get"]["parameters"]["query"]
>;

async function get<T>(
    path: string,
    query: Record<string, string | number | null | undefined> = {},
) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null && value !== "")
            params.set(key, String(value));
    }
    const url = params.size ? `${path}?${params}` : path;
    const response = await fetch(url);
    if (!response.ok)
        throw new Error(`${url}: ${response.status} ${response.statusText}`);
    return (await response.json()) as T;
}

export const api = {
    sources: () => get<SourceView[]>("/api/sources"),
    documents: (query: DocumentsQuery = {}) =>
        get<DocumentView[]>("/api/documents", query),
    events: (query: EventsQuery = {}) => get<EventPage>("/api/events", query),
    changelog: () => get<ChangeLogView>("/api/changelog"),
    registry: (query: RegistryQuery = {}) =>
        get<RecordView[]>("/api/registry", query),
    originals: (query: OriginalsQuery) =>
        get<OriginalView[]>("/api/originals", query),
    t2Decisions: (query: DecisionsQuery = {}) =>
        get<DecisionPage>("/api/t2/decisions", query),
};
