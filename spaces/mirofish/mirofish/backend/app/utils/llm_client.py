"""
LLM Client Wrapper
Uses the configured OpenFang gateway for OpenAI-compatible API calls.
"""

import json
import re
from typing import Optional, Dict, Any, List

from vibemind_shared import get_client_sync, get_model


_MIROFISH_ROLE = "space_mirofish"

# Das OpenFang-Gateway reicht nur die LETZTE Nutzernachricht an den Agenten weiter
# (openfang-api openai_compat: "Extract the last user message as the input"). Systemnachrichten und
# fruehere Nachrichten gehen dort verloren, und response_format wird nicht erzwungen. Deshalb falten wir
# alle Nachrichten in eine einzige Nutzernachricht und verlangen JSON ausdruecklich im Text.
_JSON_HINWEIS = (
    "Antworte ausschliesslich mit einem einzigen gueltigen JSON-Objekt. "
    "Kein Text davor oder danach, keine Erklaerung, keine Markdown-Zaeune. "
    "Fuehre die Aufgabe aus dem Abschnitt ANWEISUNG aus; beantworte nicht stattdessen die Eingabe."
)
_NACHFRAGE = (
    "Das war kein gueltiges JSON-Objekt. Gib dieselbe Antwort jetzt nur als ein einziges gueltiges "
    "JSON-Objekt aus, ohne weiteren Text."
)
_ABSCHNITT = {"system": "ANWEISUNG", "user": "EINGABE", "assistant": "BISHERIGE ANTWORT"}


def fold_messages(messages: List[Dict[str, Any]], json_only: bool = False) -> List[Dict[str, Any]]:
    """Faltet eine Nachrichtenliste in eine einzige Nutzernachricht fuer das Gateway.

    Eine einzelne Nutzernachricht ohne JSON-Pflicht bleibt unveraendert. Nachrichten, deren Inhalt
    kein Text ist, werden nicht angefasst (dann gibt es nichts verlustfrei zu falten).
    """
    if not messages:
        return messages
    if any(not isinstance(m.get("content"), str) for m in messages):
        return messages
    if len(messages) == 1 and messages[0].get("role") == "user" and not json_only:
        return messages
    teile = []
    for m in messages:
        rolle = str(m.get("role") or "user")
        teile.append(f"### {_ABSCHNITT.get(rolle, rolle.upper())}\n{m['content']}")
    if json_only:
        teile.append(f"### AUSGABEFORMAT\n{_JSON_HINWEIS}")
    return [{"role": "user", "content": "\n\n".join(teile)}]


def extract_json(text: str) -> Any:
    """Liest das erste JSON-Objekt aus einer Modellantwort, auch aus Fliesstext oder Markdown-Zaeunen.

    Wirft ValueError, wenn kein JSON-Objekt enthalten ist.
    """
    cleaned = (text or "").strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    while start != -1:
        try:
            wert, _ = decoder.raw_decode(cleaned[start:])
            if isinstance(wert, dict):
                return wert
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{", start + 1)
    raise ValueError("no JSON object in LLM response")


class LLMClient:
    """LLM Client"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 300.0
    ):
        # Retain the public constructor for callers, but gateway configuration is
        # resolved centrally and cannot be overridden per request.
        del api_key, base_url, model, timeout
        self.model = get_model(_MIROFISH_ROLE)
        self.client = get_client_sync(_MIROFISH_ROLE)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send chat request

        Args:
            messages: Message list
            temperature: Temperature parameter
            max_tokens: Max token count
            response_format: Response format (e.g., JSON mode)

        Returns:
            Model response text
        """
        kwargs = {
            "model": self.model,
            "messages": fold_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        # Some models (like MiniMax M2.5) include <think>thinking content in response, need to remove
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Send chat request and return JSON

        Args:
            messages: Message list
            temperature: Temperature parameter
            max_tokens: Max token count

        Returns:
            Parsed JSON object
        """
        response = self.chat(
            messages=fold_messages(messages, json_only=True),
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        try:
            return extract_json(response)
        except ValueError:
            pass
        # Genau eine Nachfrage: die unbrauchbare Antwort mitgeben und nur das JSON verlangen.
        nachfrage = list(messages) + [
            {"role": "assistant", "content": response[:6000]},
            {"role": "user", "content": _NACHFRAGE},
        ]
        zweite = self.chat(
            messages=fold_messages(nachfrage, json_only=True),
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        try:
            return extract_json(zweite)
        except ValueError:
            raise ValueError(f"Invalid JSON format from LLM: {zweite.strip()[:300]}")
