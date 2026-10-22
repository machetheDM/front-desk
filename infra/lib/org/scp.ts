/**
 * Service control policies, loaded from infra/policies/scp so the JSON that is tested is the
 * JSON that is deployed.
 */
import * as fs from 'fs';
import * as path from 'path';

export const SCP_DIR = path.join(__dirname, '..', '..', 'policies', 'scp');

export type ScpName =
  | 'deny-unapproved-regions'
  | 'frankfurt-is-for-bedrock'
  | 'protect-audit-trail'
  | 'deny-long-lived-credentials'
  | 'deny-root-and-leaving'
  | 'protect-log-archive';

export function loadScp(name: ScpName): any {
  return JSON.parse(fs.readFileSync(path.join(SCP_DIR, `${name}.json`), 'utf8'));
}

/**
 * Where each SCP is attached. AWS allows at most five policies per target and the default
 * FullAWSAccess counts as one, so each target may carry four of ours (asserted in tests).
 */
export const ATTACHMENTS: Record<'root' | 'security' | 'workloads', ScpName[]> = {
  root: ['deny-unapproved-regions', 'frankfurt-is-for-bedrock', 'protect-audit-trail', 'deny-long-lived-credentials'],
  security: ['deny-root-and-leaving', 'protect-log-archive'],
  workloads: ['deny-root-and-leaving'],
};
