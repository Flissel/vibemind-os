"""Fail-closed Brain routing through configured OpenFang agents."""

from typing import Dict, List, Optional, Any, Tuple, Callable
import inspect
import json
import logging
from dataclasses import dataclass
import time

from vibemind_shared import OpenFangUnavailable, get_client, get_client_sync, get_model

logger = logging.getLogger(__name__)

def get_llm_concurrency_stats() -> Dict[str, Any]:
    """Compatibility endpoint; concurrency belongs to the OpenFang gateway."""
    return {"limits": {}, "providers": {}}


@dataclass
class LLMConfig:
    """OpenFang role configuration for one Brain cognitive function."""
    provider: str
    model: str
    max_tokens: int = 1000
    temperature: float = 0.7
    use_for: List[str] = None  # Which functions this LLM handles
    role: str = "brain_planning"


class MultiLLMRouter:
    """Route Brain functions through OpenFang-configured OpenAI agents."""

    def __init__(
        self,
        openrouter_api_key: str = None,
        default_provider: str = 'anthropic',
        dev_mode: bool = None,
        enable_infinite_chat: bool = True,
        user_id: Optional[str] = None
    ):
        """Initialize without accepting direct provider authority.

        ``openrouter_api_key`` and ``default_provider`` remain accepted only for
        constructor compatibility. Shared role configuration is authoritative.
        """
        if openrouter_api_key is not None or default_provider != "anthropic":
            logger.warning("Ignoring legacy direct LLM configuration; OpenFang is authoritative")
        self.enable_infinite_chat = enable_infinite_chat
        self.user_id = user_id
        self.dev_mode = bool(dev_mode) if dev_mode is not None else False
        self.llm_configs = self._get_openfang_configs()

        # Function to LLM mapping
        self.function_map = {}
        for llm_name, config in self.llm_configs.items():
            for function in config.use_for:
                self.function_map[function] = llm_name

        # Statistics
        self.call_counts = {name: 0 for name in self.llm_configs}
        self.latencies = {name: [] for name in self.llm_configs}
        self.failures = {name: 0 for name in self.llm_configs}

        # Cost accounting belongs to OpenFang, which owns provider selection.
        self.cost_per_million = {name: 0.0 for name in self.llm_configs}
        self.total_tokens_used = {name: 0 for name in self.llm_configs}

    def _get_supermemory_llm(self, user_id: Optional[str] = None):
        """Retained compatibility hook; direct memory-provider calls are disabled."""
        # Infinite-chat performs a separate provider call and is therefore not
        # part of this fail-closed execution path.
        return None

    def set_user_id(self, user_id: Optional[str]):
        """
        Set user ID for memory isolation

        Args:
            user_id: User ID or None to disable per-user memory
        """
        self.user_id = user_id
    def _get_openfang_configs(self) -> Dict[str, LLMConfig]:
        """Resolve every cognitive function from the shared role authority."""
        specs = {
            "fast_reasoning": ("brain_fast_reasoning", 500, 0.3, ["feature_extraction", "decision_making", "fast_inference", "code_understanding"]),
            "planning": ("brain_planning", 1500, 0.7, ["path_planning", "strategy_selection", "complex_understanding"]),
            "context_tracking": ("brain_context_tracking", 1500, 0.5, ["short_term_memory", "context_maintenance", "working_memory"]),
            "communication": ("brain_communication", 800, 0.8, ["question_generation", "user_interaction", "natural_language"]),
            "long_term_memory": ("brain_long_term_memory", 2000, 0.5, ["episodic_memory", "pattern_discovery", "memory_search", "huge_context"]),
        }
        return {
            name: LLMConfig(provider="openfang", role=role, model=self._openfang_model(role), max_tokens=max_tokens, temperature=temperature, use_for=functions)
            for name, (role, max_tokens, temperature, functions) in specs.items()
        }

    @staticmethod
    def get_free_model_configs() -> Dict[str, str]:
        """Return configured OpenFang agent models for legacy callers."""
        return {
            'summarizer': get_model('brain_communication'),
            'connector': get_model('brain_fast_reasoning'),
            'critic': get_model('brain_planning'),
            'enricher': get_model('brain_context_tracking'),
            'responder': get_model('brain_communication'),
            'fallback': get_model('brain_fast_reasoning'),
        }

    def route(
        self,
        function: str,
        prompt: str,
        user_id: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Route a request to the appropriate LLM

        Args:
            function: Cognitive function name
            prompt: Prompt to send
            user_id: Optional user ID for memory isolation (overrides instance user_id)
            **kwargs: Additional parameters

        Returns:
            LLM response text
        """
        self._warn_legacy_overrides(kwargs)
        # Get appropriate LLM
        llm_name = self.function_map.get(function, 'planning')  # Default to planning
        config = self.llm_configs[llm_name]

        # Track call
        self.call_counts[llm_name] += 1

        # Make request
        start_time = time.time()
        try:
            response = self._call_llm(
                model=config.model,
                prompt=prompt,
                max_tokens=kwargs.get('max_tokens', config.max_tokens),
                temperature=kwargs.get('temperature', config.temperature),
                user_id=user_id,
                role=config.role,
            )

            # Guard against None response
            if response is None:
                response = ""

            latency = (time.time() - start_time) * 1000
            self.latencies[llm_name].append(latency)

            # Estimate tokens used (rough: prompt + response / 4 chars per token)
            estimated_tokens = (len(prompt) + len(response)) / 4
            self.total_tokens_used[llm_name] += estimated_tokens

            return response

        except OpenFangUnavailable:
            self.failures[llm_name] += 1
            logger.exception("OpenFang unavailable for Brain function %s", function)
            raise
        except Exception:
            self.failures[llm_name] += 1
            logger.exception("OpenFang execution failed for Brain function %s", function)
            raise

    def _call_llm(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        user_id: Optional[str] = None,
        role: str = "brain_planning",
    ) -> str:
        """Synchronously execute the configured role; ``model`` is legacy-only."""
        self._warn_legacy_model(model, role)
        return self._complete_sync(role, prompt, max_tokens, temperature)

    def _call_openrouter(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float
    ) -> str:
        """Compatibility shim for legacy private callers, routed via OpenFang."""
        self._warn_legacy_model(model, "brain_planning")
        return self._complete_sync("brain_planning", prompt, max_tokens, temperature)

    async def aroute(
        self,
        function: str,
        prompt: str,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Asynchronously execute the configured OpenFang role."""
        self._warn_legacy_overrides(kwargs)
        llm_name = self.function_map.get(function, 'planning')
        config = self.llm_configs[llm_name]
        self.call_counts[llm_name] += 1

        start_time = time.time()
        try:
            response = await self._acall_llm(
                model=config.model,
                prompt=prompt,
                max_tokens=kwargs.get('max_tokens', config.max_tokens),
                temperature=kwargs.get('temperature', config.temperature),
                user_id=user_id,
                role=config.role,
            )
            if response is None:
                response = ""
            latency = (time.time() - start_time) * 1000
            self.latencies[llm_name].append(latency)
            estimated_tokens = (len(prompt) + len(response)) / 4
            self.total_tokens_used[llm_name] += estimated_tokens
            return response
        except OpenFangUnavailable:
            self.failures[llm_name] += 1
            logger.exception("OpenFang unavailable for Brain function %s", function)
            raise
        except Exception:
            self.failures[llm_name] += 1
            logger.exception("OpenFang execution failed for Brain function %s", function)
            raise

    async def _acall_llm(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        user_id: Optional[str] = None,
        role: str = "brain_planning",
    ) -> str:
        """Async role execution; ``model`` is accepted but not authoritative."""
        self._warn_legacy_model(model, role)
        return await self._complete_async(role, prompt, max_tokens, temperature)

    async def _acall_openrouter(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Compatibility shim for legacy private callers, routed via OpenFang."""
        self._warn_legacy_model(model, "brain_planning")
        return await self._complete_async("brain_planning", prompt, max_tokens, temperature)

    def _call_openrouter_with_tools(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_executors: Dict[str, Callable],
        max_tokens: int = 300,
        temperature: float = 0.6,
        max_rounds: int = 3,
    ) -> Tuple[Optional[str], int]:
        """Run legacy tool use through the configured planning OpenFang agent."""
        self._warn_legacy_model(model, "brain_planning")
        conversation = list(messages)
        rounds_used = 0
        last_content: Optional[str] = None
        try:
            for _round in range(max_rounds + 1):
                response = get_client_sync("brain_planning").chat.completions.create(
                    model=self._openfang_model("brain_planning"),
                    messages=conversation,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                message = response.choices[0].message
                content = getattr(message, "content", None)
                tool_calls = getattr(message, "tool_calls", None)
                if content:
                    last_content = str(content)
                if not tool_calls:
                    return content or last_content, rounds_used
                if rounds_used >= max_rounds:
                    return last_content, rounds_used
                conversation.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    function = getattr(call, "function", None)
                    name = getattr(function, "name", "")
                    arguments = getattr(function, "arguments", "{}")
                    try:
                        parsed = json.loads(arguments)
                    except (TypeError, json.JSONDecodeError):
                        parsed = {}
                    executor = tool_executors.get(name)
                    try:
                        result = executor(parsed) if executor else f"Error: Unknown tool '{name}'"
                    except Exception as exc:
                        result = f"Error: Tool execution failed — {exc}"
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", ""),
                        "content": str(result),
                    })
                rounds_used += 1
        except OpenFangUnavailable:
            logger.exception("OpenFang unavailable for Brain tool execution")
            raise
        except Exception as exc:
            logger.debug("OpenFang tool execution error: %s", exc)
            return None, rounds_used
        return last_content, rounds_used

    @staticmethod
    def _openfang_model(role: str) -> str:
        model = str(get_model(role))
        if not model.startswith("openfang:"):
            raise RuntimeError(f"role {role!r} is not configured for OpenFang")
        return model

    @staticmethod
    def _response_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        content = getattr(getattr(choices[0], "message", None), "content", "")
        return str(content or "")

    @staticmethod
    def _warn_legacy_model(model: str, role: str) -> None:
        if model != get_model(role):
            logger.warning("Ignoring legacy model override for %s; OpenFang role configuration is authoritative", role)

    @staticmethod
    def _warn_legacy_overrides(kwargs: Dict[str, Any]) -> None:
        if "model" in kwargs or "provider" in kwargs:
            logger.warning("Ignoring legacy model/provider override; OpenFang role configuration is authoritative")

    def _complete_sync(self, role: str, prompt: str, max_tokens: int, temperature: float) -> str:
        response = get_client_sync(role).chat.completions.create(
            model=self._openfang_model(role),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self._response_text(response)

    async def _complete_async(self, role: str, prompt: str, max_tokens: int, temperature: float) -> str:
        client = get_client(role)
        if inspect.isawaitable(client):
            client = await client
        response = client.chat.completions.create(
            model=self._openfang_model(role),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if inspect.isawaitable(response):
            response = await response
        return self._response_text(response)

    # Specialized methods for each cognitive function

    def extract_features(self, task_description: str) -> Dict[str, Any]:
        """
        Extract task features through the configured fast-reasoning agent.

        Args:
            task_description: Task description

        Returns:
            Extracted features
        """
        prompt = f"""Extract task features from this description. Be precise with complexity estimation.

Task: "{task_description}"

COMPLEXITY GUIDELINES:
- 0.0-0.3: Simple, single-step tasks (list, read, write a file)
- 0.3-0.5: Medium, 2-3 steps (edit and test, build and run)
- 0.5-0.7: Complex, multiple steps with dependencies (deploy with tests and monitoring)
- 0.7-0.9: Very complex, system design (architecture, distributed systems, multiple components)
- 0.9-1.0: Extremely complex, research-level (novel algorithms, optimization problems)

TASK TYPES:
- "docker": Docker/container operations
- "github": Git/GitHub operations
- "filesystem": File/directory operations
- "terminal": Shell/command-line operations
- "network": Network/API operations
- "design": System design, architecture planning
- "debugging": Bug fixing, troubleshooting
- "testing": Test writing, QA
- "unknown": Cannot determine type

URGENCY INDICATORS:
- High (0.7-1.0): "urgent", "ASAP", "emergency", "critical", "now"
- Medium (0.4-0.6): "soon", "today", "important"
- Low (0.0-0.3): "eventually", "when possible", no time indicators

Return ONLY valid JSON:
{{
  "task_type": "one of the types above",
  "complexity": 0.0-1.0,
  "urgency": 0.0-1.0,
  "keywords": ["key", "words", "from", "task"]
}}"""

        response = self.route('feature_extraction', prompt, temperature=0.3)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            # Fallback
            return {
                "task_type": "unknown",
                "complexity": 0.5,
                "urgency": 0.5,
                "keywords": []
            }

    def plan_sequence(
        self,
        task_description: str,
        task_type: str,
        available_states: List[str]
    ) -> Dict[str, Any]:
        """
        Plan an action sequence through the configured planning agent.

        Args:
            task_description: Task description
            task_type: Task type
            available_states: Available brain states

        Returns:
            Planned sequence
        """
        prompt = f"""Plan a sequence of brain states for this task.

Task: "{task_description}"
Task Type: {task_type}
Available States: {', '.join(available_states)}

Return JSON with:
{{
  "sequence": ["state1", "state2", "state3"],
  "reasoning": "Why this sequence",
  "confidence": 0.0-1.0
}}"""

        response = self.route('path_planning', prompt, temperature=0.7)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return {
                "sequence": ["start", "process", "complete"],
                "reasoning": "Default sequence",
                "confidence": 0.5
            }

    def make_decision(
        self,
        task_description: str,
        context: Dict[str, Any],
        options: List[str]
    ) -> Dict[str, Any]:
        """
        Make a decision through the configured fast-reasoning agent.

        Args:
            task_description: Task description
            context: Decision context
            options: Available options

        Returns:
            Decision
        """
        prompt = f"""Make a decision for this task.

Task: "{task_description}"
Context: {json.dumps(context, indent=2)}
Options: {', '.join(options)}

Return JSON with:
{{
  "decision": "execute|wait|suggest|retry|terminate",
  "confidence": 0.0-1.0,
  "reasoning": "Why this decision"
}}"""

        response = self.route('decision_making', prompt, temperature=0.3)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return {
                "decision": "wait",
                "confidence": 0.5,
                "reasoning": "Default decision"
            }

    def generate_questions(
        self,
        task_description: str,
        hypotheses: List[Dict],
        uncertainty: float
    ) -> List[Dict[str, Any]]:
        """
        Generate questions through the configured communication agent.

        Args:
            task_description: Task description
            hypotheses: Current hypotheses
            uncertainty: Total uncertainty

        Returns:
            List of questions
        """
        hyp_text = "\n".join([
            f"{i+1}. {h['description']} (probability: {h['probability']:.1%})"
            for i, h in enumerate(hypotheses[:3])
        ])

        prompt = f"""Generate 1-2 intelligent clarifying questions.

Task: "{task_description}"

Current interpretations:
{hyp_text}

Uncertainty: {uncertainty:.2f}

Generate natural, context-aware questions that:
1. Help distinguish between interpretations
2. Reduce uncertainty about user intent
3. Are specific to this task domain

Return JSON array:
[
  {{
    "question": "Your question here?",
    "purpose": "Why this helps",
    "expected_info_gain": 0.0-1.0
  }}
]"""

        response = self.route('question_generation', prompt, temperature=0.8)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return [{
                "question": "Could you clarify what you'd like me to focus on?",
                "purpose": "Get clarification",
                "expected_info_gain": 0.5
            }]

    def search_long_term_memory(
        self,
        query: str,
        memory_context: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search long-term memory through the configured knowledge agent.

        Args:
            query: Search query
            memory_context: Full memory context (can be huge!)
            top_k: Number of results

        Returns:
            Relevant memories
        """
        prompt = f"""Search through these past experiences for relevant ones.

Query: "{query}"

Past Experiences:
{memory_context}

Return top {top_k} most relevant experiences as JSON:
[
  {{
    "task": "Task description",
    "outcome": "success|failure",
    "relevance": 0.0-1.0,
    "why_relevant": "Explanation"
  }}
]"""

        response = self.route('memory_search', prompt, temperature=0.5, max_tokens=2000)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return []

    def maintain_short_term_context(
        self,
        recent_tasks: List[Dict],
        current_task: str
    ) -> Dict[str, Any]:
        """
        Maintain short-term context using CLAUDE SONNET 4.5

        Args:
            recent_tasks: Recent tasks (last 5-10)
            current_task: Current task

        Returns:
            Context summary
        """
        recent_text = "\n".join([
            f"- {t['task']} -> {t['outcome']}"
            for t in recent_tasks[-10:]
        ])

        prompt = f"""Analyze recent task history for context.

Recent tasks:
{recent_text}

Current task: "{current_task}"

Return JSON with:
{{
  "pattern": "Detected pattern or 'none'",
  "similar_tasks": ["task1", "task2"],
  "recommended_approach": "Based on recent history",
  "context_summary": "Brief summary"
}}"""

        response = self.route('context_maintenance', prompt, temperature=0.5)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return {
                "pattern": "none",
                "similar_tasks": [],
                "recommended_approach": "default",
                "context_summary": "No recent context"
            }

    def get_statistics(self) -> Dict[str, Any]:
        """Get usage statistics for all LLMs"""
        stats = {}

        total_cost = 0.0

        for llm_name, config in self.llm_configs.items():
            calls = self.call_counts[llm_name]
            latencies = self.latencies[llm_name]
            failures = self.failures[llm_name]
            tokens = self.total_tokens_used[llm_name]

            # Calculate cost
            cost_per_token = self.cost_per_million.get(llm_name, 0) / 1_000_000
            estimated_cost = tokens * cost_per_token
            total_cost += estimated_cost

            stats[llm_name] = {
                'provider': config.provider,
                'model': config.model,
                'total_calls': calls,
                'failures': failures,
                'success_rate': (calls - failures) / max(1, calls),
                'avg_latency_ms': sum(latencies) / len(latencies) if latencies else 0,
                'min_latency_ms': min(latencies) if latencies else 0,
                'max_latency_ms': max(latencies) if latencies else 0,
                'tokens_used': int(tokens),
                'estimated_cost_usd': round(estimated_cost, 4),
                'use_for': config.use_for
            }

        # Overall stats
        total_calls = sum(self.call_counts.values())
        total_failures = sum(self.failures.values())
        total_tokens = sum(self.total_tokens_used.values())

        stats['overall'] = {
            'total_calls': total_calls,
            'total_failures': total_failures,
            'overall_success_rate': (total_calls - total_failures) / max(1, total_calls),
            'total_tokens_used': int(total_tokens),
            'total_estimated_cost_usd': round(total_cost, 4),
            'cost_per_call': round(total_cost / max(1, total_calls), 6)
        }

        return stats

    def __repr__(self):
        return (
            f"MultiLLMRouter("
            f"llms={len(self.llm_configs)}, "
            f"calls={sum(self.call_counts.values())}, "
            "provider='openfang')"
        )


if __name__ == "__main__":
    print("MultiLLMRouter routes Brain cognitive functions through OpenFang.")
