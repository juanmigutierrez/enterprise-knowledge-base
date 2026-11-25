"""The HR assistant's answer (S15): written from what Q4 released, and cached safely.

The query path (Q1 to Q4, S14) ends with cited passages a person may read now.
The ``ask`` tool turns them into a short answer with ``[n]`` citations:

- ``port``: :class:`~ekb.access.answer.port.AnswerWriter` (the question and the
  released passages in, a :class:`~ekb.access.answer.port.Draft` out) and
  :class:`~ekb.access.answer.port.AnswerCache` (answers kept for reuse, with what
  they were written from).
- ``options/``: the writers ``model`` (the gateway's ``answer`` task, card 4.11)
  and ``extractive`` (no model: the passages' opening sentences, for the ``ci``
  profile and offline demos); the cache ``in_memory``.

**The writer only ever sees released passages.** Held-back text never reaches
a prompt, so a model cannot leak what Q4 stopped. **A cached answer is never
reused without a fresh Q4 check** for the person asking now (D16 #10, D8): the
rule lives where the query path is wired, ``ekb.app.assistant``.
"""
