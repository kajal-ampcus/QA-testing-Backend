"""Describe forms from a live accessibility snapshot and match saved answers.

Field descriptors never include values. Values stay in the encrypted answer
store or the login secret.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_FILLABLE_ROLES = {"textbox", "searchbox", "spinbutton", "combobox", "checkbox", "radio"}
_SUBMIT_NAME = re.compile(
    r"\b(submit|send|save|search|continue|next|apply|update|create|add|register|sign up|log in|login|sign in|confirm)\b",
    re.I,
)
_DESTRUCTIVE_NAME = re.compile(
    r"\b(log.?out|sign.?out|delete|remove|purchase|checkout|payment|transfer|withdraw)\b",
    re.I,
)
_PASSWORD_NAME = re.compile(r"\b(password|passphrase)\b", re.I)
_USERNAME_NAME = re.compile(
    r"\b(email|e-mail|username|user name|user id|userid|login|employee id|staff id)\b",
    re.I,
)
_CAPTCHA_NAME = re.compile(r"\bcaptcha\b", re.I)
_OTP_NAME = re.compile(
    r"\b(otp|one[- ]?time(?: password| code)?|passcode|mfa|2fa|authenticator|security code|verification code)\b",
    re.I,
)
_SESSION_SECRET_NAME = re.compile(
    r"\b(session token|csrf|access token|refresh token|auth token|bearer token)\b",
    re.I,
)
_INVALID_CREDENTIAL = re.compile(
    r"\b(?:invalid|incorrect).{0,40}(?:email|password|credential|employee id|username)\b",
    re.I,
)


class InvalidCredentialsError(RuntimeError):
    """The saved account was rejected. Do not submit it again in this run."""


class LoginInputRequired(Exception):
    """The login page has required fields that the saved account does not cover."""

    def __init__(
        self,
        fields: list[dict[str, Any]],
        *,
        otp: bool,
        submit: dict[str, str] | None = None,
    ) -> None:
        super().__init__("Login needs additional input")
        self.fields = fields
        self.otp = otp
        self.submit = submit


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def field_key(role: str, name: str) -> str:
    return f"{role}:{normalize_name(name)}"


def is_password_field(element: dict[str, Any]) -> bool:
    if str(element.get("input_type") or "").lower() == "password":
        return True
    return bool(_PASSWORD_NAME.search(str(element.get("name") or "")))


def is_captcha_field(name: str) -> bool:
    return bool(_CAPTCHA_NAME.search(name))


def is_otp_field(name: str) -> bool:
    return bool(_OTP_NAME.search(name))


def is_ephemeral_field(name: str) -> bool:
    """CAPTCHA, OTP, and session secrets are valid only for the current sign-in."""
    return bool(
        _CAPTCHA_NAME.search(name) or _OTP_NAME.search(name) or _SESSION_SECRET_NAME.search(name)
    )


def is_credential_rejection(text: str) -> bool:
    return bool(_INVALID_CREDENTIAL.search(text))


def should_retry_login(page_text: str, attempt: int, max_attempts: int = 3) -> bool:
    """Wrong credentials stop immediately. A rejected CAPTCHA may be read again."""
    if is_credential_rejection(page_text):
        return False
    return attempt + 1 < max_attempts


def is_destructive_control(name: str) -> bool:
    return bool(_DESTRUCTIVE_NAME.search(name))


def form_key_for(fields: list[dict[str, Any]]) -> str:
    raw = "|".join(sorted(str(field.get("key") or "") for field in fields))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def describe_field(element: dict[str, Any]) -> dict[str, Any]:
    role = str(element.get("role") or "")
    name = str(element.get("name") or "")
    options = element.get("options")
    input_type = str(element.get("input_type") or "")
    if not input_type and is_password_field(element):
        input_type = "password"
    return {
        "key": field_key(role, name),
        "role": role,
        "name": name,
        "input_type": input_type or "text",
        "required": bool(element.get("required")) or is_otp_field(name),
        "options": list(options) if isinstance(options, list) and options else None,
    }


def _fillable(element: dict[str, Any]) -> bool:
    if element.get("role") not in _FILLABLE_ROLES:
        return False
    if element.get("disabled") or element.get("readonly") or element.get("visible") is False:
        return False
    name = str(element.get("name") or "").strip()
    return bool(name) and not is_captcha_field(name)


def _needs_value(element: dict[str, Any]) -> bool:
    if not _fillable(element):
        return False
    options = element.get("options") or element.get("option_items")
    if isinstance(options, list) and options:
        return False
    name = str(element.get("name") or "")
    if is_captcha_field(name):
        return False
    return bool(element.get("required")) or is_otp_field(name)


def _safe_submit(elements: list[dict[str, Any]]) -> dict[str, str] | None:
    for element in elements:
        role = str(element.get("role") or "")
        name = str(element.get("name") or "").strip()
        if element.get("disabled") or element.get("visible") is False:
            continue
        if is_destructive_control(name) or is_captcha_field(name):
            continue
        typed_submit = str(element.get("input_type") or "").lower() == "submit"
        named_submit = role in {"button", "link"} and bool(_SUBMIT_NAME.search(name))
        if typed_submit or named_submit:
            return {"role": role or "button", "name": name or "Submit"}
    return None


def blocking_form(elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A form blocks exploration when required fields and a safe submit are both visible."""
    fields = [describe_field(element) for element in elements if _needs_value(element)]
    if not fields:
        return None
    submit = _safe_submit(elements)
    if submit is None:
        return None
    kind = (
        "login"
        if any(is_password_field(field) or is_otp_field(str(field.get("name") or "")) for field in fields)
        else "form"
    )
    return {"kind": kind, "fields": fields, "submit": submit}


def saved_field_values(secret: dict[str, Any]) -> dict[str, str]:
    """Map a normalized accessible name to a saved login value. Never log the result."""
    found: dict[str, str] = {}
    for item in secret.get("fields") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if name and isinstance(value, str) and value:
            found[normalize_name(name)] = value
    username = secret.get("username")
    password = secret.get("password")
    if isinstance(username, str) and username:
        for label in ("username", "email", "email address", "user name", "user id"):
            found.setdefault(label, username)
    if isinstance(password, str) and password:
        found.setdefault("password", password)
    return found


def value_for_field(field: dict[str, Any], saved: dict[str, str], secret: dict[str, Any] | None = None) -> str | None:
    name = normalize_name(str(field.get("name") or ""))
    if name in saved:
        return saved[name]
    for label, value in saved.items():
        if label and (label in name or name in label):
            return value
    if secret is None:
        return None
    if is_password_field(field) and isinstance(secret.get("password"), str):
        return secret["password"]
    if _USERNAME_NAME.search(name) and isinstance(secret.get("username"), str):
        return secret["username"]
    return None


def missing_fields(
    fields: list[dict[str, Any]], saved: dict[str, str], secret: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [field for field in fields if not value_for_field(field, saved, secret)]


def challenges_ready(
    fields: list[dict[str, Any]], durable: dict[str, str], session: dict[str, str]
) -> bool:
    ephemeral = [field for field in fields if is_ephemeral_field(str(field.get("name") or ""))]
    if ephemeral and not all(str(session.get(str(field.get("key") or "")) or "").strip() for field in ephemeral):
        return False
    reusable = [field for field in fields if field not in ephemeral]
    if not reusable:
        return bool(ephemeral)
    return answers_cover(reusable, durable)


def answers_cover(fields: list[dict[str, Any]], answers: dict[str, str]) -> bool:
    durable = [field for field in fields if not is_ephemeral_field(str(field.get("name") or ""))]
    if not durable:
        return False
    return all(str(answers.get(str(field.get("key") or "")) or "").strip() for field in durable)


def split_saved_values(
    values: dict[str, str], fields: list[dict[str, Any]] | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """Separate reusable answers from values that must not be kept for a later run."""
    names = {
        str(field.get("key") or ""): str(field.get("name") or "")
        for field in fields or []
        if isinstance(field, dict)
    }
    durable: dict[str, str] = {}
    session: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            continue
        label = names.get(key, key)
        if is_ephemeral_field(label) or is_ephemeral_field(key):
            session[key] = value.strip()
        else:
            durable[key] = value.strip()
    return durable, session


def mask_saved_value(key: str, value: str) -> str:
    if is_password_field({"name": key}) or is_ephemeral_field(key):
        return "••••"
    return value


def public_field(field: dict[str, Any]) -> dict[str, Any]:
    """Drop transient browser ids before a field is stored or shown."""
    described = describe_field(field) if "key" not in field else {
        "key": field.get("key"),
        "role": field.get("role"),
        "name": field.get("name"),
        "input_type": field.get("input_type") or "text",
        "required": bool(field.get("required")),
        "options": field.get("options") if isinstance(field.get("options"), list) else None,
    }
    return described


def login_field_names(secret: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in secret.get("fields") or []:
        if isinstance(item, dict) and item.get("name") and not is_password_field({"name": item["name"]}):
            names.append(str(item["name"]))
    return names
