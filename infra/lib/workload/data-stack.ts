/**
 * System of record in af-south-1: Aurora PostgreSQL Serverless v2 (pgvector, RLS), reached
 * only through the RDS Data API (ADR 0004); S3 for documents, transcripts and analytics; the
 * environment's KMS key.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { EnvName } from '../shared/config';

export interface DataStackProps extends StackProps {
  envName: EnvName;
}

export class DataStack extends Stack {
  readonly key: kms.Key;
  readonly cluster: rds.DatabaseCluster;
  readonly appSecret: secretsmanager.Secret;
  readonly docsBucket: s3.Bucket;
  readonly analyticsBucket: s3.Bucket;
  readonly databaseName = 'frontdesk';

  /** Fixed AZs so synth needs no AWS lookup (CI has no credentials). af-south-1 has a, b, c. */
  get availabilityZones(): string[] {
    return [`${this.region}a`, `${this.region}b`];
  }

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const prod = props.envName === 'prod';
    const retain = prod ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;

    // One key per environment. Tenant separation in S3 comes from the tenant id in the
    // encryption context and per-tenant prefixes, not from a key per tenant (USD 1/key-month).
    this.key = new kms.Key(this, 'Key', {
      alias: `alias/frontdesk-${props.envName}`,
      enableKeyRotation: true,
      description: 'Front Desk data at rest (Aurora, S3, SNS, logs)',
      removalPolicy: retain,
    });

    // CloudWatch Logs encrypts the app's log groups with this key; it needs explicit leave.
    this.key.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal(`logs.${this.region}.amazonaws.com`)],
      actions: ['kms:Encrypt*', 'kms:Decrypt*', 'kms:ReEncrypt*', 'kms:GenerateDataKey*', 'kms:Describe*'],
      resources: ['*'],
      conditions: { ArnLike: { 'kms:EncryptionContext:aws:logs:arn': `arn:aws:logs:${this.region}:${this.account}:*` } },
    }));

    // Isolated subnets only: no internet gateway route, no NAT. Nothing in this VPC can reach
    // out and nothing outside can reach in. Lambdas do not join it; they use the Data API.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [{ name: 'db', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 26 }],
      flowLogs: { reject: { trafficType: ec2.FlowLogTrafficType.REJECT } },
    });

    this.cluster = new rds.DatabaseCluster(this, 'Aurora', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({ version: rds.AuroraPostgresEngineVersion.VER_16_6 }),
      writer: rds.ClusterInstance.serverlessV2('writer', { publiclyAccessible: false }),
      // 0 ACU = auto-pause (needs 16.3+). If af-south-1 refuses 0, check-regions.ps1 shows it
      // and the context value frontdesk:minAcu overrides this (ADR 0004).
      serverlessV2MinCapacity: Number(this.node.tryGetContext('frontdesk:minAcu') ?? 0),
      serverlessV2MaxCapacity: prod ? 4 : 2,
      defaultDatabaseName: this.databaseName,
      credentials: rds.Credentials.fromGeneratedSecret('frontdesk_admin', { secretName: `frontdesk/${props.envName}/db-admin`, encryptionKey: this.key }),
      enableDataApi: true,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      storageEncrypted: true,
      storageEncryptionKey: this.key,
      iamAuthentication: true,
      deletionProtection: prod,
      backup: { retention: Duration.days(prod ? 14 : 7), preferredWindow: '01:00-02:00' },
      cloudwatchLogsExports: ['postgresql'],
      cloudwatchLogsRetention: 14,
      removalPolicy: prod ? RemovalPolicy.SNAPSHOT : RemovalPolicy.DESTROY,
      parameters: {
        // Statement-level logging of DDL and role changes; not of data-bearing DML.
        'log_statement': 'ddl',
        'rds.force_ssl': '1',
      },
    });

    // The non-owner role Lambdas use. The migrate Lambda sets its password from this secret.
    this.appSecret = new secretsmanager.Secret(this, 'AppDbSecret', {
      secretName: `frontdesk/${props.envName}/db-app`,
      encryptionKey: this.key,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'frontdesk_app', dbname: this.databaseName }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 40,
      },
    });

    const logs = new s3.Bucket(this, 'AccessLogs', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [{ expiration: Duration.days(90) }],
      removalPolicy: retain,
      autoDeleteObjects: !prod,
    });
    const bucket = (cid: string, prefix: string) => new s3.Bucket(this, cid, {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      serverAccessLogsBucket: logs,
      serverAccessLogsPrefix: `${prefix}/`,
      removalPolicy: retain,
      autoDeleteObjects: !prod,
    });
    this.docsBucket = bucket('Docs', 'docs');
    this.analyticsBucket = bucket('Analytics', 'analytics');

    NagSuppressions.addResourceSuppressions(logs, [{ id: 'AwsSolutions-S1', reason: 'Access-log bucket; logging it to itself would loop.' }]);
    NagSuppressions.addResourceSuppressions(this.cluster, [
      { id: 'AwsSolutions-RDS6', reason: 'IAM auth is enabled; Lambdas use the Data API, which authenticates with IAM plus a Secrets Manager secret.' },
      ...(prod ? [] : [{ id: 'AwsSolutions-RDS10', reason: 'Dev is disposable; prod has deletion protection.' }]),
      { id: 'AwsSolutions-RDS14', reason: 'Backtrack is Aurora MySQL only.' },
      { id: 'AwsSolutions-SMG4', reason: 'Rotation of the admin secret is a Phase 5 runbook item; the secret is used only by the migrate Lambda.' },
    ], true);
    NagSuppressions.addResourceSuppressions(this.appSecret, [
      { id: 'AwsSolutions-SMG4', reason: 'Rotation needs a rotation Lambda in the VPC (NAT or endpoint cost); rotated by the migrate runbook instead (docs/INCIDENT-PLAN.md).' },
    ]);

    // cloudwatchLogsRetention creates a CDK-managed custom resource; its role is CDK's, not ours.
    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-IAM4', reason: 'CDK LogRetention custom resource uses AWSLambdaBasicExecutionRole.', appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'] },
      { id: 'AwsSolutions-IAM5', reason: 'CDK LogRetention custom resource must manage retention on log groups named at deploy time.', appliesTo: ['Resource::*'] },
    ]);

    new CfnOutput(this, 'ClusterArn', { value: this.cluster.clusterArn });
    new CfnOutput(this, 'AppSecretArn', { value: this.appSecret.secretArn });
    new CfnOutput(this, 'AdminSecretArn', { value: this.cluster.secret!.secretArn });
  }
}
