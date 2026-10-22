/**
 * Application-level detections in each workload account (DET-05 to DET-11). The app emits
 * custom EventBridge events and CloudWatch metrics; these rules and alarms turn them into
 * alerts. Account-level detections (root, IAM, trail) are in org/org-trail-stack.ts.
 */
import { Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { EnvName } from '../shared/config';

export interface DetectionStackProps extends StackProps {
  envName: EnvName;
  alarmTopic: sns.ITopic;
}

/** Event-driven detections: the app publishes `source: frontdesk.app` events. */
export const APP_EVENT_DETECTIONS = [
  { id: 'DET-05', detailType: 'UnusualTranscriptReads', description: 'One staff member read an unusual number of conversations in an hour.' },
  { id: 'DET-08', detailType: 'SignatureVerificationFailed', description: 'A webhook or SNS message failed signature verification.' },
  { id: 'DET-11', detailType: 'CanaryTokenLeaked', description: 'A system-prompt canary token appeared in model output.' },
];

/** Metric-driven detections: namespace FrontDesk, dimension Env. */
export const APP_METRIC_DETECTIONS = [
  { id: 'DET-06', metric: 'Degraded', threshold: 3, description: 'Model layer failing; customers told a person will reply.' },
  { id: 'DET-07', metric: 'GuardrailIntervened', threshold: 10, description: 'Guardrail interventions spiking: likely a prompt-attack campaign.' },
  { id: 'DET-09', metric: 'RateLimited', threshold: 50, description: 'Rate limits hit unusually often: abuse of the public bot.' },
];

export class DetectionStack extends Stack {
  constructor(scope: Construct, id: string, props: DetectionStackProps) {
    super(scope, id, props);
    for (const d of APP_EVENT_DETECTIONS) {
      new events.Rule(this, d.detailType, {
        ruleName: `frontdesk-${props.envName}-${d.id.toLowerCase()}`,
        description: `${d.id}: ${d.description} Runbook: docs/DETECTIONS.md#${d.id.toLowerCase()}`,
        eventPattern: { source: ['frontdesk.app'], detailType: [d.detailType], detail: { env: [props.envName] } },
        targets: [new targets.SnsTopic(props.alarmTopic)],
      });
    }
    for (const d of APP_METRIC_DETECTIONS) {
      if (d.metric === 'Degraded') continue; // defined with the canary in AppStack so it can roll back deploys
      new cloudwatch.Metric({ namespace: 'FrontDesk', metricName: d.metric, dimensionsMap: { Env: props.envName }, statistic: 'Sum', period: Duration.minutes(5) })
        .createAlarm(this, `${d.metric}Alarm`, {
          alarmName: `frontdesk-${props.envName}-${d.id.toLowerCase()}`,
          alarmDescription: `${d.id}: ${d.description} Runbook: docs/DETECTIONS.md#${d.id.toLowerCase()}`,
          threshold: d.threshold,
          evaluationPeriods: 1,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }).addAlarmAction(new cwActions.SnsAction(props.alarmTopic));
    }
  }
}
