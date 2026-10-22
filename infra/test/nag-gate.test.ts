/**
 * Kept failing example for the cdk-nag gate: a stack with an obviously bad bucket must produce
 * AwsSolutions errors. If cdk-nag were removed from the app or its errors stopped surfacing,
 * this test and the zero-errors test in stacks.test.ts would disagree.
 */
import { App, Aspects, Stack } from 'aws-cdk-lib';
import { Annotations, Match } from 'aws-cdk-lib/assertions';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { AwsSolutionsChecks } from 'cdk-nag';

test('cdk-nag reports errors for an unencrypted bucket without TLS enforcement or logging', () => {
  const app = new App();
  const stack = new Stack(app, 'BadExample');
  new s3.Bucket(stack, 'Bad', { encryption: s3.BucketEncryption.UNENCRYPTED, enforceSSL: false });
  Aspects.of(app).add(new AwsSolutionsChecks());
  const errors = Annotations.fromStack(stack).findError('*', Match.stringLikeRegexp('AwsSolutions-S(1|10)'));
  expect(errors.length).toBeGreaterThanOrEqual(2);
});
