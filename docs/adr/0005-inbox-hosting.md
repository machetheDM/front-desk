# ADR 0005: Staff inbox as a Next.js static export on S3 + CloudFront

- Status: accepted
- Date: 2026-10-22

## Context
The proposal named Amplify Hosting. The Price List API has no Amplify offer for af-south-1, and
AWS launch notes list no Cape Town support. Hosting the inbox in another Region would put staff
session traffic outside SA for no gain.

## Decision
`next build` with `output: "export"`. Files go to a private S3 bucket in af-south-1 behind
CloudFront with Origin Access Control, a strict security-headers policy (CSP, HSTS,
frame-ancestors none) and no public bucket access. Sign-in uses Cognito Hosted UI with
authorization code + PKCE; tokens stay in memory. All data comes from the HTTP API with a JWT
authorizer.

## Consequences
No server-side rendering, which the inbox does not need. Cheaper and fewer moving parts than
Amplify. Revisit if we need SSR.

## Addendum (2026-10-23): Pages Router and the CSP

The CloudFront policy is `script-src 'self'`. Next.js App Router exports, and Turbopack builds,
emit inline bootstrap scripts that this policy blocks. The inbox therefore uses the Pages Router
with the webpack build, whose export has only external scripts and a non-executable JSON data
tag. `apps/inbox/scripts/check-no-inline-scripts.mjs` fails the build if an inline script
appears, so nobody is tempted to weaken the policy instead.
