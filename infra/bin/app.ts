#!/usr/bin/env node
/**
 * Front Desk CDK app. Every stack is defined here; `cdk deploy <name>` picks one.
 *
 * Real account ids, e-mails and organization ids are passed with -c at deploy time
 * (docs/DEPLOY.md). `-c frontdesk:ci=true` fills placeholders so CI can synthesize and run
 * cdk-nag without any AWS account; a placeholder can never be deployed because CDK refuses to
 * deploy a stack whose account does not match the caller's credentials.
 */
import { buildApp } from '../lib/build-app';

buildApp();
