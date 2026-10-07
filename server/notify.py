"""Slack notices via an optional incoming webhook; log only when unset."""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("account-research")


def notify(text: str) -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        log.warning("Slack notice (SLACK_WEBHOOK_URL unset): %s", text)
        return "logged"
    try:
        httpx.post(url, json={"text": text}, timeout=10).raise_for_status()
        return "posted"
    except httpx.HTTPError as e:
        log.warning("Slack notice failed (%s): %s", e, text)
        return "failed"
