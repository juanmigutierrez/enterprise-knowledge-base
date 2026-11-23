"""Cross-cutting building blocks with no business logic.

- ``registry``: the option plug-in system (one implementation per option).
- ``config``: loads which option each step uses (config/*.yaml).

Planned here as the POC grows: the model gateway (provider switch,
content-hash cache, cost logging) and telemetry helpers.
"""
