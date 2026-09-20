"""Safe user-facing categories for provider failures; never echo provider payloads."""

from json import JSONDecodeError

from pydantic import ValidationError


def requirement_failure_detail(exc: Exception) -> str:
    cause = exc
    seen: set[int] = set()
    while cause.__cause__ is not None and id(cause) not in seen:
        seen.add(id(cause))
        cause = cause.__cause__
    status = getattr(cause, "status_code", None)
    if status in (401, 403):
        return "The LLM provider rejected authentication. Check the API key and provider access."
    if status == 429:
        return "The LLM provider's rate or quota limit was reached. Check quota and retry later."
    if status in (400, 404, 422):
        return (
            "The LLM provider rejected the request. Check that LLM_BASE_URL, LLM_MODEL, "
            "and LLM_API_KEY belong to the same provider and that the model supports tool calling."
        )
    if isinstance(cause, (ValidationError, JSONDecodeError)):
        return "The model returned invalid structured requirement data. Retry the analysis."
    if isinstance(cause, TimeoutError) or "Timeout" in type(cause).__name__:
        return "The LLM provider timed out. Check connectivity and retry the analysis."
    if "Connection" in type(cause).__name__ or isinstance(cause, OSError):
        return "Cannot connect to the LLM provider. Check LLM_BASE_URL and network connectivity."
    if isinstance(status, int) and status >= 500:
        return "The LLM provider is temporarily unavailable. Retry the analysis later."
    return "Requirement extraction failed. Check the LLM provider configuration and retry."
