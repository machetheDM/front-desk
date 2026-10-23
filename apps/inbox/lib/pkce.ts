/**
 * OAuth 2.0 authorization code flow with PKCE against the Cognito Hosted UI. The inbox has no
 * client secret (it is a static site), so PKCE is what stops a stolen authorization code being
 * redeemed by anyone else. Pure functions, unit-tested in pkce.test.ts.
 */

export function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomVerifier(bytes = 32): string {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return base64url(a);
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export interface AuthConfig {
  cognitoDomain: string;
  clientId: string;
  redirectUri: string;
}

export function authorizeUrl(cfg: AuthConfig, challenge: string, state: string): string {
  const q = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: "openid email",
    code_challenge_method: "S256",
    code_challenge: challenge,
    state,
  });
  return `https://${cfg.cognitoDomain}/oauth2/authorize?${q.toString()}`;
}

export function tokenRequestBody(cfg: AuthConfig, code: string, verifier: string): string {
  return new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cfg.clientId,
    code,
    redirect_uri: cfg.redirectUri,
    code_verifier: verifier,
  }).toString();
}

/** Reads claims without verifying: the API verifies the signature; the UI only needs names. */
export function decodeClaims(jwt: string): Record<string, unknown> {
  const part = jwt.split(".")[1] ?? "";
  const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
  return JSON.parse(json);
}
