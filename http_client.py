"""Small HTTP helpers with retries."""

from __future__ import annotations

import json
import time

import requests


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    try:
        response = requests.post(url, json=payload, headers=headers or {}, timeout=30)
        response.raise_for_status()
        return response.json() if response.text else {}
    except requests.exceptions.HTTPError as error:
        raise RuntimeError(f"HTTP {error.response.status_code}: {error.response.text}") from error
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Network error: {error}") from error


def get_json(url: str, headers: dict[str, str], max_retries: int = 3, retry_delay: float = 2.0) -> dict | list:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 524):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, json.JSONDecodeError) as error:
            last_error = error
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
            else:
                raise RuntimeError(f"Request to {url} failed: {error}") from error
    if last_error:
        raise RuntimeError(f"Request to {url} failed: {last_error}") from last_error
    return {}
