"""3 · Change log and raw store: the durable backbone between connectors and workers.

Sub-packages are infrastructure ports with local and AWS options:
``bus`` (Kafka / SQS, card 4.15), ``registry`` (DynamoDB), ``rawstore`` (S3 / SeaweedFS),
and ``acceptance`` (S17: what the bus accepted, shared by every process).
"""
