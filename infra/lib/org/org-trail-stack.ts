/**
 * The organization trail plus the account-level detections that need it (DET-01 to DET-04).
 * Defined in the management account, us-east-1: IAM and console sign-in events from every
 * account are visible there, and SCPs never restrict the management account.
 *
 * Classic CIS-style approach: the trail also streams to a CloudWatch log group; metric filters
 * turn specific events into metrics; alarms page the owner. Runbooks: docs/DETECTIONS.md.
 */
import { Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

export interface OrgTrailStackProps extends StackProps {
  bucketName: string;
  alertEmail: string;
}

export const ACCOUNT_DETECTIONS: { id: string; name: string; pattern: string; description: string }[] = [
  {
    id: 'DET-01', name: 'RootActivity',
    pattern: '{ $.userIdentity.type = "Root" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != "AwsServiceEvent" }',
    description: 'The root user of any account did something.',
  },
  {
    id: 'DET-02', name: 'IamChangeOutsideDeploy',
    pattern: '{ ($.eventSource = "iam.amazonaws.com") && (($.eventName = "Put*Policy") || ($.eventName = "Attach*Policy") || ($.eventName = "CreateRole") || ($.eventName = "UpdateAssumeRolePolicy") || ($.eventName = "DeleteRolePermissionsBoundary") || ($.eventName = "CreateUser") || ($.eventName = "CreateAccessKey")) && ($.userIdentity.arn != "*cdk-hnb659fds-cfn-exec-role*") }',
    description: 'An IAM change was made by something other than the CDK deploy pipeline.',
  },
  {
    id: 'DET-03', name: 'TrailTampering',
    pattern: '{ ($.eventSource = "cloudtrail.amazonaws.com") && (($.eventName = "StopLogging") || ($.eventName = "DeleteTrail") || ($.eventName = "UpdateTrail") || ($.eventName = "PutEventSelectors")) }',
    description: 'Someone tried to stop or change the organization trail.',
  },
  {
    id: 'DET-04', name: 'ConsoleSignInWithoutMfa',
    pattern: '{ ($.eventName = "ConsoleLogin") && ($.additionalEventData.MFAUsed != "Yes") && ($.responseElements.ConsoleLogin = "Success") }',
    description: 'A console sign-in succeeded without MFA.',
  },
];

export class OrgTrailStack extends Stack {
  constructor(scope: Construct, id: string, props: OrgTrailStackProps) {
    super(scope, id, props);
    const group = new logs.LogGroup(this, 'TrailLogs', {
      logGroupName: '/frontdesk/audit/org-trail',
      retention: logs.RetentionDays.THREE_MONTHS,
    });
    const role = new iam.Role(this, 'TrailToLogs', { assumedBy: new iam.ServicePrincipal('cloudtrail.amazonaws.com') });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:CreateLogStream', 'logs:PutLogEvents'], resources: [`${group.logGroupArn}:*`] }));

    const trail = new cloudtrail.CfnTrail(this, 'OrgTrail', {
      trailName: 'frontdesk-org-trail',
      s3BucketName: props.bucketName,
      isLogging: true,
      isOrganizationTrail: true,
      isMultiRegionTrail: true,
      includeGlobalServiceEvents: true,
      // Digest files let us prove a log file was not altered after delivery.
      enableLogFileValidation: true,
      cloudWatchLogsLogGroupArn: group.logGroupArn,
      cloudWatchLogsRoleArn: role.roleArn,
    });
    trail.node.addDependency(role);
    NagSuppressions.addResourceSuppressions(role, [{
      id: 'AwsSolutions-IAM5', reason: 'CloudTrail names its own log streams; access is limited to this one log group.',
      appliesTo: [{ regex: '/^Resource::<TrailLogs.*\\.Arn>:\\*$/g' }],
    }], true);

    const topic = new sns.Topic(this, 'SecurityAlerts', { topicName: 'frontdesk-security-alerts', enforceSSL: true });
    topic.addSubscription(new subs.EmailSubscription(props.alertEmail));
    for (const d of ACCOUNT_DETECTIONS) {
      const filter = new logs.MetricFilter(this, `${d.name}Filter`, {
        logGroup: group,
        filterPattern: logs.FilterPattern.literal(d.pattern),
        metricNamespace: 'FrontDesk/Security',
        metricName: d.name,
        metricValue: '1',
      });
      new cloudwatch.Alarm(this, `${d.name}Alarm`, {
        alarmName: `frontdesk-${d.id.toLowerCase()}-${d.name}`,
        alarmDescription: `${d.id}: ${d.description} Runbook: docs/DETECTIONS.md#${d.id.toLowerCase()}`,
        metric: filter.metric({ statistic: 'Sum', period: Duration.minutes(5) }),
        threshold: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(new cwActions.SnsAction(topic));
    }
    NagSuppressions.addResourceSuppressions(topic, [{ id: 'AwsSolutions-SNS2', reason: 'Alert text contains no customer data; SSE would need a CMK (USD 1/month) in the management account.' }]);
  }
}
