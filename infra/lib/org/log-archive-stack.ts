/**
 * Organization CloudTrail destination, in the log-archive account (ADR 0003, ADR 0007).
 *
 * Object Lock is on from creation (it cannot be added later without AWS support), in
 * governance mode for 365 days. The protect-log-archive SCP denies BypassGovernanceRetention
 * and deletes to everyone except the break-glass role, so in practice the trail cannot be
 * erased by a compromised workload or CI role.
 */
import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

export interface LogArchiveStackProps extends StackProps {
  organizationId: string;
  managementAccountId: string;
}

export class LogArchiveStack extends Stack {
  readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: LogArchiveStackProps) {
    super(scope, id, props);
    const name = `frontdesk-org-trail-${this.account}-${this.region}`;
    const accessLogs = new s3.Bucket(this, 'AccessLogs', {
      bucketName: `frontdesk-org-trail-access-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [{ expiration: Duration.days(90) }],
      removalPolicy: RemovalPolicy.RETAIN,
    });
    NagSuppressions.addResourceSuppressions(accessLogs, [
      { id: 'AwsSolutions-S1', reason: 'This is the access-log bucket; logging it to itself would loop.' },
    ]);
    this.bucket = new s3.Bucket(this, 'OrgTrail', {
      bucketName: name,
      // SSE-S3 rather than KMS: CloudTrail delivery from every account would need key-policy
      // grants per account, and a KMS key is USD 1/month. Object Lock + SCPs carry integrity.
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      objectLockEnabled: true,
      objectLockDefaultRetention: s3.ObjectLockRetention.governance(Duration.days(365)),
      serverAccessLogsBucket: accessLogs,
      lifecycleRules: [{ transitions: [{ storageClass: s3.StorageClass.GLACIER_INSTANT_RETRIEVAL, transitionAfter: Duration.days(90) }] }],
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const trailArn = `arn:aws:cloudtrail:*:${props.managementAccountId}:trail/frontdesk-org-trail`;
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'CloudTrailAclCheck',
      principals: [new iam.ServicePrincipal('cloudtrail.amazonaws.com')],
      actions: ['s3:GetBucketAcl'],
      resources: [this.bucket.bucketArn],
      conditions: { StringLike: { 'aws:SourceArn': trailArn } },
    }));
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'CloudTrailWriteOrgPrefix',
      principals: [new iam.ServicePrincipal('cloudtrail.amazonaws.com')],
      actions: ['s3:PutObject'],
      resources: [
        this.bucket.arnForObjects(`AWSLogs/${props.managementAccountId}/*`),
        this.bucket.arnForObjects(`AWSLogs/${props.organizationId}/*`),
      ],
      conditions: {
        StringEquals: { 's3:x-amz-acl': 'bucket-owner-full-control' },
        StringLike: { 'aws:SourceArn': trailArn },
      },
    }));
  }
}
