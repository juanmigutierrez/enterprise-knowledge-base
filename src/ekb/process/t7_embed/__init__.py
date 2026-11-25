"""4 · T7 Embed (S10): each chunk becomes a vector; the ID of what made it is stored with it.

A vector is a list of numbers (1024 for bge-m3) placed so that texts with
similar meaning sit close together, in any language the model knows. Search
(S13) compares a question's vector with the chunks' vectors.

- ``input``: what text of a chunk is embedded (``input@1``).
- ``port``: :class:`~ekb.process.t7_embed.port.Embedder`, what every option implements.
- ``step``: :class:`~ekb.process.t7_embed.step.EmbedStep`, which checks every vector.
- ``options/``: ``model`` (through the model gateway's ``embed`` task: bge-m3 on
  Ollama locally, Cohere Embed v4 on Bedrock in the production design) and
  ``fake`` (offline, no meaning, for CI). Card 4.4.
"""
