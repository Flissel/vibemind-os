from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from spaces.learning.contracts.ui_intents import UiIntent


_MINIMUM_TOKEN_LENGTH = 32
_MAXIMUM_TOKEN_LENGTH = 512


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
    event_id: str


@dataclass(frozen=True)
class UiDeliveryResult:
    delivered: bool
    receipt: UiDeliveryReceipt | None = None
    error_code: str | None = None


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
        auth_token: str | None,
        timeout_seconds: float = 2.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = _validate_loopback_url(base_url)
        if (
            auth_token is None
            or not _MINIMUM_TOKEN_LENGTH <= len(auth_token) <= _MAXIMUM_TOKEN_LENGTH
            or auth_token.strip() != auth_token
        ):
            raise ValueError("UI bridge token must be a nontrivial runtime token")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.Client()

    def deliver(self, intent: UiIntent, *, correlation_id: UUID) -> UiDeliveryReceipt:
        try:
            response = self._http_client.post(
                f"{self._base_url}/ui/intents",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._auth_token}",
                    "X-Correlation-ID": str(correlation_id),
                },
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

    def try_deliver(self, intent: UiIntent, *, correlation_id: UUID) -> UiDeliveryResult:
        try:
            receipt = self.deliver(intent, correlation_id=correlation_id)
        except UiBridgeRevisionConflict:
            return UiDeliveryResult(delivered=False, error_code="revision_conflict")
        except UiBridgeMalformedResponse:
            return UiDeliveryResult(delivered=False, error_code="malformed_response")
        except UiBridgeTransportError:
            return UiDeliveryResult(delivered=False, error_code="transport_error")
        return UiDeliveryResult(delivered=True, receipt=receipt)

    @staticmethod
    def _receipt(value: object, intent: UiIntent) -> UiDeliveryReceipt:
        if not isinstance(value, Mapping):
            raise UiBridgeMalformedResponse("UI bridge returned malformed response")
        accepted = value.get("accepted")
        revision = value.get("aggregate_revision")
        event_id = value.get("event_id")
        if accepted is not True or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise UiBridgeMalformedResponse("UI bridge returned malformed response")
        if (
            not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 128
            or not event_id[0].isalnum()
            or any(not (character.isalnum() or character in "._:-") for character in event_id)
        ):
            raise UiBridgeMalformedResponse("UI bridge returned malformed response")
        if revision != intent.aggregate_revision:
            raise UiBridgeRevisionConflict("UI bridge acknowledged a stale revision")
        return UiDeliveryReceipt(
            accepted=True,
            aggregate_revision=revision,
            event_id=event_id,
        )
