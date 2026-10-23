/**
 * Static hosting for the staff inbox and the chat widget (ADR 0005): private S3 bucket behind
 * CloudFront with Origin Access Control and a strict security-headers policy.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as fs from 'fs';
import * as path from 'path';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { EnvName } from '../shared/config';

export interface WebStackProps extends StackProps {
  envName: EnvName;
}

export class WebStack extends Stack {
  readonly bucket: s3.Bucket;
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);
    const prod = props.envName === 'prod';
    const logs = new s3.Bucket(this, 'CdnLogs', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      // CloudFront standard logs need ACLs on the log bucket.
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [{ expiration: Duration.days(30) }],
      removalPolicy: prod ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      autoDeleteObjects: !prod,
    });
    this.bucket = new s3.Bucket(this, 'Site', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      serverAccessLogsBucket: logs,
      serverAccessLogsPrefix: 'site/',
      removalPolicy: prod ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      autoDeleteObjects: !prod,
    });
    const headers = new cloudfront.ResponseHeadersPolicy(this, 'Headers', {
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          contentSecurityPolicy: [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "connect-src 'self' https://*.execute-api.af-south-1.amazonaws.com wss://*.execute-api.af-south-1.amazonaws.com https://*.amazoncognito.com https://cognito-idp.af-south-1.amazonaws.com",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self' https://*.amazoncognito.com",
          ].join('; '),
          override: true,
        },
        strictTransportSecurity: { accessControlMaxAge: Duration.days(365), includeSubdomains: true, preload: true, override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        contentTypeOptions: { override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
        xssProtection: { protection: true, modeBlock: true, override: true },
      },
    });
    // S3 has no directory index: /inbox/conversation/ must be served as .../index.html.
    const indexRewrite = new cloudfront.Function(this, 'IndexRewrite', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event) { var r = event.request; if (r.uri.endsWith('/')) { r.uri += 'index.html'; } " +
        "else if (r.uri.indexOf('.') === -1) { r.uri += '/index.html'; } return r; }"),
    });
    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: headers,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        functionAssociations: [{ function: indexRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      defaultRootObject: 'inbox/index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      enableLogging: true,
      logBucket: logs,
      logFilePrefix: 'cdn/',
      errorResponses: [{ httpStatus: 404, responseHttpStatus: 404, responsePagePath: '/inbox/404.html', ttl: Duration.minutes(1) }],
    });
    NagSuppressions.addResourceSuppressions(logs, [{ id: 'AwsSolutions-S1', reason: 'Log bucket; logging it to itself would loop.' }]);
    NagSuppressions.addResourceSuppressions(this.distribution, [
      { id: 'AwsSolutions-CFR1', reason: 'Geo restriction would block SA staff travelling; access is controlled by Cognito.' },
      { id: 'AwsSolutions-CFR2', reason: 'WAF is a "when a client pays" item (docs/ARCHITECTURE.md cost table).' },
      { id: 'AwsSolutions-CFR4', reason: 'The default *.cloudfront.net certificate allows TLSv1 at the viewer; a custom domain with ACM and TLS 1.2 minimum comes with the first client.' },
    ]);
    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-IAM4', reason: 'CDK BucketDeployment custom resource uses AWSLambdaBasicExecutionRole.', appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'] },
      { id: 'AwsSolutions-IAM5', reason: 'CDK BucketDeployment must write any key under the site prefix and read its asset bucket.' },
      { id: 'AwsSolutions-L1', reason: 'CDK-managed BucketDeployment runtime.' },
    ]);
    const inboxOut = path.join(__dirname, '..', '..', '..', 'apps', 'inbox', 'out');
    if (fs.existsSync(inboxOut)) {
      // prune: false keeps inbox/config.json, which the App stack writes after this one.
      new s3deploy.BucketDeployment(this, 'InboxSite', {
        destinationBucket: this.bucket, destinationKeyPrefix: 'inbox', sources: [s3deploy.Source.asset(inboxOut)],
        prune: false, distribution: this.distribution, distributionPaths: ['/inbox/*'],
      });
    }
    new s3deploy.BucketDeployment(this, 'Widget', {
      destinationBucket: this.bucket, destinationKeyPrefix: 'widget',
      sources: [s3deploy.Source.asset(path.join(__dirname, '..', '..', '..', 'apps', 'widget'), { exclude: ['README.md'] })],
    });

    new CfnOutput(this, 'WebDomain', { value: this.distribution.distributionDomainName });
    new CfnOutput(this, 'WebBucket', { value: this.bucket.bucketName });
  }
}
