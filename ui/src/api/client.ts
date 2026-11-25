// Typed access to the inspector API. Every type comes from schema.d.ts, which is
// generated from the API's OpenAPI schema (npm run gen:types): if the back end
// changes a field, this file stops compiling instead of failing at run time.
import type { components, operations } from "./schema";

type Schemas = components["schemas"];
export type AclView = Schemas["AclView"];
export type BlockView = Schemas["BlockView"];
export type CallPage = Schemas["CallPage"];
export type CallTotalsView = Schemas["CallTotalsView"];
export type CallView = Schemas["CallView"];
export type ChangeLogView = Schemas["ChangeLogView"];
export type ChunkedDocumentView = Schemas["ChunkedDocumentView"];
export type ChunkListView = Schemas["ChunkListView"];
export type ChunkSummaryView = Schemas["ChunkSummaryView"];
export type ChunkView = Schemas["ChunkView"];
export type DecisionPage = Schemas["DecisionPage"];
export type DecisionView = Schemas["DecisionView"];
export type DiffCounts = Schemas["DiffCounts"];
export type DocumentTraceView = Schemas["DocumentTraceView"];
export type DocumentView = Schemas["DocumentView"];
export type EntityLinkView = Schemas["EntityLinkView"];
export type EntityListView = Schemas["EntityListView"];
export type EntityView = Schemas["EntityView"];
export type EventPage = Schemas["EventPage"];
export type EventView = Schemas["EventView"];
export type GatewayProviderView = Schemas["GatewayProviderView"];
export type GatewayQuotaView = Schemas["GatewayQuotaView"];
export type GatewayRouteView = Schemas["GatewayRouteView"];
export type GatewayView = Schemas["GatewayView"];
export type GroupView = Schemas["GroupView"];
export type MembershipView = Schemas["MembershipView"];
export type MentionView = Schemas["MentionView"];
export type OriginalView = Schemas["OriginalView"];
export type ParsedDocumentView = Schemas["ParsedDocumentView"];
export type ParsedListView = Schemas["ParsedListView"];
export type ParsedSummaryView = Schemas["ParsedSummaryView"];
export type PermissionsView = Schemas["PermissionsView"];
export type PipelineView = Schemas["PipelineView"];
export type PublicationView = Schemas["PublicationView"];
export type RecordView = Schemas["RecordView"];
export type SourceView = Schemas["SourceView"];
export type StepTotalsView = Schemas["StepTotalsView"];
export type TracedDocumentView = Schemas["TracedDocumentView"];
export type TraceStepView = Schemas["TraceStepView"];
export type TraceView = Schemas["TraceView"];
export type ChangeOp = EventView["op"];
export type DiffAction = DecisionView["action"];
export type BlockKind = BlockView["kind"];
export type EntityKind = EntityView["kind"];
export type PublicationStatus = PublicationView["status"];
export type TraceStatus = TraceView["status"];

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
type ParsedQuery = NonNullable<
    operations["t3_parsed_api_t3_parsed_get"]["parameters"]["query"]
>;
type ParsedDocumentQuery =
    operations["t3_parsed_document_api_t3_parsed_document_get"]["parameters"]["query"];
type ChunksQuery = NonNullable<
    operations["t4_chunks_api_t4_chunks_get"]["parameters"]["query"]
>;
type ChunkedDocumentQuery =
    operations["t4_chunks_document_api_t4_chunks_document_get"]["parameters"]["query"];
type EntitiesQuery = NonNullable<
    operations["t6_entities_api_t6_entities_get"]["parameters"]["query"]
>;
type PipelineQuery = NonNullable<
    operations["pipeline_traces_api_pipeline_traces_get"]["parameters"]["query"]
>;
type PipelineTraceQuery =
    operations["pipeline_trace_api_pipeline_trace_get"]["parameters"]["query"];
type GatewayCallsQuery = NonNullable<
    operations["gateway_calls_api_gateway_calls_get"]["parameters"]["query"]
>;

type Query = Record<string, string | number | null | undefined>;

function url(path: string, query: Query): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null && value !== "")
            params.set(key, String(value));
    }
    return params.size ? `${path}?${params}` : path;
}

async function get<T>(path: string, query: Query = {}) {
    const address = url(path, query);
    const response = await fetch(address);
    if (!response.ok)
        throw new Error(`${address}: ${response.status} ${response.statusText}`);
    return (await response.json()) as T;
}

/** Like `get`, but a 404 is an answer (`null`), not an error: the item isn't there. */
async function getOrNull<T>(path: string, query: Query = {}) {
    const address = url(path, query);
    const response = await fetch(address);
    if (response.status === 404) return null;
    if (!response.ok)
        throw new Error(`${address}: ${response.status} ${response.statusText}`);
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
    t3Parsed: (query: ParsedQuery = {}) =>
        get<ParsedListView>("/api/t3/parsed", query),
    t3Document: (query: ParsedDocumentQuery) =>
        getOrNull<ParsedDocumentView>("/api/t3/parsed/document", query),
    t4Chunks: (query: ChunksQuery = {}) =>
        get<ChunkListView>("/api/t4/chunks", query),
    t4Document: (query: ChunkedDocumentQuery) =>
        getOrNull<ChunkedDocumentView>("/api/t4/chunks/document", query),
    t6Entities: (query: EntitiesQuery = {}) =>
        get<EntityListView>("/api/t6/entities", query),
    t8Permissions: () => get<PermissionsView>("/api/t8/permissions"),
    pipeline: (query: PipelineQuery = {}) =>
        get<PipelineView>("/api/pipeline/traces", query),
    pipelineTrace: (query: PipelineTraceQuery) =>
        getOrNull<DocumentTraceView>("/api/pipeline/trace", query),
    gateway: () => get<GatewayView>("/api/gateway"),
    gatewayCalls: (query: GatewayCallsQuery = {}) =>
        get<CallPage>("/api/gateway/calls", query),
};
