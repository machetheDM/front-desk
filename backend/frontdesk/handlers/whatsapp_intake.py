"""SNS (End User Messaging Social) -> verify -> store -> start the pipeline."""

from __future__ import annotations

import json
import os

from .. import pipeline, routing, telemetry
from ..channels import sns_verify
from ..channels.whatsapp import parse_event


def handler(event, context):
    import boto3

    sfn = boto3.client("stepfunctions")
    started = 0
    for record in event.get("Records", []):
        sns = record.get("Sns", {})
        try:
            sns_verify.verify(sns, expected_topic_arn=os.environ.get("FRONTDESK_WHATSAPP_TOPIC_ARN") or None)
        except sns_verify.SignatureError as e:
            telemetry.security_event("SignatureVerificationFailed", {"source": "sns", "reason": str(e)})
            continue
        for msg in parse_event(json.loads(sns["Message"])):
            route = routing.for_whatsapp(msg.phone_number_id)
            if route is None:
                telemetry.metric("UnroutedWhatsApp")
                continue
            from .common import db

            rec = pipeline.receive(db(), pipeline.Inbound(route.tenant_id, "whatsapp", msg.from_number, msg.text,
                                                          msg.name, msg.message_id))
            if rec.duplicate or not rec.proceed:
                continue
            sfn.start_execution(stateMachineArn=os.environ["FRONTDESK_STATE_MACHINE_ARN"], input=json.dumps({
                "tenant_id": str(route.tenant_id), "conversation_id": rec.conversation_id, "message_id": rec.message_id,
                "customer_id": rec.customer_id, "first_message": rec.first_message, "proceed": True,
                "phone_number_id": msg.phone_number_id}))
            started += 1
    return {"started": started}
