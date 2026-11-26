"""``ekb demo <step>``: run one build step on the synthetic data and explain it.

Each plan step (docs/plan.md §4.2) adds one module here with a ``run_<step>``
function (the story, testable with any HTTP client) and a CLI command. A demo
prints what happens in plain language, to be read next to the step's
walkthrough in docs/walkthrough/.
"""

from ekb.app.demos import (  # noqa: F401  (importing registers the commands)
    answer,
    gateway,
    infra,
    ingest,
    lifecycle,
    mcp,
    s1,
    s1b,
    s1c,
    s1d,
    s3,
    search,
    t1,
    t2,
    t3,
    t4,
    t5,
    t6,
    t7,
    t8,
)
from ekb.app.demos._app import demo_app

__all__ = ["demo_app"]
