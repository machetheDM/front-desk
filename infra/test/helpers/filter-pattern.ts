/**
 * Evaluates the subset of CloudWatch Logs JSON filter-pattern syntax our detections use, so a
 * test can feed sample CloudTrail events through DET-01 to DET-04 and see which fire:
 *   { ($.a.b = "x") && (($.c = "y*") || ($.d != "*z*")) && $.e NOT EXISTS }
 * Unsupported syntax throws, so a new detection written in a form this evaluator does not know
 * fails its test rather than passing untested.
 */
type Tok = { t: 'lp' | 'rp' | 'and' | 'or' | 'cmp'; path?: string; op?: string; val?: string };

function tokenize(src: string): Tok[] {
  const s = src.trim().replace(/^\{/, '').replace(/\}$/, '');
  const re = /\s*(\(|\)|&&|\|\||\$\.[A-Za-z0-9_.]+\s*(?:=|!=)\s*"[^"]*"|\$\.[A-Za-z0-9_.]+\s+NOT EXISTS|\$\.[A-Za-z0-9_.]+\s+EXISTS)/y;
  const out: Tok[] = [];
  let m: RegExpExecArray | null;
  let i = 0;
  re.lastIndex = 0;
  while (i < s.length && (m = re.exec(s))) {
    const x = m[1];
    i = re.lastIndex;
    if (x === '(') out.push({ t: 'lp' });
    else if (x === ')') out.push({ t: 'rp' });
    else if (x === '&&') out.push({ t: 'and' });
    else if (x === '||') out.push({ t: 'or' });
    else {
      const c = /^\$\.([A-Za-z0-9_.]+)\s*(=|!=|NOT EXISTS|EXISTS)\s*(?:"([^"]*)")?$/.exec(x.trim());
      if (!c) throw new Error(`unsupported comparison: ${x}`);
      out.push({ t: 'cmp', path: c[1], op: c[2], val: c[3] });
    }
  }
  if (s.slice(i).trim()) throw new Error(`unsupported filter syntax near: ${s.slice(i, i + 30)}`);
  return out;
}

const get = (o: any, path: string) => path.split('.').reduce((v, k) => (v == null ? undefined : v[k]), o);
const like = (pat: string, v: unknown) => typeof v === 'string' && new RegExp('^' + pat.split('*').map((p) => p.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$').test(v);

export function matches(pattern: string, event: unknown): boolean {
  const toks = tokenize(pattern);
  let pos = 0;
  const expr = (): boolean => {
    let v = term();
    while (toks[pos]?.t === 'or') { pos++; const r = term(); v = v || r; }
    return v;
  };
  const term = (): boolean => {
    let v = factor();
    while (toks[pos]?.t === 'and') { pos++; const r = factor(); v = v && r; }
    return v;
  };
  const factor = (): boolean => {
    const tk = toks[pos++];
    if (!tk) throw new Error('unexpected end of pattern');
    if (tk.t === 'lp') { const v = expr(); if (toks[pos++]?.t !== 'rp') throw new Error('missing )'); return v; }
    if (tk.t !== 'cmp') throw new Error(`unexpected token ${tk.t}`);
    const v = get(event, tk.path!);
    switch (tk.op) {
      case '=': return like(tk.val!, v);
      case '!=': return v !== undefined && !like(tk.val!, v);
      case 'NOT EXISTS': return v === undefined;
      case 'EXISTS': return v !== undefined;
      default: throw new Error(`unsupported operator ${tk.op}`);
    }
  };
  const result = expr();
  if (pos !== toks.length) throw new Error('trailing tokens in pattern');
  return result;
}

/** EventBridge pattern matching for the shapes we use: arrays of exact values, nested objects. */
export function eventMatches(pattern: any, event: any): boolean {
  return Object.entries(pattern).every(([k, want]) => {
    const key = k === 'detailType' ? 'detail-type' : k;
    const have = event[key];
    if (Array.isArray(want)) return want.includes(have);
    if (want && typeof want === 'object') return have && typeof have === 'object' && eventMatches(want, have);
    throw new Error(`unsupported EventBridge pattern for ${k}`);
  });
}
