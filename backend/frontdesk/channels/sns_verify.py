"""Verify an Amazon SNS message signature (SignatureVersion 1 = SHA1, 2 = SHA256).

When SNS invokes Lambda directly, IAM already restricts who can publish. We verify anyway: a
misconfigured topic policy, or a future HTTPS subscription, would otherwise accept forged
WhatsApp events. The signing certificate must come from an sns.<region>.amazonaws.com host.
"""

from __future__ import annotations

import base64
import functools
import re
import urllib.request
from urllib.parse import urlparse

CERT_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com$")
KEYS = {
    "Notification": ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
    "SubscriptionConfirmation": ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"],
    "UnsubscribeConfirmation": ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"],
}


class SignatureError(ValueError):
    pass


def string_to_sign(msg: dict) -> bytes:
    keys = KEYS.get(msg.get("Type", ""))
    if not keys:
        raise SignatureError("unknown SNS message type")
    parts = []
    for k in keys:
        if k == "Subject" and "Subject" not in msg:
            continue
        parts += [k, str(msg[k])]
    return ("\n".join(parts) + "\n").encode()


@functools.lru_cache(maxsize=8)
def _fetch_cert(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - host checked by caller
        return r.read()


def verify(msg: dict, fetch=_fetch_cert, expected_topic_arn: str | None = None) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    url = msg.get("SigningCertURL") or msg.get("SigningCertUrl") or ""
    u = urlparse(url)
    if u.scheme != "https" or not CERT_HOST.match(u.hostname or ""):
        raise SignatureError("signing certificate is not from an SNS host")
    if expected_topic_arn and msg.get("TopicArn") != expected_topic_arn:
        raise SignatureError("message is from an unexpected topic")
    algo = {"1": hashes.SHA1(), "2": hashes.SHA256()}.get(str(msg.get("SignatureVersion")))
    if algo is None:
        raise SignatureError("unsupported signature version")
    cert = x509.load_pem_x509_certificate(fetch(url))
    try:
        cert.public_key().verify(base64.b64decode(msg["Signature"]), string_to_sign(msg), padding.PKCS1v15(), algo)
    except Exception as e:
        raise SignatureError("signature does not verify") from e
