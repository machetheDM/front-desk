/**
 * Organization layout (ADR 0003). Deployed once, in the management account, in us-east-1
 * (Organizations is a global service homed there).
 *
 * The organization itself and the root id come from the owner's one-off CLI steps in
 * docs/DEPLOY.md; CloudFormation can manage OUs, accounts and policies but not create the
 * organization.
 */
import { CfnOutput, RemovalPolicy, Stack, StackProps, Tags } from 'aws-cdk-lib';
import * as organizations from 'aws-cdk-lib/aws-organizations';
import { Construct } from 'constructs';
import { ATTACHMENTS, loadScp, ScpName } from './scp';

export interface OrganizationStackProps extends StackProps {
  rootId: string;
  /** e-mail addresses for the member accounts; plus-addressing on one inbox works. */
  accountEmails: { security: string; logArchive: string; dev: string; prod: string };
}

export class OrganizationStack extends Stack {
  readonly ouIds: Record<string, string> = {};

  constructor(scope: Construct, id: string, props: OrganizationStackProps) {
    super(scope, id, props);

    const security = new organizations.CfnOrganizationalUnit(this, 'SecurityOu', { name: 'Security', parentId: props.rootId });
    const workloads = new organizations.CfnOrganizationalUnit(this, 'WorkloadsOu', { name: 'Workloads', parentId: props.rootId });
    const devOu = new organizations.CfnOrganizationalUnit(this, 'DevOu', { name: 'Dev', parentId: workloads.attrId });
    const prodOu = new organizations.CfnOrganizationalUnit(this, 'ProdOu', { name: 'Prod', parentId: workloads.attrId });

    const account = (cid: string, name: string, email: string, parent: string) => {
      const a = new organizations.CfnAccount(this, cid, { accountName: name, email, parentIds: [parent] });
      // An account removed from this template must not be closed by accident: closing is a
      // 90-day, mostly irreversible operation. Retain means CloudFormation forgets it instead.
      a.applyRemovalPolicy(RemovalPolicy.RETAIN);
      return a;
    };
    const securityAccount = account('SecurityAccount', 'frontdesk-security', props.accountEmails.security, security.attrId);
    const logArchive = account('LogArchiveAccount', 'frontdesk-log-archive', props.accountEmails.logArchive, security.attrId);
    const dev = account('DevAccount', 'frontdesk-dev', props.accountEmails.dev, devOu.attrId);
    const prod = account('ProdAccount', 'frontdesk-prod', props.accountEmails.prod, prodOu.attrId);

    const targets: Record<keyof typeof ATTACHMENTS, string> = {
      root: props.rootId,
      security: security.attrId,
      workloads: workloads.attrId,
    };
    const byPolicy = new Map<ScpName, string[]>();
    for (const [target, names] of Object.entries(ATTACHMENTS) as [keyof typeof ATTACHMENTS, ScpName[]][]) {
      for (const n of names) byPolicy.set(n, [...(byPolicy.get(n) ?? []), targets[target]]);
    }
    for (const [name, targetIds] of byPolicy) {
      new organizations.CfnPolicy(this, `Scp-${name}`, {
        name: `frontdesk-${name}`,
        description: `Front Desk SCP ${name} (infra/policies/scp/${name}.json, ADR 0003)`,
        type: 'SERVICE_CONTROL_POLICY',
        content: loadScp(name),
        targetIds,
      });
    }

    Tags.of(this).add('project', 'front-desk');
    new CfnOutput(this, 'SecurityAccountId', { value: securityAccount.attrAccountId });
    new CfnOutput(this, 'LogArchiveAccountId', { value: logArchive.attrAccountId });
    new CfnOutput(this, 'DevAccountId', { value: dev.attrAccountId });
    new CfnOutput(this, 'ProdAccountId', { value: prod.attrAccountId });
    this.ouIds.security = security.attrId;
    this.ouIds.workloads = workloads.attrId;
  }
}
