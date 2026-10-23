import { test } from "node:test";
import assert from "node:assert/strict";
import { authorizeUrl, base64url, challengeFor, decodeClaims, tokenRequestBody } from "./pkce.ts";

test("the S256 challenge is base64url(SHA-256(verifier)), checked against an independent implementation", async () => {
  // Expected value from Python: base64.urlsafe_b64encode(hashlib.sha256(v).digest()).rstrip(b"=")
  const verifier = "dBjftJeZ4CVP-mJ92K9-wmPZ_6yHh7bUGQx_Dj7ukOw";
  assert.equal(await challengeFor(verifier), "bEE6muNP2tM4UZ_JDOpeXJHwR9kVfd5OBNkBL0_nhR4");
});

test("base64url has no padding or unsafe characters", () => {
  assert.equal(base64url(new Uint8Array([251, 255, 254])), "-__-");
});

test("the authorize URL asks for a code with S256 PKCE and carries the state", () => {
  const u = new URL(authorizeUrl({ cognitoDomain: "x.auth.af-south-1.amazoncognito.com", clientId: "abc", redirectUri: "https://d.cloudfront.net/inbox/callback/" }, "chal", "st"));
  assert.equal(u.searchParams.get("response_type"), "code");
  assert.equal(u.searchParams.get("code_challenge_method"), "S256");
  assert.equal(u.searchParams.get("state"), "st");
});

test("the token request sends the verifier, not a secret", () => {
  const body = new URLSearchParams(tokenRequestBody({ cognitoDomain: "d", clientId: "abc", redirectUri: "r" }, "code1", "ver1"));
  assert.equal(body.get("code_verifier"), "ver1");
  assert.equal(body.get("client_secret"), null);
});

test("claims are decoded for display", () => {
  const payload = btoa(JSON.stringify({ email: "naledi@bushveldsolar.example", "cognito:groups": ["owner"] })).replace(/=+$/, "");
  assert.equal(decodeClaims(`h.${payload}.s`).email, "naledi@bushveldsolar.example");
});
