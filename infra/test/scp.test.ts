import { ATTACHMENTS, loadScp, ScpName } from '../lib/org/scp';
import { deniedBy, Req } from './helpers/scp-eval';

const all = (names: ScpName[]) => Object.fromEntries(names.map((n) => [n, loadScp(n)]));
// What a workload account (dev/prod) inherits: root attachments plus the Workloads OU.
const workload = all([...ATTACHMENTS.root, ...ATTACHMENTS.workloads]);
const security = all([...ATTACHMENTS.root, ...ATTACHMENTS.security]);
const role = (name: string, account = '444444444444') => `arn:aws:iam::${account}:role/${name}`;
const app = role('frontdesk-dev-answer-role');
const req = (r: Partial<Req>): Req => ({ action: 's3:GetObject', region: 'af-south-1', principalArn: app, ...r });

describe('service control policies', () => {
  test('each target carries at most four of our SCPs, leaving room for FullAWSAccess', () => {
    for (const names of Object.values(ATTACHMENTS)) expect(names.length).toBeLessThanOrEqual(4);
  });

  test('every SCP fits the 5,120 character limit', () => {
    for (const n of Object.values(ATTACHMENTS).flat()) expect(JSON.stringify(loadScp(n)).length).toBeLessThanOrEqual(5120);
  });

  test('normal work in Cape Town is allowed', () => {
    expect(deniedBy(workload, req({ action: 'rds-data:ExecuteStatement' }))).toEqual([]);
    expect(deniedBy(workload, req({ action: 'lambda:InvokeFunction' }))).toEqual([]);
  });

  test('anything in an unapproved Region is denied', () => {
    expect(deniedBy(workload, req({ action: 'ec2:RunInstances', region: 'us-west-2' }))).toContain('deny-unapproved-regions:DenyOutsideCapeTownAndFrankfurt');
    expect(deniedBy(workload, req({ action: 's3:PutObject', region: 'eu-west-1' }))).not.toEqual([]);
  });

  test('global services keep working from us-east-1', () => {
    expect(deniedBy(workload, req({ action: 'iam:GetRole', region: 'us-east-1' }))).toEqual([]);
    expect(deniedBy(workload, req({ action: 'cloudfront:CreateInvalidation', region: 'us-east-1' }))).toEqual([]);
  });

  test('Bedrock in Cape Town is denied, so Global routing from af-south-1 cannot happen', () => {
    expect(deniedBy(workload, req({ action: 'bedrock:InvokeModel', region: 'af-south-1' }))).toContain('frankfurt-is-for-bedrock:ModelInvocationOnlyInEuRegions');
  });

  test('a Global inference profile is denied even when called from Frankfurt', () => {
    const r = req({ action: 'bedrock:InvokeModel', region: 'eu-central-1', resource: 'arn:aws:bedrock:eu-central-1:444444444444:inference-profile/global.anthropic.claude-haiku-5-5' });
    expect(deniedBy(workload, r)).toContain('frankfurt-is-for-bedrock:NoGlobalInferenceProfiles');
  });

  test('Bedrock model calls in Frankfurt, and EU geo routing targets, are allowed', () => {
    const r = (region: string) => req({ action: 'bedrock:InvokeModel', region, resource: 'arn:aws:bedrock:eu-central-1:444444444444:inference-profile/eu.anthropic.claude-haiku-5-5' });
    expect(deniedBy(workload, r('eu-central-1'))).toEqual([]);
    expect(deniedBy(workload, r('eu-west-3'))).toEqual([]);
  });

  test('customer-data services cannot be created in Frankfurt', () => {
    expect(deniedBy(workload, req({ action: 'rds:CreateDBCluster', region: 'eu-central-1' }))).toContain('frankfurt-is-for-bedrock:NoCustomerDataServicesInFrankfurt');
    expect(deniedBy(workload, req({ action: 's3:PutObject', region: 'eu-central-1', resource: 'arn:aws:s3:::my-data/x' }))).toContain('frankfurt-is-for-bedrock:NoObjectsInFrankfurtExceptCdkAssets');
  });

  test('IAM users and access keys cannot be created anywhere', () => {
    for (const action of ['iam:CreateUser', 'iam:CreateAccessKey', 'iam:CreateLoginProfile']) {
      expect(deniedBy(workload, req({ action, region: 'us-east-1' }))).toContain('deny-long-lived-credentials:NoIamUsersOrAccessKeys');
    }
  });

  test('stopping CloudTrail is denied, except to break-glass', () => {
    expect(deniedBy(workload, req({ action: 'cloudtrail:StopLogging' }))).toContain('protect-audit-trail:NoTamperingWithTrailsAnalyzersOrAuditLogs');
    expect(deniedBy(workload, req({ action: 'cloudtrail:StopLogging', principalArn: role('FrontDeskBreakGlass') }))).toEqual([]);
  });

  test('audit log groups cannot be deleted', () => {
    const r = req({ action: 'logs:DeleteLogGroup', resource: 'arn:aws:logs:af-south-1:444444444444:log-group:/frontdesk/audit/inbox' });
    expect(deniedBy(workload, r)).toContain('protect-audit-trail:NoDeletingAuditLogGroups');
  });

  test('member-account root is denied everything', () => {
    expect(deniedBy(workload, req({ action: 's3:ListBucket', principalArn: 'arn:aws:iam::444444444444:root' }))).toContain('deny-root-and-leaving:NoMemberAccountRoot');
  });

  test('no account can leave the organization or change its Regions', () => {
    expect(deniedBy(workload, req({ action: 'organizations:LeaveOrganization', region: 'us-east-1' }))).toContain('deny-root-and-leaving:NoLeavingOrChangingRegions');
    expect(deniedBy(workload, req({ action: 'account:DisableRegion', region: 'us-east-1' }))).toContain('deny-root-and-leaving:NoLeavingOrChangingRegions');
  });

  test('CloudTrail archive objects cannot be deleted or unlocked, even by the CDK deploy role', () => {
    const cdk = role('cdk-hnb659fds-cfn-exec-role-222222222222-af-south-1', '222222222222');
    for (const action of ['s3:DeleteObject', 's3:BypassGovernanceRetention', 's3:PutObjectRetention']) {
      expect(deniedBy(security, { action, region: 'af-south-1', principalArn: cdk, resource: 'arn:aws:s3:::frontdesk-org-trail-222222222222-af-south-1/AWSLogs/x' }))
        .toContain('protect-log-archive:LogsAreNeverDeletedOrUnlocked');
    }
  });

  test('the CDK deploy role may manage the archive bucket settings, nobody else may', () => {
    const bucket = 'arn:aws:s3:::frontdesk-org-trail-222222222222-af-south-1';
    const cdk = role('cdk-hnb659fds-cfn-exec-role-222222222222-af-south-1', '222222222222');
    expect(deniedBy(security, { action: 's3:PutBucketPolicy', region: 'af-south-1', principalArn: cdk, resource: bucket })).toEqual([]);
    expect(deniedBy(security, { action: 's3:PutBucketPolicy', region: 'af-south-1', principalArn: role('someone', '222222222222'), resource: bucket }))
      .toContain('protect-log-archive:BucketSettingsOnlyThroughCdk');
  });

  test('the evaluator refuses operators it does not understand', () => {
    const bad = { x: { Statement: [{ Sid: 'S', Effect: 'Deny', Action: '*', Resource: '*', Condition: { NumericLessThan: { 'aws:RequestedRegion': '1' } } }] } };
    expect(() => deniedBy(bad, req({}))).toThrow(/not supported/);
  });
});
