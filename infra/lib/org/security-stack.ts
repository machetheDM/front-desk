/**
 * Security account (delegated administrator): the organization-wide IAM Access Analyzer for
 * external access (free) and DET-10 on its findings.
 */
import { Stack, StackProps } from 'aws-cdk-lib';
import * as accessanalyzer from 'aws-cdk-lib/aws-accessanalyzer';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

export interface SecurityStackProps extends StackProps {
  alertEmail: string;
}

export class SecurityStack extends Stack {
  constructor(scope: Construct, id: string, props: SecurityStackProps) {
    super(scope, id, props);
    // External-access analysis is free. Unused-access analysis is paid and stays off.
    new accessanalyzer.CfnAnalyzer(this, 'OrgAnalyzer', { analyzerName: 'frontdesk-org-external-access', type: 'ORGANIZATION' });
    const topic = new sns.Topic(this, 'Findings', { topicName: 'frontdesk-access-findings', enforceSSL: true });
    topic.addSubscription(new subs.EmailSubscription(props.alertEmail));
    new events.Rule(this, 'Det10', {
      ruleName: 'frontdesk-det-10',
      description: 'DET-10: IAM Access Analyzer found a resource shared outside the organization. Runbook: docs/DETECTIONS.md#det-10',
      eventPattern: { source: ['aws.access-analyzer'], detailType: ['Access Analyzer Finding'], detail: { status: ['ACTIVE'] } },
      targets: [new targets.SnsTopic(topic)],
    });
    NagSuppressions.addResourceSuppressions(topic, [{ id: 'AwsSolutions-SNS2', reason: 'Finding notifications carry resource ARNs, not customer data.' }]);
  }
}
