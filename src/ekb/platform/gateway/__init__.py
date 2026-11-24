"""The model gateway (S7): every model call in the knowledge base goes through here.

A step asks for a *task* ("write a context header", "classify this chunk"), not
for a model. The gateway, configured in ``config/*.yaml`` (``gateway:``):

1. **routes** the task to its candidates, an ordered list of ``provider:model``
   (D15): free tiers first-to-last, so a used-up quota moves the call to the next;
2. **reuses** a reply already made for exactly the same call (the content-hash
   cache, D16 #1), so re-processing an unchanged document costs nothing;
3. **guards** the limits before calling: each free tier's requests per minute and
   per day, and the spend cap for paid models;
4. **logs** every call, cached or not, with tokens, cost and outcome, but never
   the prompt or the reply (the cost log feeds the inspector's gateway panel).

Modules:

- ``config``: the ``gateway:`` section of the settings (tasks, models, quotas).
- ``models``: the request, the reply, the cached reply and the call record.
- ``port``: the three ports, :class:`ModelClient`, :class:`ResponseCache` and
  :class:`CostLog`, each with options in ``options/``.
- ``keys``: the cache key.
- ``usage``: quota windows and totals, computed from the cost log.
- ``errors``: what can go wrong, in the gateway's own words.
- ``gateway``: :class:`ModelGateway`, which puts it all together.

LangChain makes the actual call (``options/langchain.py``) and is imported
nowhere else: steps depend on the gateway, never on a model library.
"""
