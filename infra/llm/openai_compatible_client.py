"""
Generic OpenAI-compatible client — works with OpenAI, Groq, xAI/Grok,
OpenRouter, Google Gemini (via OpenAI-compat endpoint), and local Ollama.

Fixes applied in this version:
──────────────────────────────
1. finish_reason=length retry now fires for ALL providers, not just Gemini.
   gpt-oss-120b on Groq truncates JSON mid-object — we double the token
   budget and ask the model to produce complete JSON or fewer test cases.

2. _is_groq and _is_openrouter flags properly set so inline_schema_refs
   runs for every provider that needs $ref flattening.

3. Groq reasoning_effort="low" applied to gpt-oss-120b and gpt-oss-20b so
   those models don't burn their thinking budget on structured output tasks.

4. _repair_error_message helper strips failed_generation from error bodies
   before echoing them into repair messages — prevents a truncated multi-KB
   generation from filling the context window of the repair call.

5. schema validation error marker list extended with Groq-specific strings.

6. A successful HTTP response that omits the forced tool call is retried with
   an explicit repair prompt. Some OpenAI-compatible providers occasionally
   return assistant prose with finish_reason=stop despite tool_choice being
   required; treating that as immediately fatal caused intermittent 502s.
"""

import json
import os
from typing import Any

from openai import AsyncOpenAI, BadRequestError
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from infra.llm.base import LLMClient, LLMStructuredResult
from infra.llm.gemini_schema import inline_schema_refs
from infra.llm.rate_limiter import get_llm_semaphore
from infra.llm.retry import with_retry

_SCHEMA_ERROR_MARKERS = (
    "tool_use_failed",
    "did not match schema",
    "tool call validation failed",
    "required property",
    "missing required",
)
_PARSE_ERROR_MARKERS = (
    "failed to parse tool call arguments",
    "invalid json",
    "json parse",
    "unterminated string",
    "unexpected end",
)


def _is_schema_validation_error(exc: BadRequestError) -> bool:
    return any(marker in str(exc).lower() for marker in _SCHEMA_ERROR_MARKERS)


def _is_json_parse_error(exc: BadRequestError) -> bool:
    return any(marker in str(exc).lower() for marker in _PARSE_ERROR_MARKERS)


def _repair_error_message(exc: BadRequestError) -> str:
    """
    Extract only the human-readable error message from a BadRequestError.
    Never include failed_generation — it can be thousands of tokens of
    truncated JSON which would overflow the repair call's context window.
    """
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    if isinstance(error, dict):
        msg = error.get("message", "Tool output did not match the JSON schema.")
        # Strip failed_generation from the message if present
        if "failed_generation" in msg:
            msg = msg.split("failed_generation")[0].strip().rstrip(",").rstrip("'")
    else:
        msg = "Invalid tool output."
    return str(msg)[:600]


# Models on Groq that benefit from reasoning_effort="low" for structured tasks
_GROQ_LOW_REASONING_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str | None, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self.model = model
        self._max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))

        host = self._client.base_url.host
        self._is_gemini = host == "generativelanguage.googleapis.com" and model.startswith("gemini-")
        self._is_groq = host == "api.groq.com"
        self._is_openrouter = host == "openrouter.ai"
        # Any provider whose schema validator chokes on $ref needs inlining
        self._needs_schema_inline = self._is_groq or self._is_gemini or self._is_openrouter

    async def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2000,
    ) -> LLMStructuredResult:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Inline $ref for providers that don't resolve JSON Schema references
        if self._needs_schema_inline:
            tool_schema = inline_schema_refs(tool_schema)

        # Start with a generous token budget — truncation is the #1 failure
        # mode for structured output tasks that generate multiple test cases.
        # Gemini needs extra headroom for its shared thinking/output budget.
        if self._is_gemini:
            response_tokens = max(max_tokens, min(max_tokens * 4, 16000))
        else:
            response_tokens = max_tokens

        input_tokens = output_tokens = 0

        # Per-model options
        request_options: dict[str, Any] = {}
        if self._is_groq and self.model in _GROQ_LOW_REASONING_MODELS:
            # These models run chain-of-thought internally; "low" stops them
            # spending their budget thinking about structured output format
            request_options["reasoning_effort"] = "low"
        if self._is_gemini and self.model.startswith("gemini-2.5-"):
            request_options["reasoning_effort"] = "low"

        async def _do_call() -> ChatCompletion:
            async with get_llm_semaphore():
                return await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=response_tokens,
                    messages=messages,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": (
                                    f"Return the structured {tool_name} result. "
                                    "This is the only way to respond."
                                ),
                                "parameters": tool_schema,
                            },
                        }
                    ],
                    tool_choice={"type": "function", "function": {"name": tool_name}},
                    **request_options,
                )

        max_repair_attempts = 2
        response: ChatCompletion | None = None

        for attempt in range(max_repair_attempts + 1):
            try:
                response = await with_retry(_do_call, max_retries=self._max_retries)
                if response.usage:
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens

                finish = response.choices[0].finish_reason if response.choices else None

                # ── Truncation: JSON cut mid-object ──────────────────────
                # Fires for ALL providers when finish_reason=length.
                # On Groq (gpt-oss-120b) this is the primary failure mode.
                if finish == "length":
                    max_budget = 32000 if self._is_gemini else 16000
                    if attempt < max_repair_attempts and response_tokens < max_budget:
                        response_tokens = min(response_tokens * 2, max_budget)
                        messages = [messages[0], messages[1]]  # reset to original only
                        messages.append({
                            "role": "user",
                            "content": (
                                "Your previous response was cut off mid-JSON because the output "
                                f"exceeded the token limit. Call {tool_name} again with COMPLETE, "
                                "valid JSON — every object and array properly closed. "
                                "If you cannot fit all items in the budget, generate FEWER items "
                                "but ensure every item you do generate is complete and valid."
                            ),
                        })
                        continue
                    raise RuntimeError(
                        f"Model '{self.model}' exhausted the {response_tokens}-token response "
                        f"budget (finish_reason=length) while generating '{tool_name}'. "
                        "Increase max_tokens in the agent call or reduce the number of ACs "
                        "processed per batch."
                    )

                # ── Gemini malformed function call ────────────────────────
                if self._is_gemini and finish and "MALFORMED_FUNCTION_CALL" in finish:
                    if attempt == max_repair_attempts:
                        raise RuntimeError(
                            f"Model '{self.model}' returned MALFORMED_FUNCTION_CALL for "
                            f"'{tool_name}' after {max_repair_attempts} repair attempts."
                        )
                    response_tokens = min(max(response_tokens, response_tokens * 2), 32000)
                    messages = [messages[0], messages[1]]
                    messages.append({
                        "role": "user",
                        "content": (
                            f"The provider rejected the previous function call. "
                            f"Call only {tool_name} once with a complete root object. "
                            "Use strict JSON — no comments, no trailing commas."
                        ),
                    })
                    continue

                # Some OpenAI-compatible providers occasionally acknowledge a
                # forced tool request with ordinary assistant text and
                # finish_reason=stop. This is recoverable, unlike content or
                # safety filtering, and should not surface as an intermittent
                # 502 to callers after a single response.
                message = response.choices[0].message if response.choices else None
                matching_tool_call = bool(
                    message
                    and message.tool_calls
                    and any(
                        call.type == "function" and call.function.name == tool_name
                        for call in message.tool_calls
                    )
                )
                if not matching_tool_call and finish in (None, "stop", "tool_calls"):
                    if attempt < max_repair_attempts:
                        messages = [messages[0], messages[1]]
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Your previous response did not call the required {tool_name} "
                                "function. Do not answer in prose. Call that function exactly "
                                "once with complete JSON matching its schema."
                            ),
                        })
                        continue

                break  # success

            except BadRequestError as exc:
                if not (_is_json_parse_error(exc) or _is_schema_validation_error(exc)):
                    raise
                if attempt >= max_repair_attempts:
                    raise RuntimeError(
                        f"Model '{self.model}' returned a tool call that didn't match the "
                        f"'{tool_name}' schema, and {max_repair_attempts} repair "
                        f"attempt(s) also failed: {_repair_error_message(exc)}"
                    ) from exc

                # Reset conversation to original two messages before repair prompt
                messages = [messages[0], messages[1]]

                if _is_json_parse_error(exc):
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your last response could not be parsed as JSON "
                            f"({_repair_error_message(exc)}). "
                            "This almost always means you added a // comment, a /* */ comment, "
                            "or a trailing comma somewhere in the JSON. "
                            f"Call {tool_name} again with STRICT, plain JSON — "
                            "zero comments, no trailing commas. "
                            "Put any explanatory notes inside a string field value, "
                            "never as a JSON comment."
                        ),
                    })
                else:
                    # Schema validation error — the model used wrong field names or values
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your last response did not match the required schema:\n"
                            f"{_repair_error_message(exc)}\n\n"
                            f"Call {tool_name} again and fix EXACTLY the violations above. "
                            "Common mistakes:\n"
                            "- action must be one of: navigate, fill, click, assert, wait\n"
                            "- category must be exactly: POSITIVE, NEGATIVE, or EDGE_CASE\n"
                            "- traceability must be a non-empty list of AC ids like ['AC-1']\n"
                            "- every step needs step_number starting at 1, consecutive\n"
                            "Use exact property names from the schema."
                        ),
                    })
                continue

        # ── Parse the successful response ─────────────────────────────────
        if not response or not response.choices:
            raise RuntimeError(f"Model '{self.model}' returned no choices for '{tool_name}'.")

        message = response.choices[0].message
        if message.tool_calls:
            for call in message.tool_calls:
                if call.type == "function" and call.function.name == tool_name:
                    try:
                        parsed = json.loads(call.function.arguments)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Model '{self.model}' returned syntactically invalid JSON for "
                            f"'{tool_name}' even after repair attempts: {exc}"
                        ) from exc
                    return LLMStructuredResult(
                        data=parsed,
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

        raise RuntimeError(
            f"Model '{self.model}' did not return a '{tool_name}' function call — "
            f"finish_reason={response.choices[0].finish_reason}. "
            "The provider returned no matching structured output."
        )
