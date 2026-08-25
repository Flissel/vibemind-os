from __future__ import annotations

import asyncio
from collections.abc import Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken
from pydantic import BaseModel

from spaces.learning.services.course_factory.model_gateway import (
    GatewayResult,
    ModelGateway,
    ModelGatewayError,
    ModelInvocation,
)


class GatewayRoleAgent(BaseChatAgent):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        stage: str,
        prompt_version: str,
        output_schema_version: str,
        correlation_id: str,
        gateway: ModelGateway,
        output_model: type[BaseModel],
    ) -> None:
        super().__init__(name=name, description=description)
        self._stage = stage
        self._prompt_version = prompt_version
        self._output_schema_version = output_schema_version
        self._correlation_id = correlation_id
        self._gateway = gateway
        self._output_model = output_model
        self.last_result: GatewayResult | None = None
        self.last_error: ModelGatewayError | None = None

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        if cancellation_token.is_cancelled():
            raise asyncio.CancelledError
        input_payload = {
            "messages": [
                {"source": message.source, "content": str(message.content)}
                for message in messages
            ]
        }
        try:
            result = await self._gateway.generate(
                ModelInvocation(
                    role=self.name,
                    stage=self._stage,
                    correlation_id=self._correlation_id,
                    prompt_version=self._prompt_version,
                    output_schema_version=self._output_schema_version,
                    input_payload=input_payload,
                ),
                self._output_model,
            )
        except ModelGatewayError as error:
            self.last_error = error
            raise
        self.last_result = result
        return Response(
            chat_message=TextMessage(
                content=result.output.model_dump_json(), source=self.name
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_result = None
        self.last_error = None
