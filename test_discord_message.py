#!/usr/bin/env python3
"""Send a test Discord message via webhook."""

import json
import urllib.request
import os
import sys

webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
message_type = os.environ.get('MESSAGE_TYPE', 'day')

if not webhook_url:
    print('Error: DISCORD_WEBHOOK_URL not set')
    sys.exit(1)

# Keep text format in sync with main.py message()
messages = {
    'day': '📅 **ДЕНЬ 1 THE INTERNATIONAL 2026**\nhttps://www.youtube.com/@Dota2_maincast',
    'live': '🔴 **МАТЧ РОЗПОЧАВСЯ**\n🇪🇺 Team Liquid 🆚 🇨🇳 Xtreme Gaming\nГра 1',
    'game_finished': '🎮 **ГРА 1 ЗАВЕРШИЛАСЯ**\n🇪🇺 Team Liquid 1 — 0 🇨🇳 Xtreme Gaming\n⏱ Тривалість: 42:15',
    'series_finished': '🏆 **МАТЧ ЗАВЕРШИВСЯ**\n🇪🇺 Team Liquid 3 — 1 🇨🇳 Xtreme Gaming\n🥇 Переможець: 🇪🇺 Team Liquid'
}

payload = {
    'content': messages.get(message_type, messages['day']),
    'username': 'The International 2026',
}

req = urllib.request.Request(
    webhook_url,
    data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req, timeout=10) as response:
        print(f'✓ Test message sent! Status: {response.status}')
except Exception as e:
    print(f'✗ Error: {e}')
    sys.exit(1)
