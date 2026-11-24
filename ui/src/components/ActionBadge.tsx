import type { DiffAction } from "../api/client";
import { ACTION_LABEL } from "../format";

export function ActionBadge({ action }: { action: DiffAction }) {
    return <span className={`act act-${action}`}>{ACTION_LABEL[action]}</span>;
}
