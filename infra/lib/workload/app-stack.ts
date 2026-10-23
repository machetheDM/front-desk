/**
 * The application in af-south-1: intake, the conversation pipeline, the staff inbox API,
 * WhatsApp, analytics export, and the deployment safety net (signed code, canary, rollback).
 */
import * as path from 'path';
import * as fs from 'fs';
import { CfnOutput, Duration, Stack, StackProps, Tags } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as codedeploy from 'aws-cdk-lib/aws-codedeploy';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as signer from 'aws-cdk-lib/aws-signer';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { AI_REGION, EnvName } from '../shared/config';
import { DataStack } from './data-stack';

export const LAMBDA_BUILD_DIR = path.join(__dirname, '..', '..', '..', 'build', 'lambda');
const EU_INVOKE_REGIONS = ['eu-central-1', 'eu-north-1', 'eu-south-1', 'eu-south-2', 'eu-west-1', 'eu-west-3'];

export interface AppStackProps extends StackProps {
  envName: EnvName;
  data: DataStack;
  guardrailId: string;
  guardrailVersion: string;
  webBucket: s3.IBucket;
  webDomain: string;
  alertEmail?: string;
  modelSmall: string;
  modelLarge: string;
  signingProfile: signer.ISigningProfile;
  /** Prod deploys signed code from the signing bucket (scripts/sign-lambda.ps1). */
  signedCode?: { bucketName: string; key: string; objectVersion?: string };
}

export class AppStack extends Stack {
  readonly httpApi: apigw.HttpApi;
  readonly alarmTopic: sns.Topic;
  readonly functions: Record<string, lambda.Function> = {};

  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props);
    const { data, envName } = props;
    const prod = envName === 'prod';
    const prefix = `frontdesk-${envName}`;

    this.alarmTopic = new sns.Topic(this, 'Alarms', { topicName: `${prefix}-alarms`, masterKey: data.key });
    if (props.alertEmail) this.alarmTopic.addSubscription(new subs.EmailSubscription(props.alertEmail));
    const alarmAction = new cwActions.SnsAction(this.alarmTopic);

    // ---------- Code, signing ----------
    // Prod refuses unsigned code outright. Dev warns, so a laptop deploy still works.
    const codeSigningConfig = new lambda.CodeSigningConfig(this, 'CodeSigning', {
      signingProfiles: [props.signingProfile],
      untrustedArtifactOnDeployment: prod ? lambda.UntrustedArtifactOnDeployment.ENFORCE : lambda.UntrustedArtifactOnDeployment.WARN,
    });
    const code = this.lambdaCode(props);

    const baseEnv: Record<string, string> = {
      FRONTDESK_ENV: envName,
      FRONTDESK_DB_CLUSTER_ARN: data.cluster.clusterArn,
      FRONTDESK_DB_SECRET_ARN: data.appSecret.secretArn,
      FRONTDESK_DB_NAME: data.databaseName,
      FRONTDESK_AI_REGION: AI_REGION,
      FRONTDESK_GUARDRAIL_ID: props.guardrailId,
      FRONTDESK_GUARDRAIL_VERSION: props.guardrailVersion,
      FRONTDESK_MODEL_SMALL: props.modelSmall,
      FRONTDESK_MODEL_LARGE: props.modelLarge,
      FRONTDESK_EMBEDDER: 'cohere-embed-multilingual-v3',
      FRONTDESK_RERANKER: 'cohere-rerank-v3-5',
      FRONTDESK_SSM_PREFIX: `/frontdesk/${envName}`,
      FRONTDESK_DOCS_BUCKET: data.docsBucket.bucketName,
      FRONTDESK_KMS_KEY_ARN: data.key.keyArn,
    };

    const fn = (name: string, handler: string, opts: Partial<lambda.FunctionProps> = {}) => {
      const logGroup = new logs.LogGroup(this, `${name}Logs`, {
        logGroupName: `/frontdesk/${envName}/${name}`,
        retention: logs.RetentionDays.TWO_WEEKS,
        encryptionKey: data.key,
      });
      const f = new lambda.Function(this, name, {
        functionName: `${prefix}-${name}`,
        runtime: lambda.Runtime.PYTHON_3_13,
        architecture: lambda.Architecture.ARM_64,
        handler,
        code,
        memorySize: 512,
        timeout: Duration.seconds(30),
        environment: { ...baseEnv, ...(opts.environment ?? {}) },
        codeSigningConfig,
        tracing: lambda.Tracing.ACTIVE,
        logGroup,
        loggingFormat: lambda.LoggingFormat.JSON,
        ...opts,
      });
      this.grantDataApi(f, data);
      this.functions[name] = f;
      return f;
    };

    // ---------- Pipeline functions ----------
    const screen = fn('screen', 'frontdesk.handlers.pipeline.screen');
    const answer = fn('answer', 'frontdesk.handlers.pipeline.answer', { timeout: Duration.seconds(60), memorySize: 1024 });
    const deliver = fn('deliver', 'frontdesk.handlers.pipeline.deliver');
    const degrade = fn('degrade', 'frontdesk.handlers.pipeline.degrade');
    this.grantBedrock(answer, props);
    // Only the order-API secrets (SecureString, AWS managed key): the answer step needs nothing else from SSM.
    answer.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/frontdesk/${envName}/orders-api/*`],
    }));
    this.grantSsmRead(screen, envName);
    this.grantSsmRead(deliver, envName);
    deliver.addToRolePolicy(new iam.PolicyStatement({
      actions: ['social-messaging:SendWhatsAppMessage'],
      resources: [`arn:aws:social-messaging:${this.region}:${this.account}:phone-number-id/*`],
    }));
    degrade.addToRolePolicy(new iam.PolicyStatement({
      actions: ['social-messaging:SendWhatsAppMessage'],
      resources: [`arn:aws:social-messaging:${this.region}:${this.account}:phone-number-id/*`],
    }));

    // The answer function is the one most likely to regress (prompts, models), so it is the
    // one released by canary: 10% of traffic for five minutes, rolled back if errors or the
    // degraded-mode rate rise.
    const answerAlias = new lambda.Alias(this, 'AnswerLive', { aliasName: 'live', version: answer.currentVersion });
    const answerErrors = answerAlias.metricErrors({ period: Duration.minutes(1) }).createAlarm(this, 'AnswerErrors', {
      alarmName: `${prefix}-answer-errors`, threshold: 2, evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    const degradedMetric = new cloudwatch.Metric({ namespace: 'FrontDesk', metricName: 'Degraded', dimensionsMap: { Env: envName }, statistic: 'Sum', period: Duration.minutes(5) });
    const degraded = degradedMetric.createAlarm(this, 'DegradedMode', {
      alarmName: `${prefix}-degraded-mode`, threshold: 3, evaluationPeriods: 1,
      alarmDescription: 'DET-06: the model layer is failing and customers are being told a person will reply. Runbook docs/DETECTIONS.md#det-06',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    degraded.addAlarmAction(alarmAction);
    answerErrors.addAlarmAction(alarmAction);
    new codedeploy.LambdaDeploymentGroup(this, 'AnswerCanary', {
      alias: answerAlias,
      deploymentConfig: codedeploy.LambdaDeploymentConfig.CANARY_10PERCENT_5MINUTES,
      alarms: [answerErrors, degraded],
      autoRollback: { failedDeployment: true, deploymentInAlarm: true, stoppedDeployment: true },
    });

    // ---------- Step Functions: screen -> answer -> deliver, Catch -> degrade ----------
    const sfnLogs = new logs.LogGroup(this, 'PipelineLogs', {
      logGroupName: `/aws/vendedlogs/states/${prefix}-pipeline`, retention: logs.RetentionDays.TWO_WEEKS, encryptionKey: data.key,
    });
    const invoke = (id: string, f: lambda.IFunction) => new tasks.LambdaInvoke(this, id, {
      lambdaFunction: f, payloadResponseOnly: true, retryOnServiceExceptions: true,
    });
    const degradeTask = invoke('Degrade', degrade);
    const answerTask = invoke('Answer', answerAlias).addRetry({ errors: ['ThrottlingException', 'ModelNotReadyException'], maxAttempts: 2, interval: Duration.seconds(1), backoffRate: 2 });
    // Any failure in the language layer lands here: the customer is told a person will reply
    // and the conversation is handed over. The front desk does not close.
    answerTask.addCatch(degradeTask, { errors: ['States.ALL'], resultPath: '$.error' });
    const deliverTask = invoke('Deliver', deliver);
    const skip = new sfn.Choice(this, 'NeedsAnswer?')
      .when(sfn.Condition.booleanEquals('$.proceed', false), new sfn.Succeed(this, 'NothingToAnswer'))
      .otherwise(answerTask.next(deliverTask));
    const pipeline = new sfn.StateMachine(this, 'Pipeline', {
      stateMachineName: `${prefix}-pipeline`,
      stateMachineType: sfn.StateMachineType.EXPRESS,
      definitionBody: sfn.DefinitionBody.fromChainable(invoke('Screen', screen).next(skip)),
      timeout: Duration.minutes(2),
      tracingEnabled: true,
      // Execution data would put customer text in CloudWatch; errors only, data never.
      logs: { destination: sfnLogs, level: sfn.LogLevel.ERROR, includeExecutionData: false },
    });

    // ---------- WhatsApp (End User Messaging Social -> SNS -> intake) ----------
    // Signature version 2 (SHA-256): the intake Lambda rejects anything else (sns_verify.py).
    const whatsappTopic = new sns.Topic(this, 'WhatsAppEvents', { topicName: `${prefix}-whatsapp-events`, masterKey: data.key, signatureVersion: '2' });
    whatsappTopic.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal('social-messaging.amazonaws.com')],
      actions: ['sns:Publish'],
      resources: [whatsappTopic.topicArn],
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    }));
    data.key.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal('social-messaging.amazonaws.com')],
      actions: ['kms:GenerateDataKey*', 'kms:Decrypt'],
      resources: ['*'],
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    }));
    const whatsappIntake = fn('whatsapp-intake', 'frontdesk.handlers.whatsapp_intake.handler', {
      environment: { FRONTDESK_STATE_MACHINE_ARN: pipeline.stateMachineArn, FRONTDESK_WHATSAPP_TOPIC_ARN: whatsappTopic.topicArn },
    });
    pipeline.grantStartExecution(whatsappIntake);
    this.grantSsmRead(whatsappIntake, envName);
    whatsappTopic.addSubscription(new subs.LambdaSubscription(whatsappIntake));

    // ---------- Web chat (HTTP for simple widgets, WebSocket for progress streaming) ----------
    const webChat = fn('web-chat', 'frontdesk.handlers.web_chat.handler', { timeout: Duration.seconds(29), memorySize: 1024 });
    this.grantBedrock(webChat, props);
    this.grantSsmRead(webChat, envName);

    // ---------- Staff inbox API (Cognito, MFA required) ----------
    const userPool = new cognito.UserPool(this, 'StaffPool', {
      userPoolName: `${prefix}-staff`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { otp: true, sms: false },
      featurePlan: cognito.FeaturePlan.LITE,
      passwordPolicy: { minLength: 12, requireDigits: true, requireLowercase: true, requireUppercase: true, requireSymbols: true, tempPasswordValidity: Duration.days(2) },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      customAttributes: { tenant_id: new cognito.StringAttribute({ mutable: false, minLen: 36, maxLen: 36 }) },
      deletionProtection: prod,
    });
    new cognito.CfnUserPoolGroup(this, 'OwnerGroup', { userPoolId: userPool.userPoolId, groupName: 'owner' });
    new cognito.CfnUserPoolGroup(this, 'AgentGroup', { userPoolId: userPool.userPoolId, groupName: 'agent' });
    const domain = userPool.addDomain('Domain', { cognitoDomain: { domainPrefix: `${prefix}-${this.account}`.slice(0, 63) } });
    const inboxClient = userPool.addClient('InboxClient', {
      generateSecret: false,
      authFlows: { userSrp: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
        callbackUrls: [`https://${props.webDomain}/inbox/callback`, ...(prod ? [] : ['http://localhost:3000/inbox/callback'])],
        logoutUrls: [`https://${props.webDomain}/inbox/`],
      },
      idTokenValidity: Duration.minutes(60),
      accessTokenValidity: Duration.minutes(60),
      refreshTokenValidity: Duration.hours(12),
      preventUserExistenceErrors: true,
      readAttributes: new cognito.ClientAttributes().withStandardAttributes({ email: true, fullname: true }).withCustomAttributes('tenant_id'),
    });

    const inboxApi = fn('inbox-api', 'frontdesk.handlers.inbox_api.handler', {
      environment: { FRONTDESK_USER_POOL_ID: userPool.userPoolId },
    });
    this.grantSsmRead(inboxApi, envName);
    inboxApi.addToRolePolicy(new iam.PolicyStatement({ actions: ['events:PutEvents'], resources: [`arn:aws:events:${this.region}:${this.account}:event-bus/default`] }));
    inboxApi.addToRolePolicy(new iam.PolicyStatement({
      actions: ['social-messaging:SendWhatsAppMessage'],
      resources: [`arn:aws:social-messaging:${this.region}:${this.account}:phone-number-id/*`],
    }));

    const accessLogs = new logs.LogGroup(this, 'ApiAccessLogs', { retention: logs.RetentionDays.TWO_WEEKS, encryptionKey: data.key });
    this.httpApi = new apigw.HttpApi(this, 'HttpApi', {
      apiName: `${prefix}-api`,
      // Any origin may *call*; that is safe because nothing here uses cookies (the inbox sends
      // a bearer token, the widget a public key). Which origin a widget key may be used from is
      // enforced in the chat Lambda against the key's allow-list (frontdesk/routing.py).
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST],
        allowHeaders: ['authorization', 'content-type', 'x-frontdesk-widget-key'],
        allowCredentials: false,
        maxAge: Duration.hours(1),
      },
    });
    const stage = this.httpApi.defaultStage!.node.defaultChild as apigw.CfnStage;
    // Stage-wide throttle: a cheap floor under the per-number and per-tenant limits in the app.
    stage.defaultRouteSettings = { throttlingBurstLimit: 20, throttlingRateLimit: 10 };
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({ requestId: '$context.requestId', ip: '$context.identity.sourceIp', route: '$context.routeKey', status: '$context.status', latency: '$context.responseLatency' }),
    };
    const jwt = new authorizers.HttpUserPoolAuthorizer('StaffJwt', userPool, { userPoolClients: [inboxClient] });
    const inboxIntegration = new integrations.HttpLambdaIntegration('InboxIntegration', inboxApi);
    for (const [method, route] of [
      [apigw.HttpMethod.GET, '/v1/inbox/handoffs'],
      [apigw.HttpMethod.GET, '/v1/inbox/conversations/{id}'],
      [apigw.HttpMethod.POST, '/v1/inbox/conversations/{id}/reply'],
      [apigw.HttpMethod.POST, '/v1/inbox/conversations/{id}/resolve'],
      [apigw.HttpMethod.POST, '/v1/inbox/conversations/{id}/template'],
      [apigw.HttpMethod.GET, '/v1/insights/summary'],
      [apigw.HttpMethod.POST, '/v1/admin/deletion-requests'],
    ] as const) {
      this.httpApi.addRoutes({ path: route, methods: [method], integration: inboxIntegration, authorizer: jwt });
    }
    this.httpApi.addRoutes({ path: '/v1/chat', methods: [apigw.HttpMethod.POST], integration: new integrations.HttpLambdaIntegration('ChatIntegration', webChat) });

    const wsHandler = fn('web-chat-ws', 'frontdesk.handlers.web_chat.ws_handler', { timeout: Duration.seconds(60), memorySize: 1024 });
    this.grantBedrock(wsHandler, props);
    this.grantSsmRead(wsHandler, envName);
    const wsApi = new apigw.WebSocketApi(this, 'ChatSocket', {
      apiName: `${prefix}-chat-ws`,
      connectRouteOptions: { integration: new integrations.WebSocketLambdaIntegration('WsConnect', wsHandler) },
      disconnectRouteOptions: { integration: new integrations.WebSocketLambdaIntegration('WsDisconnect', wsHandler) },
    });
    wsApi.addRoute('ask', { integration: new integrations.WebSocketLambdaIntegration('WsAsk', wsHandler) });
    const wsStage = new apigw.WebSocketStage(this, 'ChatSocketStage', { webSocketApi: wsApi, stageName: 'v1', autoDeploy: true,
      throttle: { burstLimit: 20, rateLimit: 10 } });
    wsApi.grantManageConnections(wsHandler);
    wsHandler.addEnvironment('FRONTDESK_WS_ENDPOINT', wsStage.callbackUrl);

    // ---------- Operations: migrations, smoke test, analytics export ----------
    const migrate = fn('migrate', 'frontdesk.handlers.ops.migrate', {
      timeout: Duration.minutes(5),
      environment: { FRONTDESK_DB_ADMIN_SECRET_ARN: data.cluster.secret!.secretArn },
    });
    this.grantSecretRead(migrate, data, data.cluster.secret!.secretArn);
    this.grantDataApiSecret(migrate, data);
    fn('smoke', 'frontdesk.handlers.ops.smoke');

    const glueDb = new glue.CfnDatabase(this, 'AnalyticsDb', {
      catalogId: this.account,
      databaseInput: { name: `frontdesk_${envName}`, description: 'Derived conversation metrics, no message text (docs/ARCHITECTURE.md)' },
    });
    const icebergTable = new glue.CfnTable(this, 'EventsIceberg', {
      catalogId: this.account,
      databaseName: `frontdesk_${envName}`,
      openTableFormatInput: { icebergInput: { metadataOperation: 'CREATE', version: '2' } },
      tableInput: {
        name: 'conversation_events',
        tableType: 'EXTERNAL_TABLE',
        storageDescriptor: {
          location: `s3://${data.analyticsBucket.bucketName}/iceberg/conversation_events/`,
          columns: [
            { name: 'tenant_id', type: 'string' }, { name: 'event_id', type: 'bigint' },
            { name: 'conversation_id', type: 'string' }, { name: 'type', type: 'string' },
            { name: 'route', type: 'string' }, { name: 'intent', type: 'string' },
            { name: 'after_hours', type: 'boolean' }, { name: 'first_reply_seconds', type: 'double' },
            { name: 'occurred_at', type: 'timestamp' },
          ],
        },
      },
    });
    icebergTable.addResourceDependency(glueDb);
    const workgroup = new athena.CfnWorkGroup(this, 'Athena', {
      name: `${prefix}-insights`,
      state: 'ENABLED',
      workGroupConfiguration: {
        // A runaway query costs at most 1 GB scanned (USD 0.006). Enforced, not advisory.
        bytesScannedCutoffPerQuery: 1_000_000_000,
        enforceWorkGroupConfiguration: true,
        publishCloudWatchMetricsEnabled: true,
        resultConfiguration: {
          outputLocation: `s3://${data.analyticsBucket.bucketName}/athena-results/`,
          encryptionConfiguration: { encryptionOption: 'SSE_KMS', kmsKey: data.key.keyArn },
        },
      },
    });
    const exporter = fn('export', 'frontdesk.handlers.ops.export_events', {
      timeout: Duration.minutes(10),
      environment: {
        FRONTDESK_ANALYTICS_BUCKET: data.analyticsBucket.bucketName,
        FRONTDESK_ATHENA_WORKGROUP: workgroup.name,
        FRONTDESK_GLUE_DATABASE: `frontdesk_${envName}`,
      },
    });
    // Identity-based grants throughout: a resource-policy grant on the Data stack's key or
    // bucket would make the Data stack depend on this one and create a cycle.
    exporter.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket', 's3:GetBucketLocation', 's3:AbortMultipartUpload'],
      resources: [data.analyticsBucket.bucketArn, data.analyticsBucket.arnForObjects('*')],
    }));
    exporter.addToRolePolicy(new iam.PolicyStatement({ actions: ['kms:GenerateDataKey', 'kms:Encrypt'], resources: [data.key.keyArn] }));
    this.grantSsmRead(exporter, envName);
    exporter.addToRolePolicy(new iam.PolicyStatement({
      actions: ['athena:StartQueryExecution', 'athena:GetQueryExecution', 'athena:GetQueryResults'],
      resources: [`arn:aws:athena:${this.region}:${this.account}:workgroup/${workgroup.name}`],
    }));
    exporter.addToRolePolicy(new iam.PolicyStatement({
      actions: ['glue:GetTable', 'glue:GetDatabase', 'glue:UpdateTable', 'glue:GetPartitions'],
      resources: [`arn:aws:glue:${this.region}:${this.account}:catalog`, `arn:aws:glue:${this.region}:${this.account}:database/frontdesk_${envName}`,
        `arn:aws:glue:${this.region}:${this.account}:table/frontdesk_${envName}/*`],
    }));
    // 02:15 SAST nightly (00:15 UTC).
    new events.Rule(this, 'NightlyExport', { schedule: events.Schedule.cron({ minute: '15', hour: '0' }), targets: [new targets.LambdaFunction(exporter)] });

    // One page for the operator: is the front desk answering, how fast, at what cost, and is
    // anything being attacked or drifting. Built from the same metrics the alarms use.
    const m = (metricName: string, statistic = 'Sum', dims: Record<string, string> = {}, period = Duration.minutes(5)) =>
      new cloudwatch.Metric({ namespace: 'FrontDesk', metricName, dimensionsMap: { Env: envName, ...dims }, statistic, period });
    const routes = ['small', 'large', 'rules'];
    new cloudwatch.Dashboard(this, 'Operations', {
      dashboardName: `${prefix}-operations`,
      widgets: [
        [
          new cloudwatch.GraphWidget({ title: 'Answer latency p90 by route (ms)', width: 12, left: routes.map((r) => m('AnswerLatencyMs', 'p90', { Route: r })) }),
          new cloudwatch.GraphWidget({ title: 'Model cost (USD) by route', width: 12, left: routes.map((r) => m('ModelCostUsd', 'Sum', { Route: r }, Duration.hours(1))) }),
        ],
        [
          new cloudwatch.GraphWidget({ title: 'Degraded (DET-06) and paused', width: 8, left: [m('Degraded'), m('AssistantPaused')] }),
          new cloudwatch.GraphWidget({ title: 'Guardrail interventions (DET-07) and rate limits (DET-09)', width: 8, left: [m('GuardrailIntervened'), m('RateLimited')] }),
          new cloudwatch.GraphWidget({ title: 'Intent-mix drift, worst tenant (DET-12)', width: 8, left: [m('IntentMixPsiMax', 'Maximum', {}, Duration.days(1))],
            leftAnnotations: [{ value: 0.25, label: 'shifted' }] }),
        ],
        [
          new cloudwatch.GraphWidget({ title: 'Answer step errors and duration', width: 12, left: [answer.metricErrors()], right: [answer.metricDuration({ statistic: 'p90' })] }),
          new cloudwatch.AlarmStatusWidget({ title: 'Alarms', width: 12, alarms: [degraded, answerErrors] }),
        ],
      ],
    });

    // Weekly drift on each tenant's intent mix; DET-12 (DetectionStack) alarms on the result.
    // 07:00 SAST on Mondays, so the owner reads it with the week's first messages.
    const drift = fn('drift', 'frontdesk.handlers.ops.drift', { timeout: Duration.minutes(5) });
    this.grantSsmRead(drift, envName);
    new events.Rule(this, 'WeeklyDrift', { schedule: events.Schedule.cron({ minute: '0', hour: '5', weekDay: 'MON' }), targets: [new targets.LambdaFunction(drift)] });

    // ---------- Inbox runtime config (read by the static inbox at startup) ----------
    new s3deploy.BucketDeployment(this, 'InboxConfig', {
      destinationBucket: props.webBucket,
      destinationKeyPrefix: 'inbox',
      prune: false,
      sources: [s3deploy.Source.jsonData('config.json', {
        apiUrl: this.httpApi.apiEndpoint,
        wsUrl: wsStage.url,
        region: this.region,
        userPoolId: userPool.userPoolId,
        clientId: inboxClient.userPoolClientId,
        cognitoDomain: `${domain.domainName}.auth.${this.region}.amazoncognito.com`,
      })],
    });

    Tags.of(this).add('project', 'front-desk');
    Tags.of(this).add('env', envName);
    new CfnOutput(this, 'ApiUrl', { value: this.httpApi.apiEndpoint });
    new CfnOutput(this, 'ChatSocketUrl', { value: wsStage.url });
    new CfnOutput(this, 'WhatsAppTopicArn', { value: whatsappTopic.topicArn, description: 'Set this as the event destination of the WhatsApp Business Account in End User Messaging Social' });
    new CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new CfnOutput(this, 'PipelineArn', { value: pipeline.stateMachineArn });

    this.suppressions();
  }

  private lambdaCode(props: AppStackProps): lambda.Code {
    if (props.signedCode) {
      const b = s3.Bucket.fromBucketName(this, 'SignedCodeBucket', props.signedCode.bucketName);
      return lambda.Code.fromBucket(b, props.signedCode.key, props.signedCode.objectVersion);
    }
    if (props.envName === 'prod') {
      throw new Error('prod requires signed code: pass -c frontdesk:signedCodeKey=... (docs/DEPLOY.md step 12)');
    }
    if (fs.existsSync(LAMBDA_BUILD_DIR)) return lambda.Code.fromAsset(LAMBDA_BUILD_DIR);
    if (this.node.tryGetContext('frontdesk:testing')) {
      // Synth-only: the template is the same, the asset is the source tree without dependencies.
      return lambda.Code.fromAsset(path.join(__dirname, '..', '..', '..', 'backend', 'frontdesk'));
    }
    throw new Error(`Lambda package missing: run scripts/build-lambda.ps1 (expected ${LAMBDA_BUILD_DIR})`);
  }

  private grantDataApi(f: lambda.Function, data: DataStack): void {
    this.grantDataApiSecret(f, data);
    this.grantSecretRead(f, data, data.appSecret.secretArn);
  }

  private grantDataApiSecret(f: lambda.Function, data: DataStack): void {
    f.addToRolePolicy(new iam.PolicyStatement({
      actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement', 'rds-data:BeginTransaction', 'rds-data:CommitTransaction', 'rds-data:RollbackTransaction'],
      resources: [data.cluster.clusterArn],
    }));
  }

  private grantSecretRead(f: lambda.Function, data: DataStack, secretArn: string): void {
    f.addToRolePolicy(new iam.PolicyStatement({ actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'], resources: [secretArn] }));
    f.addToRolePolicy(new iam.PolicyStatement({ actions: ['kms:Decrypt'], resources: [data.key.keyArn] }));
  }

  private grantSsmRead(f: lambda.Function, envName: string): void {
    f.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParametersByPath'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/frontdesk/${envName}/*`],
    }));
  }

  private grantBedrock(f: lambda.Function, props: AppStackProps): void {
    f.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-5-5*`,
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5-5*`,
        `arn:aws:bedrock:*::foundation-model/cohere.embed-multilingual-v3`,
        `arn:aws:bedrock:*::foundation-model/cohere.rerank-v3-5:0`,
        `arn:aws:bedrock:${AI_REGION}:${this.account}:inference-profile/eu.anthropic.*`,
      ],
      conditions: { StringEquals: { 'aws:RequestedRegion': EU_INVOKE_REGIONS } },
    }));
    f.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [`arn:aws:bedrock:${AI_REGION}:${this.account}:guardrail/${props.guardrailId}`],
    }));
    f.addToRolePolicy(new iam.PolicyStatement({ actions: ['bedrock:Rerank'], resources: ['*'], conditions: { StringEquals: { 'aws:RequestedRegion': AI_REGION } } }));
    f.addToRolePolicy(new iam.PolicyStatement({ actions: ['cloudwatch:PutMetricData'], resources: ['*'], conditions: { StringEquals: { 'cloudwatch:namespace': 'FrontDesk' } } }));
  }

  private suppressions(): void {
    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-IAM4', reason: 'AWSLambdaBasicExecutionRole for CloudWatch Logs; all data permissions are inline and scoped.' },
      { id: 'AwsSolutions-IAM5', reason: 'Wildcards are limited to model version suffixes, per-env SSM paths, phone-number ids not known at synth, and CDK-generated grants; each is Region-conditioned where it matters.' },
      { id: 'AwsSolutions-L1', reason: 'Python 3.13 is the newest runtime the backend is tested on; CDK-internal custom resources are outside our control.' },
      { id: 'AwsSolutions-APIG1', reason: 'HTTP API access logging is configured on the stage via the L1 property.' },
      { id: 'AwsSolutions-APIG4', reason: 'POST /v1/chat and the WebSocket routes are public by design (website visitors); they are protected by widget keys, rate limits and stage throttling.' },
      { id: 'AwsSolutions-COG4', reason: 'Same as APIG4 for the public chat routes; every inbox route uses the Cognito authorizer.' },
      { id: 'AwsSolutions-COG3', reason: 'Cognito threat protection is a paid (Plus) feature; designed, switched on when a client pays.' },
      { id: 'AwsSolutions-COG8', reason: 'Plus tier is paid (USD 0.02/MAU); MFA is required on Lite. Recorded under "when a client pays".' },
      { id: 'AwsSolutions-SF1', reason: 'Express workflow logs ERROR only: ALL would write customer text to CloudWatch.' },
      { id: 'AwsSolutions-SF2', reason: 'X-Ray tracing is enabled (tracingEnabled: true).' },
    ]);
  }
}
