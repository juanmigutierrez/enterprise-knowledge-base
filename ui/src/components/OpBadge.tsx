import type { ChangeOp } from "../api/client";
import { OP_LABEL } from "../format";

export function OpBadge({ op }: { op: ChangeOp }) {
    return <span className={`op op-${op}`}>{OP_LABEL[op]}</span>;
}
