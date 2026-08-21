#!/usr/bin/env python3
"""Send a test Discord message via webhook using the shared message builder."""

from __future__ import annotations

import os
import sys

from discord_messages import build_test_payload, publish

webhook_url = (os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("WEBHOOK_URL") or "").strip()
message_type = os.environ.get("MESSAGE_TYPE", "day")

if not webhook_url:
    print("Error: DISCORD_WEBHOOK_URL not set")
    sys.exit(1)

payload = build_test_payload(message_type)
try:
    publish(webhook_url, payload)
    print(f"✓ Test message sent ({message_type})")
except Exception as error:
    print(f"✗ Error: {error}")
    sys.exit(1)
