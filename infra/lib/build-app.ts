/**
 * Builds the whole CDK app. bin/app.ts calls this; tests call it with CI context so they
 * assert on exactly the templates that deploy.
 */
import { App, AppProps, Aspects, Stack } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { AwsSolutionsChecks } from 'cdk-nag';
import { accounts, AI_REGION, ctx, EnvName, GLOBAL_REGION, HOME_REGION } from './shared/config';
import { GithubOidcStack, BOUNDARY_NAME } from './shared/github-oidc-stack';
import { OrganizationStack } from './org/organization-stack';
import { BudgetsStack } from './org/budgets-stack';
import { LogArchiveStack } from './org/log-archive-stack';
import { OrgTrailStack } from './org/org-trail-stack';
import { SecurityStack } from './org/security-stack';
import { AiStack } from './workload/ai-stack';
import { DataStack } from './workload/data-stack';
import { WebStack } from './workload/web-stack';
import { AppStack } from './workload/app-stack';
import { DetectionStack } from './workload/detection-stack';


export function buildApp(props?: AppProps): App {
  const app = new App(props);

  const ci = ctx(app, 'frontdesk:ci') === 'true';
  const ph = (v: string | undefined, placeholder: string) => v ?? (ci ? placeholder : undefined);
  const acc = accounts(app);
  const repo = ctx(app, 'frontdesk:githubRepo', 'machetheDM/front-desk')!;
  const alertEmail = ph(ctx(app, 'alertEmail'), 'alerts@example.invalid');

  const management = ph(acc.management, '111111111111');
  if (management && alertEmail) {
    const rootId = ph(ctx(app, 'rootId'), 'r-abcd');
    const orgId = ph(ctx(app, 'organizationId'), 'o-abcdefghij');
    const emails = {
      security: ph(ctx(app, 'emailSecurity'), 'security@example.invalid'),
      logArchive: ph(ctx(app, 'emailLogArchive'), 'logs@example.invalid'),
      dev: ph(ctx(app, 'emailDev'), 'dev@example.invalid'),
      prod: ph(ctx(app, 'emailProd'), 'prod@example.invalid'),
    };
    const env = { account: management, region: GLOBAL_REGION };
    if (rootId && emails.security && emails.logArchive && emails.dev && emails.prod) {
      new OrganizationStack(app, 'FrontDesk-Organization', { env, rootId, accountEmails: emails as any });
    }
    new BudgetsStack(app, 'FrontDesk-Budgets', { env, alertEmail });
    const logArchive = ph(acc.logArchive, '222222222222');
    if (logArchive && orgId) {
      const la = new LogArchiveStack(app, 'FrontDesk-LogArchive', {
        env: { account: logArchive, region: HOME_REGION }, organizationId: orgId, managementAccountId: management,
      });
      new OrgTrailStack(app, 'FrontDesk-OrgTrail', { env, bucketName: la.bucket.bucketName, alertEmail });
    }
    const security = ph(acc.security, '333333333333');
    if (security) new SecurityStack(app, 'FrontDesk-Security', { env: { account: security, region: HOME_REGION }, alertEmail });
  }

  for (const envName of ['dev', 'prod'] as EnvName[]) {
    const account = ph(envName === 'dev' ? acc.dev : acc.prod, envName === 'dev' ? '444444444444' : '555555555555');
    if (!account) continue;
    const name = (s: string) => `FrontDesk-${envName}-${s}`;
    const home = { account, region: HOME_REGION };
    new GithubOidcStack(app, name('GithubOidc'), { env: home, repo, envName });

    const ai = new AiStack(app, name('Ai'), { env: { account, region: AI_REGION }, envName, crossRegionReferences: true });
    const data = new DataStack(app, name('Data'), { env: home, envName });
    const web = new WebStack(app, name('Web'), { env: home, envName });
    const signedKey = ctx(app, 'frontdesk:signedCodeKey');
    const signedBucket = ctx(app, 'frontdesk:signedCodeBucket');
    // Prod needs signed code; in CI synth a placeholder key stands in for the signing job output.
    const signedCode = signedKey && signedBucket ? { bucketName: signedBucket, key: signedKey, objectVersion: ctx(app, 'frontdesk:signedCodeVersion') }
      : envName === 'prod' && ci ? { bucketName: 'frontdesk-prod-signed-placeholder', key: 'signed/lambda.zip', objectVersion: 'ci-placeholder' } : undefined;
    const appStack = new AppStack(app, name('App'), {
      env: home, envName, data, crossRegionReferences: true,
      guardrailId: ai.guardrailId, guardrailVersion: ai.guardrailVersion,
      webBucket: web.bucket, webDomain: web.distribution.distributionDomainName,
      alertEmail,
      modelSmall: ctx(app, 'frontdesk:modelSmall', 'eu.anthropic.claude-haiku-5-5')!,
      modelLarge: ctx(app, 'frontdesk:modelLarge', 'eu.anthropic.claude-sonnet-5-5')!,
      signedCode,
    });
    new DetectionStack(app, name('Detection'), { env: home, envName, alarmTopic: appStack.alarmTopic });

    // Every role in the workload stacks carries the boundary created by the OIDC stack.
    for (const s of [ai, data, web, appStack]) applyBoundary(s);
  }


  Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
  return app;
}

function applyBoundary(stack: Stack): void {
  iam.PermissionsBoundary.of(stack).apply(iam.ManagedPolicy.fromManagedPolicyName(stack, 'WorkloadBoundary', BOUNDARY_NAME));
}
