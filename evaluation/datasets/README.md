# Frozen HR questions, v1

The development file has 80 questions. The hold-out file has 20 separately phrased questions about held-back facts. Both use the synthetic HR corpus and the structure-aware chunker. Each JSON line records an ID, question, question language, category, expected answer, relevant chunk IDs and allowed user IDs. Multi-document answers join source excerpts with ` + `.

The runner verifies IDs, supporting text and reader labels against the ingested corpus. Human semantic review is pending. Reserve the hold-out for final scoring and give changed datasets a new version. Timed edits, deletes and permission changes use separate live scenarios described in the [walkthrough](../../docs/walkthrough/S18-evaluation.md).
