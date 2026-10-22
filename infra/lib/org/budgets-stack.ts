/**
 * Spend alarms (Phase 0). In the management (payer) account, so they see the whole
 * organization's consolidated bill. Budgets bill in USD; the limit is derived from the R300 rule.
 */
import { Stack, StackProps } from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import { Construct } from 'constructs';
import { MONTHLY_BUDGET_ZAR, ZAR_PER_USD } from '../shared/config';

export interface BudgetsStackProps extends StackProps {
  alertEmail: string;
}

export function monthlyLimitUsd(): number {
  return Math.round((MONTHLY_BUDGET_ZAR / ZAR_PER_USD) * 100) / 100;
}

export class BudgetsStack extends Stack {
  constructor(scope: Construct, id: string, props: BudgetsStackProps) {
    super(scope, id, props);
    const subscribers = [{ subscriptionType: 'EMAIL', address: props.alertEmail }];
    const limit = monthlyLimitUsd();

    new budgets.CfnBudget(this, 'Monthly', {
      budget: {
        budgetName: 'frontdesk-monthly-total',
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: limit, unit: 'USD' },
      },
      notificationsWithSubscribers: [
        // R150, R240, R300 actual, and R300 forecast: the forecast warns mid-month.
        { notification: { notificationType: 'ACTUAL', comparisonOperator: 'GREATER_THAN', threshold: 50, thresholdType: 'PERCENTAGE' }, subscribers },
        { notification: { notificationType: 'ACTUAL', comparisonOperator: 'GREATER_THAN', threshold: 80, thresholdType: 'PERCENTAGE' }, subscribers },
        { notification: { notificationType: 'ACTUAL', comparisonOperator: 'GREATER_THAN', threshold: 100, thresholdType: 'PERCENTAGE' }, subscribers },
        { notification: { notificationType: 'FORECASTED', comparisonOperator: 'GREATER_THAN', threshold: 100, thresholdType: 'PERCENTAGE' }, subscribers },
      ],
    });

    // No separate Bedrock budget: Claude on Bedrock is billed as AWS Marketplace line items,
    // which a `Service = Amazon Bedrock` filter does not catch, so such a budget would stay
    // silent while the real spend grew. Model spend is capped in the app instead (per-tenant
    // monthly token cap, frontdesk/llm/metering.py) and alarmed on the app's own cost metric.
  }
}
