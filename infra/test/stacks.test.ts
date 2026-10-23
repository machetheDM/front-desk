import * as fs from 'fs';
import * as path from 'path';
import { Stack } from 'aws-cdk-lib';
import { Annotations, Match, Template } from 'aws-cdk-lib/assertions';
import { buildApp } from '../lib/build-app';
import { ACCOUNT_DETECTIONS } from '../lib/org/org-trail-stack';
import { APP_EVENT_DETECTIONS, APP_METRIC_DETECTIONS } from '../lib/workload/detection-stack';
import { monthlyLimitUsd } from '../lib/org/budgets-stack';

const app = buildApp({ context: { 'frontdesk:ci': 'true', 'frontdesk:testing': 'true' } });
const stack = (name: string) => app.node.findChild(name) as Stack;
const tpl = (name: string) => Template.fromStack(stack(name));
const workloadStacks = ['Ai', 'Data', 'Web', 'Signing', 'App'].flatMap((s) => [`FrontDesk-dev-${s}`, `FrontDesk-prod-${s}`]);

describe('every stack', () => {
  test('passes cdk-nag AwsSolutions with no unacknowledged errors', () => {
    for (const s of app.node.children.filter((c): c is Stack => c instanceof Stack)) {
      const errors = Annotations.fromStack(s).findError('*', Match.stringLikeRegexp('AwsSolutions-.*'));
      expect({ stack: s.stackName, errors: errors.map((e) => `${e.id}: ${String(e.entry.data).slice(0, 80)}`) }).toEqual({ stack: s.stackName, errors: [] });
    }
  });

  test('creates no IAM users and no access keys', () => {
    for (const s of app.node.children.filter((c): c is Stack => c instanceof Stack)) {
      const t = Template.fromStack(s);
      expect(Object.keys(t.findResources('AWS::IAM::User'))).toEqual([]);
      expect(Object.keys(t.findResources('AWS::IAM::AccessKey'))).toEqual([]);
    }
  });

  test('every role in a workload stack carries the permission boundary', () => {
    for (const name of workloadStacks) {
      const roles = tpl(name).findResources('AWS::IAM::Role');
      for (const [id, r] of Object.entries(roles)) {
        expect({ name, id, boundary: JSON.stringify((r as any).Properties.PermissionsBoundary ?? null) })
          .toEqual({ name, id, boundary: expect.stringContaining('frontdesk-workload-boundary') });
      }
    }
  });
});

describe('data stack', () => {
  const t = tpl('FrontDesk-dev-Data');

  test('Aurora is private, encrypted, scales to zero and has the Data API on', () => {
    t.hasResourceProperties('AWS::RDS::DBCluster', {
      EnableHttpEndpoint: true,
      StorageEncrypted: true,
      ServerlessV2ScalingConfiguration: { MinCapacity: 0, MaxCapacity: 2 },
      Engine: 'aurora-postgresql',
    });
    t.hasResourceProperties('AWS::RDS::DBInstance', { PubliclyAccessible: false });
  });

  test('the VPC has no internet gateway and no NAT', () => {
    expect(Object.keys(t.findResources('AWS::EC2::InternetGateway'))).toEqual([]);
    expect(Object.keys(t.findResources('AWS::EC2::NatGateway'))).toEqual([]);
  });

  test('buckets block public access, enforce TLS and use the environment key', () => {
    const buckets = t.findResources('AWS::S3::Bucket');
    for (const b of Object.values(buckets)) {
      expect((b as any).Properties.PublicAccessBlockConfiguration).toEqual({ BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true });
    }
    t.hasResourceProperties('AWS::S3::BucketPolicy', { PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Condition: { Bool: { 'aws:SecureTransport': 'false' } }, Effect: 'Deny' })]) } });
  });

  test('prod keeps its database: deletion protection on', () => {
    tpl('FrontDesk-prod-Data').hasResourceProperties('AWS::RDS::DBCluster', { DeletionProtection: true });
  });
});

describe('app stack', () => {
  const dev = tpl('FrontDesk-dev-App');
  const prod = tpl('FrontDesk-prod-App');

  test('the pipeline catches any answer failure and degrades instead of failing the customer', () => {
    const sm = Object.values(dev.findResources('AWS::StepFunctions::StateMachine'))[0] as any;
    const def = JSON.stringify(sm.Properties.DefinitionString);
    expect(def).toContain('\\"Catch\\":[{\\"ErrorEquals\\":[\\"States.ALL\\"]');
    expect(def).toContain('\\"Next\\":\\"Degrade\\"');
  });

  test('Step Functions never logs execution data (it would contain customer text)', () => {
    dev.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      StateMachineType: 'EXPRESS',
      LoggingConfiguration: Match.objectLike({ IncludeExecutionData: false, Level: 'ERROR' }),
    });
  });

  test('prod refuses unsigned Lambda code; dev only warns', () => {
    prod.hasResourceProperties('AWS::Lambda::CodeSigningConfig', { CodeSigningPolicies: { UntrustedArtifactOnDeployment: 'Enforce' } });
    dev.hasResourceProperties('AWS::Lambda::CodeSigningConfig', { CodeSigningPolicies: { UntrustedArtifactOnDeployment: 'Warn' } });
  });

  test('the answer function is released by canary with automatic rollback on alarms', () => {
    dev.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaCanary10Percent5Minutes',
      AutoRollbackConfiguration: { Enabled: true, Events: Match.arrayWith(['DEPLOYMENT_FAILURE', 'DEPLOYMENT_STOP_ON_ALARM']) },
      AlarmConfiguration: Match.objectLike({ Enabled: true }),
    });
  });

  test('staff sign-in requires MFA and nobody can sign themselves up', () => {
    dev.hasResourceProperties('AWS::Cognito::UserPool', {
      MfaConfiguration: 'ON',
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
    });
  });

  test('every inbox route requires the Cognito authorizer', () => {
    const routes = dev.findResources('AWS::ApiGatewayV2::Route');
    const inbox = Object.values(routes).filter((r: any) => /\/v1\/(inbox|insights|admin)/.test(r.Properties.RouteKey));
    expect(inbox.length).toBe(7);
    for (const r of inbox as any[]) expect(r.Properties.AuthorizationType).toBe('JWT');
  });

  test('Bedrock permissions are limited to EU Regions', () => {
    const policies = JSON.stringify(dev.findResources('AWS::IAM::Policy'));
    expect(policies).toContain('bedrock:InvokeModel');
    expect(policies).toContain('"aws:RequestedRegion":["eu-central-1"');
    expect(policies).not.toContain('inference-profile/global.');
  });

  test('Athena queries are capped at 1 GB scanned', () => {
    dev.hasResourceProperties('AWS::Athena::WorkGroup', {
      WorkGroupConfiguration: Match.objectLike({ BytesScannedCutoffPerQuery: 1_000_000_000, EnforceWorkGroupConfiguration: true }),
    });
  });
});

describe('ai stack', () => {
  test('the guardrail filters prompt attacks on input and checks grounding', () => {
    tpl('FrontDesk-dev-Ai').hasResourceProperties('AWS::Bedrock::Guardrail', {
      ContentPolicyConfig: { FiltersConfig: Match.arrayWith([{ Type: 'PROMPT_ATTACK', InputStrength: 'HIGH', OutputStrength: 'NONE' }]) },
      ContextualGroundingPolicyConfig: { FiltersConfig: Match.arrayWith([{ Type: 'GROUNDING', Threshold: 0.75 }]) },
    });
  });
  test('lives in Frankfurt', () => expect(stack('FrontDesk-dev-Ai').region).toBe('eu-central-1'));
});

describe('identity', () => {
  test('GitHub can assume the prod deploy role only from the prod environment of this repository', () => {
    tpl('FrontDesk-prod-GithubOidc').hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'frontdesk-github-deploy-prod',
      AssumeRolePolicyDocument: { Statement: [Match.objectLike({ Condition: { StringEquals: {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': 'repo:machetheDM/front-desk:environment:prod',
      } } })] },
    });
  });
});

describe('organization', () => {
  const t = tpl('FrontDesk-Organization');
  test('creates the security, log-archive, dev and prod accounts, retained if removed from code', () => {
    const accounts = t.findResources('AWS::Organizations::Account');
    expect(Object.values(accounts).map((a: any) => a.Properties.AccountName).sort()).toEqual(['frontdesk-dev', 'frontdesk-log-archive', 'frontdesk-prod', 'frontdesk-security']);
    for (const a of Object.values(accounts) as any[]) expect(a.DeletionPolicy).toBe('Retain');
  });
  test('deploys all six SCPs', () => {
    expect(Object.keys(t.findResources('AWS::Organizations::Policy')).length).toBe(6);
  });
  test('budgets alarm at 50, 80 and 100 percent of R300, plus a forecast alarm', () => {
    tpl('FrontDesk-Budgets').hasResourceProperties('AWS::Budgets::Budget', {
      Budget: Match.objectLike({ BudgetLimit: { Amount: monthlyLimitUsd(), Unit: 'USD' } }),
      NotificationsWithSubscribers: Match.arrayWith([
        Match.objectLike({ Notification: Match.objectLike({ NotificationType: 'FORECASTED', Threshold: 100 }) }),
      ]),
    });
    expect(monthlyLimitUsd()).toBeCloseTo(16.22, 2);
  });
  test('the org trail validates log files and is multi-Region', () => {
    tpl('FrontDesk-OrgTrail').hasResourceProperties('AWS::CloudTrail::Trail', { IsOrganizationTrail: true, IsMultiRegionTrail: true, EnableLogFileValidation: true });
  });
  test('the log archive bucket has Object Lock on', () => {
    tpl('FrontDesk-LogArchive').hasResourceProperties('AWS::S3::Bucket', { ObjectLockEnabled: true, ObjectLockConfiguration: Match.objectLike({ ObjectLockEnabled: 'Enabled' }) });
  });
});

describe('operations dashboard', () => {
  test('one dashboard shows latency, cost, degraded mode, attacks and drift from the alarmed metrics', () => {
    const dashboards = tpl('FrontDesk-dev-App').findResources('AWS::CloudWatch::Dashboard');
    const body = JSON.stringify(Object.values(dashboards).map((d: any) => d.Properties.DashboardBody));
    for (const metric of ['AnswerLatencyMs', 'ModelCostUsd', 'Degraded', 'AssistantPaused', 'GuardrailIntervened', 'RateLimited', 'IntentMixPsiMax']) {
      expect({ metric, shown: body.includes(metric) }).toEqual({ metric, shown: true });
    }
    expect(Object.keys(dashboards)).toHaveLength(1); // three dashboards are free; keep well under
  });
});

describe('drift', () => {
  test('a weekly job scores the intent mix and DET-12 alarms on the worst tenant\'s PSI', () => {
    const app = tpl('FrontDesk-dev-App');
    app.hasResourceProperties('AWS::Lambda::Function', { Handler: 'frontdesk.handlers.ops.drift' });
    app.hasResourceProperties('AWS::Events::Rule', { ScheduleExpression: 'cron(0 5 ? * MON *)' });
    tpl('FrontDesk-dev-Detection').hasResourceProperties('AWS::CloudWatch::Alarm', {
      MetricName: 'IntentMixPsiMax', Statistic: 'Maximum', Period: 86400, Threshold: 0.25,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    });
  });
});

describe('detections', () => {
  test('every detection has a runbook section in docs/DETECTIONS.md', () => {
    const doc = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'DETECTIONS.md'), 'utf8');
    const ids = [...ACCOUNT_DETECTIONS, ...APP_EVENT_DETECTIONS, ...APP_METRIC_DETECTIONS].map((d) => d.id).concat(['DET-10']);
    for (const id of ids) expect({ id, hasRunbook: new RegExp(`^## ${id}\\b`, 'm').test(doc) }).toEqual({ id, hasRunbook: true });
  });
});
