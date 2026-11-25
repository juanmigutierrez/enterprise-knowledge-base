"""5 · Entity store: the canonical people and teams (later retailers, brands, products ...).

Phase 1 of D6: one item per entity under its canonical ID. Built in S9: the
``EntityStore`` port and an ``in_memory`` option, filled from the HR record
API's change events. DynamoDB (production, and DynamoDB Local in S17) and
Neo4j (optional, phase 2 of D6) come later. Card 4.17.
"""
