"""Conformance kit for a client's order-status API, and a reference server that passes it.

The client's developer runs this against their sandbox before the integration goes live:

    $env:FD_ORDERS_SECRET = "<the shared secret>"
    python -m frontdesk.integrations.conformance --base-url https://sandbox.example.co.za/frontdesk `
        --test-ref BSE-1042 --test-number +27820000001

It checks the contract in docs/engagement/integration-contract.md: signed requests only,
replays refused, the order told only to the number on it (and the same "not found" answer for
an unknown order and for someone else's, so the API is not an oracle), and an answer within the
adapter's three-second timeout. `reference_server` is the smallest implementation that passes;
the tests run the kit against it, so the kit itself is tested.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..channels.webhook_signature import WebhookRejected, sign, verify
from .orders import HttpOrdersAdapter, _same_number

TIMEOUT_S = 3.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _post(url: str, body: bytes, headers: dict) -> tuple[int, dict, float]:
    req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json", **headers})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S + 2) as r:  # nosemgrep -- the URL is the client's sandbox, given by the operator
            return r.status, json.loads(r.read() or b"{}"), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, {}, time.perf_counter() - t0


def run(base_url: str, secret: bytes, test_ref: str, test_number: str, *, allow_loopback_http: bool = False) -> list[Check]:
    url = base_url.rstrip("/") + "/orders/status"
    checks: list[Check] = []
    try:
        adapter = HttpOrdersAdapter(base_url, secret, timeout=TIMEOUT_S, allow_loopback_http=allow_loopback_http)
        checks.append(Check("uses HTTPS", True, base_url.split(":", 1)[0]))
    except ValueError as e:
        return [Check("uses HTTPS", False, str(e))]

    def lookup(name: str, ref: str, number: str, want_found: bool) -> float:
        t0 = time.perf_counter()
        try:
            got = adapter.status(ref, requester_address=number)
            ok = (got is not None and bool(got.status)) if want_found else got is None
            checks.append(Check(name, ok, repr(got)))
        except Exception as e:  # noqa: BLE001 -- a conformance kit reports every failure instead of stopping
            checks.append(Check(name, False, f"{type(e).__name__}: {e}"))
        return time.perf_counter() - t0

    took = lookup("the order is told to the number on it", test_ref, test_number, True)
    checks.append(Check(f"answers within {TIMEOUT_S:g} seconds", took < TIMEOUT_S, f"{took:.2f} s"))
    lookup("someone else's number is told nothing", test_ref, "+27000000000", False)
    lookup("an unknown order looks the same as someone else's (no oracle)", "ZZZ-999999", test_number, False)

    body = json.dumps({"ref": test_ref, "requester": test_number}).encode()
    ts, sig = sign(secret, body)
    code, _, _ = _post(url, body, {"x-fd-timestamp": ts, "x-fd-signature": "0" * 64})
    checks.append(Check("a wrong signature is refused with 401", code == 401, f"HTTP {code}"))
    old_ts, old_sig = sign(secret, body, int(time.time()) - 600)
    code, _, _ = _post(url, body, {"x-fd-timestamp": old_ts, "x-fd-signature": old_sig})
    checks.append(Check("a 10-minute-old signed request (a replay) is refused with 401", code == 401, f"HTTP {code}"))
    code, _, _ = _post(url, body, {})
    checks.append(Check("an unsigned request is refused with 401", code == 401, f"HTTP {code}"))
    return checks


def reference_server(orders: dict[str, dict], secret: bytes, port: int = 0) -> tuple[http.server.HTTPServer, str]:
    """The contract in thirty lines. Started on a free loopback port in a background thread."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 -- http.server's naming
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            try:
                verify(secret, body, self.headers.get("x-fd-timestamp"), self.headers.get("x-fd-signature"))
            except WebhookRejected:
                return self._send(401, {"error": "unauthorised"})
            req = json.loads(body)
            o = orders.get(str(req.get("ref", "")).upper())
            if o is None or not _same_number(o["contact"], req.get("requester")):
                return self._send(200, {"found": False})  # one answer for both cases
            return self._send(200, {"found": True, "status": o["status"], "detail": o["detail"]})

        def _send(self, code: int, data: dict) -> None:
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):  # requests carry phone numbers; keep them out of logs
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--secret-env", default="FD_ORDERS_SECRET", help="environment variable holding the shared secret")
    ap.add_argument("--test-ref", required=True, help="a sandbox order reference")
    ap.add_argument("--test-number", required=True, help="the contact number on that sandbox order")
    a = ap.parse_args(argv)
    secret = os.environ.get(a.secret_env, "").encode()
    if not secret:
        print(f"set {a.secret_env} to the shared secret first", file=sys.stderr)
        return 2
    checks = run(a.base_url, secret, a.test_ref, a.test_number)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}  ({c.detail})")
    print(f"{sum(c.ok for c in checks)} of {len(checks)} checks passed")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
