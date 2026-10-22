"""FAILING EXAMPLE for the Semgrep gate (security/gate-selftest.sh). Each block below must
produce exactly the finding named in its comment. Never imported by anything."""

import logging

import boto3
import requests
import yaml

log = logging.getLogger(__name__)


def sql_injection(tx, name):
    # frontdesk-sql-built-from-strings
    return tx.execute(f"SELECT * FROM app.customers WHERE display_name = '{name}'")


def global_profile():
    # frontdesk-global-inference-profile
    return "global.anthropic.claude-haiku-5-5"


def wrong_region():
    # frontdesk-bedrock-outside-frankfurt
    return boto3.client("bedrock-runtime", region_name="af-south-1")


def logs_text(message):
    # frontdesk-message-text-in-logs
    log.info("received", body=message.body)


def no_tls():
    # frontdesk-tls-verification-disabled
    return requests.get("https://example.invalid", verify=False)


def unsafe_yaml(text):
    # frontdesk-unsafe-yaml
    return yaml.load(text)
