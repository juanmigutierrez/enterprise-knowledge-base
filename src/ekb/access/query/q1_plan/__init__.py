"""Q1 · Plan: what one question should search for (S14). Card 4.8.

- ``port``: :class:`~ekb.access.query.q1_plan.port.Planner`, what every option
  implements: a question in, a :class:`~ekb.contracts.QueryPlan` out.
- ``options/``: ``none`` (the question as it is: one search) and ``rules``
  (a question with several parts becomes one search per part), both S14.
  ``rewrite_when_needed`` (card 4.8: a model rewrites multi-part and non-English
  questions through the gateway's ``plan_query`` task) runs as ``rules`` until
  its experiment; S14b adds a route for counting questions, which go to SQL.

Q1 decides nothing about who is asking: it plans the same searches for everyone.
Q2 and Q3 then run each search over what the asker may read.
"""
