"""Cross-cutting building blocks with no business logic.

- ``registry``: the option plug-in system (one implementation per option).
- ``config``: loads which option each step uses (config/*.yaml).
- ``gateway``: the model gateway (S7): every model call is routed, cached,
  guarded against quotas and spend, and logged here.

Planned here as the POC grows: telemetry helpers.
"""
