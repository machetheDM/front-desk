/**
 * Code signing (AWS Signer) for Lambda, in its own stack because it must exist before the App
 * stack: the deploy pipeline uploads the package here, signs it, and only then deploys code
 * that prod's CodeSigningConfig will accept.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as signer from 'aws-cdk-lib/aws-signer';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { EnvName } from '../shared/config';

export interface SigningStackProps extends StackProps {
  envName: EnvName;
}

export class SigningStack extends Stack {
  readonly profile: signer.SigningProfile;
  readonly artifacts: s3.Bucket;

  constructor(scope: Construct, id: string, props: SigningStackProps) {
    super(scope, id, props);
    this.profile = new signer.SigningProfile(this, 'Profile', {
      platform: signer.Platform.AWS_LAMBDA_SHA384_ECDSA,
      signatureValidity: Duration.days(365),
    });
    this.artifacts = new s3.Bucket(this, 'Artifacts', {
      bucketName: `frontdesk-${props.envName}-signing-${this.account}-${this.region}`,
      // Signer requires a versioned source bucket.
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(90) }],
      removalPolicy: props.envName === 'prod' ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      autoDeleteObjects: props.envName !== 'prod',
    });
    NagSuppressions.addResourceSuppressions(this.artifacts, [{ id: 'AwsSolutions-S1', reason: 'Build artefacts only; every upload and signing job is in CloudTrail.' }]);
    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-IAM4', reason: 'CDK auto-delete custom resource (dev only).', appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'] },
      { id: 'AwsSolutions-IAM5', reason: 'CDK auto-delete custom resource (dev only).' },
      { id: 'AwsSolutions-L1', reason: 'CDK-managed custom resource runtime.' },
    ]);
    new CfnOutput(this, 'SigningProfileName', { value: this.profile.signingProfileName });
    new CfnOutput(this, 'ArtifactsBucket', { value: this.artifacts.bucketName });
  }
}
