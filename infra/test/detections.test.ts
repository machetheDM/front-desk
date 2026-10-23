/**
 * Detections fire in a test: sample CloudTrail events go through the real DET-01..04 filter
 * patterns, and sample app events through the real DET-05/08/11 EventBridge patterns. The
 * Python side is checked too: every metric an alarm watches is one the app actually emits.
 */
import * as fs from 'fs';
import * as path from 'path';
import { ACCOUNT_DETECTIONS } from '../lib/org/org-trail-stack';
import { APP_EVENT_DETECTIONS, APP_METRIC_DETECTIONS } from '../lib/workload/detection-stack';
import { eventMatches, matches } from './helpers/filter-pattern';

const ev = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'cloudtrail', 'events.json'), 'utf8'));
const det = (id: string) => ACCOUNT_DETECTIONS.find((d) => d.id === id)!.pattern;
const fired = (id: string) => Object.keys(ev).filter((k) => matches(det(id), ev[k])).sort();

test('DET-01 fires on root activity but not on AWS services acting as root', () => {
  expect(fired('DET-01')).toEqual(['root_console_login']);
});

test('DET-02 fires on IAM changes by people, not by the CDK deploy role, and not on reads', () => {
  expect(fired('DET-02')).toEqual(['iam_attach_policy_by_human', 'iam_create_role_by_human']);
});

test('DET-03 fires on attempts to stop the trail', () => {
  expect(fired('DET-03')).toEqual(['stop_logging']);
});

test('DET-04 fires on a successful console sign-in without MFA only', () => {
  expect(fired('DET-04')).toEqual(['console_login_no_mfa']);
});

test('app security events reach their rules only for the right environment', () => {
  const pattern = (detailType: string) => ({ source: ['frontdesk.app'], detailType: [detailType], detail: { env: ['dev'] } });
  for (const d of APP_EVENT_DETECTIONS) {
    expect(eventMatches(pattern(d.detailType), { source: 'frontdesk.app', 'detail-type': d.detailType, detail: { env: 'dev' } })).toBe(true);
    expect(eventMatches(pattern(d.detailType), { source: 'frontdesk.app', 'detail-type': d.detailType, detail: { env: 'prod' } })).toBe(false);
  }
});

test('every alarmed metric and every detected event is actually emitted by the app', () => {
  const py = fs.readdirSync(path.join(__dirname, '..', '..', 'backend', 'frontdesk'), { recursive: true })
    .filter((f) => String(f).endsWith('.py'))
    .map((f) => fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'frontdesk', String(f)), 'utf8')).join('\n');
  for (const d of APP_METRIC_DETECTIONS) expect({ id: d.id, emitted: py.includes(`metric("${d.metric}"`) }).toEqual({ id: d.id, emitted: true });
  for (const d of APP_EVENT_DETECTIONS) expect({ id: d.id, emitted: py.includes(`security_event("${d.detailType}"`) }).toEqual({ id: d.id, emitted: true });
});

test('the evaluator refuses syntax it does not understand', () => {
  expect(() => matches('{ $.a > 5 }', {})).toThrow();
});
