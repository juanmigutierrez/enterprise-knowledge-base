"""Enterprise knowledge base for AI agents.

The package layout mirrors the architecture diagram (docs/architecture.md):

    2 connect    -> ekb.connect      connectors + T1 normalize
    3 change log -> ekb.changelog    event bus, document registry, raw store
    4 process    -> ekb.process      T2-T8 worker steps
    5 stores     -> ekb.stores       search, entities, metrics, policies, permissions
    6 access     -> ekb.access       auth middleware, Q1-Q4, MCP server
    8 operate    -> ekb.operate      freshness, reconciliation, telemetry
                    ekb.evaluation   option experiments and quality gates

Layers 1 (sources) and 7 (agents) live outside the package: they are not part
of the product, they are what the product talks to.
"""

__version__ = "0.1.0"
