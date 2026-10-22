/**
 * GitHub Actions to AWS without stored keys. One of these per member account (dev, prod),
 * deployed by the owner once with Identity Center credentials (docs/DEPLOY.md step 7).
 *
 * Trust is pinned to this repository and to a GitHub *environment*, not just the repo: the
 * prod environment has required reviewers, so a workflow on a feature branch cannot obtain
 * the prod role even though it runs in the same repository.
 *
 * Also creates the permission boundary every workload role carries.
 */
import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { AI_REGION, EnvName } from './config';

export const BOUNDARY_NAME = 'frontdesk-workload-boundary';
const EU_INVOKE_REGIONS = ['eu-central-1', 'eu-north-1', 'eu-south-1', 'eu-south-2', 'eu-west-1', 'eu-west-3'];

export interface GithubOidcStackProps extends StackProps {
  repo: string; // owner/name
  envName: EnvName;
  /** Create the OIDC provider (once per account). False if it already exists. */
  createProvider?: boolean;
}

export function boundaryDocument(): iam.PolicyDocument {
  return new iam.PolicyDocument({
    statements: [
      new iam.PolicyStatement({
        sid: 'ServicesFrontDeskUses',
        actions: [
          'logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:PutRetentionPolicy', 'logs:DescribeLogStreams', 'cloudwatch:PutMetricData', 'xray:PutTraceSegments', 'xray:PutTelemetryRecords',
          's3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket', 's3:GetBucketLocation', 's3:AbortMultipartUpload',
          'kms:Decrypt', 'kms:Encrypt', 'kms:GenerateDataKey*', 'kms:DescribeKey',
          'rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement', 'rds-data:BeginTransaction', 'rds-data:CommitTransaction', 'rds-data:RollbackTransaction', 'secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret',
          'ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath', 'ssm:PutParameter', 'ssm:DeleteParameter',
          'bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Rerank', 'bedrock:ApplyGuardrail',
          'bedrock:GetInferenceProfile',
          'states:StartExecution', 'states:StartSyncExecution',
          'social-messaging:SendWhatsAppMessage', 'social-messaging:GetWhatsAppMessageMedia',
          'sns:Publish', 'events:PutEvents', 'lambda:InvokeFunction', 'lambda:GetFunction',
          'codedeploy:PutLifecycleEventHookExecutionStatus',
          'athena:StartQueryExecution', 'athena:GetQueryExecution', 'athena:GetQueryResults',
          'glue:GetTable', 'glue:GetDatabase', 'glue:GetPartitions', 'glue:UpdateTable',
          'cognito-idp:AdminGetUser', 'cognito-idp:ListUsers',
          'ec2:CreateNetworkInterface', 'ec2:DescribeNetworkInterfaces', 'ec2:DeleteNetworkInterface',
        ],
        resources: ['*'],
      }),
      new iam.PolicyStatement({
        sid: 'NeverIdentityOrOrganization',
        effect: iam.Effect.DENY,
        actions: ['iam:*', 'organizations:*', 'account:*', 'sts:AssumeRole'],
        resources: ['*'],
      }),
      new iam.PolicyStatement({
        sid: 'ModelsOnlyInEu',
        effect: iam.Effect.DENY,
        actions: ['bedrock:*'],
        resources: ['*'],
        conditions: { StringNotEquals: { 'aws:RequestedRegion': EU_INVOKE_REGIONS } },
      }),
    ],
  });
}

export class GithubOidcStack extends Stack {
  constructor(scope: Construct, id: string, props: GithubOidcStackProps) {
    super(scope, id, props);
    const providerArn = `arn:aws:iam::${this.account}:oidc-provider/token.actions.githubusercontent.com`;
    if (props.createProvider !== false) {
      new iam.CfnOIDCProvider(this, 'GithubProvider', {
        url: 'https://token.actions.githubusercontent.com',
        clientIdList: ['sts.amazonaws.com'],
      });
    }
    const federated = new iam.FederatedPrincipal(providerArn, {
      StringEquals: {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': `repo:${props.repo}:environment:${props.envName}`,
      },
    }, 'sts:AssumeRoleWithWebIdentity');

    const boundary = new iam.ManagedPolicy(this, 'WorkloadBoundary', {
      managedPolicyName: BOUNDARY_NAME,
      description: 'Permission boundary on every Front Desk workload role (AGENTS.md).',
      document: boundaryDocument(),
    });
    NagSuppressions.addResourceSuppressions(boundary, [{
      id: 'AwsSolutions-IAM5',
      reason: 'A permission boundary is a ceiling, not a grant: it lists the actions any workload role may ever be given, on any resource. Each role still gets narrow inline grants.',
      appliesTo: ['Resource::*', 'Action::kms:GenerateDataKey*'],
    }]);

    const cdkRoles = ['deploy', 'file-publishing', 'image-publishing', 'lookup'].flatMap((r) =>
      [this.region, AI_REGION, 'us-east-1'].map((reg) => `arn:aws:iam::${this.account}:role/cdk-hnb659fds-${r}-role-${this.account}-${reg}`));

    const deploy = new iam.Role(this, 'DeployRole', {
      roleName: `frontdesk-github-deploy-${props.envName}`,
      assumedBy: federated,
      maxSessionDuration: Duration.hours(1),
      description: 'GitHub Actions deploy: may only assume the CDK bootstrap roles.',
    });
    deploy.addToPolicy(new iam.PolicyStatement({ actions: ['sts:AssumeRole', 'sts:TagSession'], resources: cdkRoles }));
    // Running migrations and the post-deploy smoke test through the Data API.
    deploy.addToPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [`arn:aws:lambda:${this.region}:${this.account}:function:frontdesk-${props.envName}-migrate`,
        `arn:aws:lambda:${this.region}:${this.account}:function:frontdesk-${props.envName}-smoke`],
    }));

    if (props.envName === 'dev') {
      // Pull requests may run `cdk diff` against dev (read-only lookup role) but never deploy.
      const diff = new iam.Role(this, 'PullRequestDiffRole', {
        roleName: 'frontdesk-github-pr-diff',
        assumedBy: new iam.FederatedPrincipal(providerArn, {
          StringEquals: {
            'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
            'token.actions.githubusercontent.com:sub': `repo:${props.repo}:pull_request`,
          },
        }, 'sts:AssumeRoleWithWebIdentity'),
        maxSessionDuration: Duration.hours(1),
      });
      diff.addToPolicy(new iam.PolicyStatement({
        actions: ['sts:AssumeRole'],
        resources: cdkRoles.filter((a) => a.includes('-lookup-role-')),
      }));

      // Bedrock evals on release tags: model invocation in the EU only, nothing else.
      const evals = new iam.Role(this, 'EvalsRole', {
        roleName: 'frontdesk-github-evals',
        assumedBy: new iam.FederatedPrincipal(providerArn, {
          StringEquals: {
            'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
            'token.actions.githubusercontent.com:sub': `repo:${props.repo}:environment:evals`,
          },
        }, 'sts:AssumeRoleWithWebIdentity'),
        maxSessionDuration: Duration.hours(1),
      });
      evals.addToPolicy(new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Rerank', 'bedrock:ApplyGuardrail', 'bedrock:GetInferenceProfile'],
        resources: [
          'arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-5-5*',
          'arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5-5*',
          'arn:aws:bedrock:*::foundation-model/cohere.embed-multilingual-v3',
          'arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0',
          'arn:aws:bedrock:*::foundation-model/cohere.rerank-v3-5:0',
          `arn:aws:bedrock:${AI_REGION}:${this.account}:inference-profile/eu.anthropic.*`,
          `arn:aws:bedrock:${AI_REGION}:${this.account}:guardrail/*`,
        ],
        conditions: { StringEquals: { 'aws:RequestedRegion': EU_INVOKE_REGIONS } },
      }));
      NagSuppressions.addResourceSuppressions(evals, [{ id: 'AwsSolutions-IAM5', reason: 'Model version suffixes vary by Region; scoped to two model families, EU Regions only.' }], true);
      new CfnOutput(this, 'EvalsRoleArn', { value: evals.roleArn });
      new CfnOutput(this, 'PullRequestDiffRoleArn', { value: diff.roleArn });
    }
    new CfnOutput(this, 'DeployRoleArn', { value: deploy.roleArn });
  }
}
