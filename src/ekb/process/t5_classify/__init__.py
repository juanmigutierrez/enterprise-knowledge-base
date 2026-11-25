"""T5 · Classify department, sensitivity and PII (layer 4, process). Card 4.9.

T5 labels every chunk with three things the access layer filters on later: the
**department** it belongs to, how **sensitive** it is (public, internal,
confidential, restricted) and which kinds of **personal data** (PII) it holds.
``sensitivity`` is one of the three fields the publication gate needs, so every
chunk leaves T5 with one.

Built in S8: the ``Classifier`` port and two options. ``rules`` looks for
explicit markers ("Restricted to ...", "Do not forward") and personal data
patterns (IBAN, codice fiscale, ...), and takes the department from the source.
``model_plus_rules`` adds a model's judgment through the gateway, and the
stricter of the two wins. A missed restriction is a leak; an extra one is only
friction (card 4.9).
"""
