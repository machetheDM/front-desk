# Integration contract: order status

For the client's developer. One endpoint on your side lets the assistant answer "where is my
order?" for the customer who placed it, and nobody else. Everything else (prices, bookings,
refunds) stays with your staff.

## The request we send

```
POST https://<your host>/<your path>/orders/status
content-type: application/json
x-fd-timestamp: 1730707200
x-fd-signature: 3f1c...e9 (64 hex characters)

{"ref": "ACM-2001", "requester": "+27825550101"}
```

- `ref` is the order reference the customer typed (two to four capital letters, a dash, three
  to six digits). `requester` is the WhatsApp number the message came from, in E.164.
- `x-fd-signature` is `hex(HMAC-SHA256(secret, timestamp + "." + body))`, where `body` is the
  exact bytes received. The secret is 32 random bytes we generate and share once (below).

## What your endpoint must do

1. **Refuse** with HTTP 401 if the signature is missing or wrong, or the timestamp is more than
   five minutes from your clock. Compare signatures in constant time.
2. **Tell the order only to the number on it.** If the order exists *and* its contact number
   matches `requester` (compare the last nine digits), answer
   `{"found": true, "status": "installed", "detail": "installed on 3 November, certificate e-mailed"}`.
   `detail` is one plain sentence for the customer; it must not contain anyone's ID number,
   address or account details.
3. **Otherwise answer exactly `{"found": false}`**, whether the order does not exist or belongs to
   someone else. Two different answers would let anyone test which order numbers exist.
4. **Answer within three seconds.** After that the assistant hands the customer to your staff.
5. **HTTPS only**, with a certificate from a public CA.
6. **Log nothing personal.** The request carries a phone number; your access logs should not.

A reference implementation that does all of this in about thirty lines is
`reference_server` in `backend/frontdesk/integrations/conformance.py`.

## Before go-live: run the conformance kit against your sandbox

You need a sandbox order whose contact number you know. In PowerShell, from a checkout of
this repository with the virtual environment active:

```powershell
$env:FD_ORDERS_SECRET = "paste the shared secret here"
python -m frontdesk.integrations.conformance --base-url https://sandbox.example.co.za/frontdesk --test-ref ACM-2001 --test-number +27825550101
```

You should see eight `PASS` lines and `8 of 8 checks passed`: HTTPS; the order told to its own
number; an answer within three seconds; nothing told to another number; an unknown order
looking the same as someone else's; and 401 for a wrong signature, a ten-minute-old replay and
an unsigned request. Send us the output.

## How the secret is created and shared

We run `admin connect-orders-api`, which generates the secret straight into AWS SSM Parameter
Store as a SecureString; it is never printed, e-mailed or stored in the database. To give it to
your developer, we read it once with `aws ssm get-parameter --with-decryption` and pass it over a
channel you choose (a password manager share, or read out on a call). Rotating it is the same
command again; your side swaps the value at an agreed time.

## What happens when your system is down

The assistant tells the customer "I can't check orders right now", hands the conversation to
the staff member who handles orders, and records `order_lookup_unavailable`. Your outage never
becomes ours, and the customer is never told a guess.
