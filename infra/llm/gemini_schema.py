"""Inline local schema references for Gemini's function-calling endpoint."""

from typing import Any


def inline_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline types and remove schema titles that Gemini mistakes for tools."""

    def expand(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [expand(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/$defs/") or ref in stack:
                raise ValueError(
                    "Gemini tool schemas require non-recursive local $defs references."
                )
            target: Any = schema
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            resolved = expand(target, (*stack, ref))
            resolved.update(expand({k: v for k, v in value.items() if k != "$ref"}, stack))
            return resolved
        return {
            k: (
                {name: expand(child, stack) for name, child in v.items()}
                if k == "properties"
                else expand(v, stack)
            )
            for k, v in value.items()
            if k not in {"$defs", "title"}
        }

    return expand(schema)
