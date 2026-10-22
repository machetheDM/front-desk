/**
 * A deliberately small evaluator for *deny* SCP statements, so tests can ask "would this
 * request be denied?" for the conditions our SCPs actually use. It is not a general IAM
 * engine: unsupported operators throw, so a new SCP using one fails the test loudly rather
 * than being evaluated wrongly.
 */
export interface Req {
  action: string;
  resource?: string;
  region: string;
  principalArn: string;
}

const wild = (pattern: string, value: string) =>
  new RegExp('^' + pattern.split('*').map((p) => p.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$', 'i').test(value);

const list = (v: unknown): string[] => (v === undefined ? [] : Array.isArray(v) ? v.map(String) : [String(v)]);

function key(req: Req, k: string): string | undefined {
  switch (k) {
    case 'aws:RequestedRegion': return req.region;
    case 'aws:PrincipalArn': return req.principalArn;
    default: throw new Error(`condition key not supported by test evaluator: ${k}`);
  }
}

function conditionHolds(cond: Record<string, Record<string, unknown>> | undefined, req: Req): boolean {
  if (!cond) return true;
  return Object.entries(cond).every(([op, kv]) => Object.entries(kv).every(([k, v]) => {
    const actual = key(req, k);
    const vals = list(v);
    switch (op) {
      case 'StringEquals': return actual !== undefined && vals.includes(actual);
      case 'StringNotEquals': return actual === undefined || !vals.includes(actual);
      case 'StringLike': case 'ArnLike': return actual !== undefined && vals.some((p) => wild(p, actual));
      case 'StringNotLike': case 'ArnNotLike': return actual === undefined || !vals.some((p) => wild(p, actual));
      default: throw new Error(`condition operator not supported by test evaluator: ${op}`);
    }
  }));
}

export function deniedBy(policies: Record<string, any>, req: Req): string[] {
  const hits: string[] = [];
  for (const [name, doc] of Object.entries(policies)) {
    for (const st of doc.Statement) {
      if (st.Effect !== 'Deny') throw new Error(`${name}: only Deny statements are expected in SCPs`);
      const actionMatch = st.Action ? list(st.Action).some((a) => wild(a, req.action))
        : !list(st.NotAction).some((a) => wild(a, req.action));
      const res = req.resource ?? '*';
      const resourceMatch = st.Resource ? list(st.Resource).some((r) => wild(r, res))
        : !list(st.NotResource).some((r) => wild(r, res));
      if (actionMatch && resourceMatch && conditionHolds(st.Condition, req)) hits.push(`${name}:${st.Sid}`);
    }
  }
  return hits;
}
