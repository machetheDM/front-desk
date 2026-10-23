// Fails the build if any exported page contains an inline executable <script>. The CloudFront
// Content-Security-Policy is script-src 'self' (infra/lib/workload/web-stack.ts); an inline
// script would be blocked in the browser, or tempt someone to weaken the policy.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const walk = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
const bad = [];
for (const f of walk("out").filter((f) => f.endsWith(".html"))) {
  const html = readFileSync(f, "utf8");
  for (const m of html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*type="application\/json")[^>]*>/g)) bad.push(`${f}: ${m[0]}`);
}
if (bad.length) {
  console.error("Inline scripts found (blocked by CSP script-src 'self'):\n" + bad.join("\n"));
  process.exit(1);
}
console.log("CSP check: no inline scripts in the static export");
