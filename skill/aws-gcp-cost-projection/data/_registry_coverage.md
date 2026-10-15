# Service Registry Coverage Matrix (Layer-1.5)

Total Services Registered: **54**

| Canonical Service | Display Name | Product Code(s) | Charge Types | Tested | Primary Target / Mechanic |
|---|---|---|---|---|---|
| `acm` | AWS Certificate Manager | `AWSCertificateManager, ACM` | 3 | Yes | `misc` |
| `apigateway` | Amazon API Gateway | `AmazonApiGateway` | 1 | Yes | `per_request` |
| `appsync` | AWS AppSync | `AWSAppSync` | 2 | Yes | `per_request` |
| `athena` | Amazon Athena | `AmazonAthena` | 1 | Yes | `athena` |
| `backup` | AWS Backup | `*(alias fallback)*` | 2 | Yes | `block_storage` |
| `bedrock` | Amazon Bedrock | `AmazonBedrock, AmazonBedrockService` | 2 | Yes | `misc` |
| `cloudtrail` | AWS CloudTrail | `*(alias fallback)*` | 2 | Yes | `per_request` |
| `cloudwatch` | Amazon CloudWatch | `AmazonCloudWatch` | 1 | Yes | `cloudwatch` |
| `cognito` | Amazon Cognito | `*(alias fallback)*` | 1 | Yes | `misc` |
| `datatransfer` | AWS Data Transfer | `AWSDataTransfer` | 1 | Yes | `data_transfer` |
| `directconnect` | AWS Direct Connect | `*(alias fallback)*` | 2 | Yes | `data_transfer` |
| `dms` | AWS Database Migration Service | `*(alias fallback)*` | 2 | Yes | `data_transfer` |
| `documentdb` | Amazon DocumentDB | `*(alias fallback)*` | 2 | Yes | `managed_db` |
| `dynamodb` | Amazon DynamoDB | `AmazonDynamoDB` | 2 | Yes | `misc` |
| `ec2` | Amazon Elastic Compute Cloud | `AmazonEC2` | 8 | Yes | `non_workload` |
| `ecr` | Amazon Elastic Container Registry | `AmazonECR` | 3 | Yes | `data_transfer` |
| `ecs` | Amazon Elastic Container Service | `AmazonECS` | 5 | Yes | `per_request` |
| `efs` | Amazon Elastic File System | `AmazonEFS` | 1 | Yes | `efs` |
| `eks` | Amazon Elastic Kubernetes Service | `AmazonEKS` | 1 | Yes | `flat_hourly` |
| `elasticache` | Amazon ElastiCache | `AmazonElastiCache` | 1 | Yes | `elasticache` |
| `elb` | Elastic Load Balancing | `AWSELB` | 2 | Yes | `data_transfer` |
| `emr` | Amazon EMR | `*(alias fallback)*` | 1 | Yes | `emr` |
| `events` | Amazon EventBridge | `AWSEvents` | 2 | Yes | `per_request` |
| `fargate` | AWS Fargate | `*(alias fallback)*` | 3 | Yes | `per_request` |
| `fsx` | Amazon FSx | `AmazonFSx` | 1 | Yes | `fsx` |
| `globalaccelerator` | AWS Global Accelerator | `*(alias fallback)*` | 2 | Yes | `data_transfer` |
| `glue` | AWS Glue | `AWSGlue` | 1 | Yes | `glue` |
| `guardduty` | Amazon GuardDuty | `AmazonGuardDuty` | 1 | Yes | `guardduty` |
| `inspector` | Amazon Inspector | `AmazonInspector` | 2 | Yes | `per_request` |
| `kinesis` | Amazon Kinesis | `AmazonKinesis` | 2 | Yes | `per_request` |
| `kms` | AWS Key Management Service | `awskms, AmazonKMS` | 3 | Yes | `per_request` |
| `lambda` | AWS Lambda | `AWSLambda` | 1 | Yes | `per_request` |
| `memorydb` | Amazon MemoryDB | `*(alias fallback)*` | 2 | Yes | `managed_db` |
| `mq` | Amazon MQ | `AmazonMQ` | 3 | Yes | `misc` |
| `msk` | Amazon Managed Streaming for Apache Kafka | `AmazonMSK` | 3 | Yes | `block_storage` |
| `mwaa` | Amazon Managed Workflows for Apache Airflow | `AmazonMWAA` | 1 | Yes | `mwaa` |
| `opensearch` | Amazon OpenSearch Service | `AmazonES` | 3 | Yes | `data_transfer` |
| `quicksight` | Amazon QuickSight | `AmazonQuickSight` | 2 | Yes | `per_request` |
| `rds` | Amazon Relational Database Service | `AmazonRDS` | 5 | Yes | `rds_extended_support` |
| `redshift` | Amazon Redshift | `AmazonRedshift` | 1 | Yes | `redshift` |
| `route53` | Amazon Route 53 | `AmazonRoute53` | 1 | Yes | `per_request` |
| `s3` | Amazon Simple Storage Service | `AmazonS3` | 3 | Yes | `data_transfer` |
| `sagemaker` | Amazon SageMaker | `*(alias fallback)*` | 1 | Yes | `misc` |
| `secretsmanager` | AWS Secrets Manager | `AWSSecretsManager` | 3 | Yes | `per_request` |
| `securityhub` | AWS Security Hub | `*(alias fallback)*` | 1 | Yes | `guardduty` |
| `ses` | Amazon Simple Email Service | `AmazonSES` | 1 | Yes | `misc` |
| `shield` | AWS Shield | `AWSShield` | 1 | Yes | `shield` |
| `sns` | Amazon Simple Notification Service | `AmazonSNS` | 1 | Yes | `per_request` |
| `sqs` | Amazon Simple Queue Service | `*(alias fallback)*` | 1 | Yes | `per_request` |
| `ssm` | AWS Systems Manager | `AWSSystemsManager` | 2 | Yes | `ssm_parameter_store` |
| `states` | AWS Step Functions | `AmazonStates` | 2 | Yes | `misc` |
| `vpc` | Amazon Virtual Private Cloud | `AmazonVPC` | 3 | Yes | `data_transfer` |
| `waf` | AWS WAF | `awswaf` | 3 | Yes | `flat_hourly` |
| `xray` | AWS X-Ray | `AWSXRay` | 1 | Yes | `xray` |

## Documented Pricing Assumptions & Derivations

### Aurora Serverless v2 ACU Sizing (RV-D2)
- **RAM = 2.0 GiB/ACU**: Explicitly stated in AWS documentation that 1 ACU provides approximately 2 GiB of memory along with corresponding CPU:
  [AWS Aurora Serverless v2 Requirements](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2.requirements.html)
- **vCPU = 0.25/ACU**: AWS does not specify an exact vCPU-per-ACU number. Aurora runs on memory-optimized (R-class) architecture with an 8 GiB RAM per vCPU ratio (`2 GiB ÷ 8 GiB/vCPU = 0.25 vCPU/ACU`). This matches Google Cloud SQL N4 custom instances which allow up to 8 GB/vCPU (a 2 GiB memory to 0.25 vCPU shape):
  [Google Cloud SQL Machine Series Overview](https://cloud.google.com/sql/docs/mysql/machine-series-overview)
- **Caveat**: Cloud SQL general-purpose custom machine types cap memory at 6.5 GB/vCPU (which would require ~0.31 vCPU/ACU). 0.25 assumes Cloud SQL N4 custom machine series.

