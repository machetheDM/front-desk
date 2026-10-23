/**
 * Session handling. Tokens live in memory only (ADR 0005): a script injected into the page can
 * still use them while the tab is open, but nothing persists them for later theft. A full page
 * reload means signing in again, which Cognito's own session cookie makes quick.
 * The PKCE verifier and state survive the redirect in sessionStorage; they are single-use.
 */
import { authorizeUrl, challengeFor, decodeClaims, randomVerifier, tokenRequestBody } from "./pkce";

export interface RuntimeConfig {
  apiUrl: string;
  wsUrl: string;
  region: string;
  userPoolId: string;
  clientId: string;
  cognitoDomain: string;
}

let idToken: string | null = null;
let config: RuntimeConfig | null = null;

export async function loadConfig(): Promise<RuntimeConfig> {
  if (config) return config;
  const r = await fetch("/inbox/config.json", { cache: "no-store" });
  if (!r.ok) throw new Error("inbox config missing: deploy FrontDesk-<env>-App");
  config = (await r.json()) as RuntimeConfig;
  return config;
}

function redirectUri(): string {
  return `${window.location.origin}/inbox/callback/`;
}

export async function signIn(): Promise<void> {
  const cfg = await loadConfig();
  const verifier = randomVerifier();
  const state = randomVerifier(16);
  sessionStorage.setItem("fd_pkce", JSON.stringify({ verifier, state, back: window.location.pathname + window.location.search }));
  window.location.assign(authorizeUrl({ cognitoDomain: cfg.cognitoDomain, clientId: cfg.clientId, redirectUri: redirectUri() }, await challengeFor(verifier), state));
}

export async function completeSignIn(code: string, state: string): Promise<string> {
  const cfg = await loadConfig();
  const saved = JSON.parse(sessionStorage.getItem("fd_pkce") ?? "{}");
  sessionStorage.removeItem("fd_pkce");
  if (!saved.state || saved.state !== state) throw new Error("sign-in state mismatch: start again");
  const r = await fetch(`https://${cfg.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: tokenRequestBody({ cognitoDomain: cfg.cognitoDomain, clientId: cfg.clientId, redirectUri: redirectUri() }, code, saved.verifier),
  });
  if (!r.ok) throw new Error("token exchange failed");
  idToken = ((await r.json()) as { id_token: string }).id_token;
  return saved.back || "/inbox/";
}

export function signedIn(): boolean {
  return idToken !== null;
}

export function who(): { email: string; owner: boolean } | null {
  if (!idToken) return null;
  const c = decodeClaims(idToken);
  const groups = (c["cognito:groups"] as string[] | undefined) ?? [];
  return { email: String(c.email ?? ""), owner: groups.includes("owner") };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const cfg = await loadConfig();
  if (!idToken) throw new Error("not signed in");
  const r = await fetch(`${cfg.apiUrl}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), authorization: `Bearer ${idToken}`, "content-type": "application/json" },
  });
  if (r.status === 401) {
    idToken = null;
    throw new Error("session expired: sign in again");
  }
  return (await r.json()) as T;
}
