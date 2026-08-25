from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from spaces.learning.contracts.ui_intents import UiIntent


class UiBridgeError(RuntimeError):
    """Base error for the loopback-only UI intent transport."""


class UiBridgeTransportError(UiBridgeError):
    pass


class UiBridgeMalformedResponse(UiBridgeError):
    pass


class UiBridgeRevisionConflict(UiBridgeError):
    pass


@dataclass(frozen=True)
class UiDeliveryReceipt:
    accepted: bool
    aggregate_revision: int


def _validate_loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("UI bridge base_url must use an http loopback URL")
    try:
        if not ip_address(parsed.hostname).is_loopback:
            raise ValueError("UI bridge base_url must use a loopback address")
    except ValueError as error:
        if str(error).startswith("UI bridge"):
            raise
        raise ValueError("UI bridge base_url must use a loopback address") from None
    return value.rstrip("/")


class UiBridge:
    """Delivers frozen UI intents to the local renderer without durable secrets."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:5151",
        timeout_seconds: float = 2.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = _validate_loopback_url(base_url)
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.Client()

    def deliver(self, intent: UiIntent, *, correlation_id: UUID) -> UiDeliveryReceipt:
        try:
            response = self._http_client.post(
                f"{self._base_url}/ui/intents",
                headers={"Accept": "application/json", "X-Correlation-ID": str(correlation_id)},
                json=intent.model_dump(mode="json"),
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise UiBridgeTransportError("UI intent delivery failed") from error
        if not 200 <= response.status_code < 300:
            raise UiBridgeTransportError("UI intent delivery failed")
        try:
            value = response.json()
        except ValueError as error:
            raise UiBridgeMalformedResponse("UI bridge returned malformed JSON") from error
        return self._receipt(value, intent)

    @staticmethod
    def _receipt(value: object, intent: UiIntent) -> UiDeliveryReceipt:
        if not isinstance(value, Mapping):
            raise UiBridgeMalformedResponse("UI bridge returned malformed response")
        accepted = value.get("accepted")
        revision = value.get("aggregate_revision")
        if accepted is not True or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise UiBridgeMalformedResponse("UI bridge returned malformed response")
        if revision != intent.aggregate_revision:
            raise UiBridgeRevisionConflict("UI bridge acknowledged a stale revision")
        return UiDeliveryReceipt(accepted=True, aggregate_revision=revision)
