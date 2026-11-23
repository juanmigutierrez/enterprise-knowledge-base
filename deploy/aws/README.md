# AWS deployment (not part of the POC)

The POC runs locally (decisions.md D13). If it is deployed, this folder holds the
infrastructure as code for the lean, scale-to-zero profile in decisions.md D18:
SQS instead of Kafka, Lambda instead of Fargate, OpenSearch Serverless, DynamoDB,
AgentCore Gateway with Lambda targets, AWS Budgets alert at about $20.

The same container image and the same `config/` files are used; only the option
names for the infrastructure steps change (`bus: sqs`, `rawstore: s3`, `policies: verified_permissions`).
