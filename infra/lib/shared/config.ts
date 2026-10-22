/**
 * Deployment configuration. Account ids and e-mail addresses are passed as CDK context at
 * deploy time (docs/DEPLOY.md), never committed: this repository is public.
 */
import { App } from 'aws-cdk-lib';

export const HOME_REGION = 'af-south-1';
export const AI_REGION = 'eu-central-1';
export const GLOBAL_REGION = 'us-east-1';

export type EnvName = 'dev' | 'prod';

export interface Accounts {
  management?: string;
  security?: string;
  logArchive?: string;
  dev?: string;
  prod?: string;
}

export function ctx(app: App, key: string, fallback?: string): string | undefined {
  const v = app.node.tryGetContext(key);
  return v === undefined || v === '' ? fallback : String(v);
}

export function accounts(app: App): Accounts {
  return {
    management: ctx(app, 'managementAccount'),
    security: ctx(app, 'securityAccount'),
    logArchive: ctx(app, 'logArchiveAccount'),
    dev: ctx(app, 'devAccount'),
    prod: ctx(app, 'prodAccount'),
  };
}

/** The ZAR budget is the rule; Budgets bills in USD, so the conversion is explicit. */
export const ZAR_PER_USD = 18.5;
export const MONTHLY_BUDGET_ZAR = 300;
