import type { OriginalView } from "./api/client";

type Result = {
    documentKey: string;
    data: OriginalView[] | null;
    error: string | null;
};

/** Tag failures as well as successes so changing selection hides the old response immediately. */
export async function loadOriginals(
    documentKey: string,
    fetchOriginals: (query: { document_key: string }) => Promise<OriginalView[]>,
): Promise<Result> {
    try {
        return { documentKey, data: await fetchOriginals({ document_key: documentKey }), error: null };
    } catch (error) {
        return { documentKey, data: null, error: String(error) };
    }
}

export function originalsFor(documentKey: string | null, result: Result | null) {
    return result?.documentKey === documentKey ? result : null;
}
