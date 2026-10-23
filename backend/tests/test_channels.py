"""Inbound events are verified; widget keys route to one tenant from allowed origins only."""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid

import pytest

from frontdesk import routing
from frontdesk.channels import sns_verify, webhook_signature
from frontdesk.channels.whatsapp import WhatsAppSender, parse_event


@pytest.fixture(scope="module")
def signer():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.af-south-1.amazonaws.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(dt.datetime(2026, 1, 1)).not_valid_after(dt.datetime(2027, 1, 1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)

    def sign(msg):
        msg["Signature"] = base64.b64encode(key.sign(sns_verify.string_to_sign(msg), padding.PKCS1v15(), hashes.SHA256())).decode()
        return msg

    return sign, (lambda url: pem)


def sns_msg(**over):
    m = {"Type": "Notification", "MessageId": "m1", "TopicArn": "arn:aws:sns:af-south-1:1:frontdesk-dev-whatsapp-events",
         "Message": "{}", "Timestamp": "2026-10-22T20:00:00.000Z", "SignatureVersion": "2",
         "SigningCertURL": "https://sns.af-south-1.amazonaws.com/SimpleNotificationService-x.pem"}
    m.update(over)
    return m


def test_a_correctly_signed_sns_message_verifies(signer):
    sign, fetch = signer
    sns_verify.verify(sign(sns_msg()), fetch=fetch, expected_topic_arn="arn:aws:sns:af-south-1:1:frontdesk-dev-whatsapp-events")


def test_a_tampered_message_does_not_verify(signer):
    sign, fetch = signer
    m = sign(sns_msg())
    m["Message"] = '{"forged": true}'
    with pytest.raises(sns_verify.SignatureError):
        sns_verify.verify(m, fetch=fetch)


def test_a_certificate_from_a_non_sns_host_is_refused(signer):
    sign, fetch = signer
    with pytest.raises(sns_verify.SignatureError, match="not from an SNS host"):
        sns_verify.verify(sign(sns_msg(SigningCertURL="https://evil.example/sns.pem")), fetch=fetch)


def test_a_message_from_another_topic_is_refused(signer):
    sign, fetch = signer
    with pytest.raises(sns_verify.SignatureError, match="unexpected topic"):
        sns_verify.verify(sign(sns_msg(TopicArn="arn:aws:sns:af-south-1:1:other")), fetch=fetch, expected_topic_arn="arn:aws:sns:af-south-1:1:frontdesk-dev-whatsapp-events")


def test_webhook_signatures_reject_forgery_and_replay():
    secret, body = b"s3cret-for-tests", b'{"ref": "BSE-1042"}'
    ts, sig = webhook_signature.sign(secret, body, ts=1_760_000_000)
    webhook_signature.verify(secret, body, ts, sig, now=1_760_000_100)
    with pytest.raises(webhook_signature.WebhookRejected, match="mismatch"):
        webhook_signature.verify(secret, body + b" ", ts, sig, now=1_760_000_100)
    with pytest.raises(webhook_signature.WebhookRejected, match="window"):
        webhook_signature.verify(secret, body, ts, sig, now=1_760_000_000 + 3600)
    with pytest.raises(webhook_signature.WebhookRejected, match="missing"):
        webhook_signature.verify(secret, body, None, sig)


def test_a_whatsapp_event_is_parsed_into_number_name_text_and_message_id():
    entry = {"changes": [{"value": {"metadata": {"phone_number_id": "123"},
                                    "contacts": [{"wa_id": "27820000042", "profile": {"name": "Lerato"}}],
                                    "messages": [{"from": "27820000042", "id": "wamid.X", "timestamp": "1760000000",
                                                  "type": "text", "text": {"body": "How much is the 3kW?"}}]}}]}
    event = {"context": {"MetaPhoneNumberIds": [{"arn": "arn:aws:social-messaging:af-south-1:1:phone-number-id/phone-number-id-abc"}]},
             "whatsAppWebhookEntry": json.dumps(entry)}
    [m] = parse_event(event)
    assert (m.phone_number_id, m.from_number, m.name, m.text, m.message_id) == ("phone-number-id-abc", "+27820000042", "Lerato", "How much is the 3kW?", "wamid.X")


def test_the_whatsapp_reply_payload_has_no_link_previews():
    class C:
        def send_whatsapp_message(self, **kw):
            self.kw = kw
            return {"messageId": "m"}

    c = C()
    WhatsAppSender(client=c).send_text("phone-number-id-abc", "+27820000042", "R62 900")
    body = json.loads(c.kw["message"])
    assert body["to"] == "27820000042" and body["text"]["preview_url"] is False


def test_a_widget_key_only_reaches_its_own_tenant_and_only_from_allowed_origins(monkeypatch):
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    k1, k2 = routing.new_widget_key(), routing.new_widget_key()
    monkeypatch.setenv("FRONTDESK_ROUTING_JSON", json.dumps({
        f"routing/widget/{routing.key_hash(k1)}": {"tenant_id": str(t1), "origins": ["https://a.example"]},
        f"routing/widget/{routing.key_hash(k2)}": {"tenant_id": str(t2), "origins": ["https://b.example"]}}))
    routing._local.cache_clear()
    assert routing.for_widget(k1, "https://a.example").tenant_id == t1
    assert routing.for_widget(k1, "https://b.example") is None
    assert routing.for_widget("fdk_live_" + "x" * 32, "https://a.example") is None
    routing._local.cache_clear()
