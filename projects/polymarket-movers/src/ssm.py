"""Telegram credentials, read from SSM Parameter Store at cold start.

Terraform creates these parameters with a placeholder and then ignores their
value, so the real token is set out of band and never touches the repo or the
state file.
"""

from __future__ import annotations

import logging

import boto3

log = logging.getLogger(__name__)

# What Terraform seeds the parameters with. Reaching Telegram with this would
# just 401 on every send, so fail loudly at startup instead.
PLACEHOLDER = "REPLACE_ME"


class SecretsError(RuntimeError):
    """A credential is missing or still holds its Terraform placeholder."""


def fetch(bot_token_param: str, chat_id_param: str):
    """Return (bot_token, chat_id), decrypted."""
    client = boto3.client("ssm")
    response = client.get_parameters(
        Names=[bot_token_param, chat_id_param], WithDecryption=True
    )

    missing = response.get("InvalidParameters") or []
    if missing:
        raise SecretsError("SSM parameters not found: {}".format(", ".join(missing)))

    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    token = values.get(bot_token_param, "")
    chat_id = values.get(chat_id_param, "")

    for name, value in ((bot_token_param, token), (chat_id_param, chat_id)):
        if not value:
            raise SecretsError("{} is empty".format(name))
        if value == PLACEHOLDER:
            raise SecretsError(
                "{} still holds the Terraform placeholder; set the real value with "
                "`aws ssm put-parameter --overwrite`".format(name)
            )

    return token, chat_id
