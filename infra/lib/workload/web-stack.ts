/**
 * Static hosting for the staff inbox and the chat widget (ADR 0005): private S3 bucket behind
 * CloudFront with Origin Access Control and a strict security-headers policy.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
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
    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: headers,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
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
    new CfnOutput(this, 'WebDomain', { value: this.distribution.distributionDomainName });
    new CfnOutput(this, 'WebBucket', { value: this.bucket.bucketName });
  }
}
