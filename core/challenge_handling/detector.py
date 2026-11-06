"""Multi-signal classification from a sanitized DOM/accessibility snapshot."""

from __future__ import annotations

import re
from typing import Any

from .models import Challenge, ChallengeType

_PROVIDERS = {
    "recaptcha": ("recaptcha", "g-recaptcha", "google.com/recaptcha"),
    "hcaptcha": ("hcaptcha", "h-captcha.com"),
    "turnstile": ("turnstile", "challenges.cloudflare.com", "cf-turnstile"),
}
_TYPE_WORDS = (
    (ChallengeType.ALPHANUMERIC, r"(?:alpha[- ]?numeric|letters? and numbers?)\s*(?:captcha|challenge)|(?:captcha|challenge).{0,40}(?:alpha[- ]?numeric|letters? and numbers?)"),
    (ChallengeType.NUMERIC, r"(?:numeric|numbers?[- ]only|digits?[- ]only)\s*(?:captcha|challenge)|(?:captcha|challenge).{0,40}(?:numeric|digits?)"),
    (ChallengeType.ALPHABETIC, r"(?:alphabetic|letters?[- ]only)\s*(?:captcha|challenge)|(?:captcha|challenge).{0,40}(?:alphabetic|letters?)"),
    (ChallengeType.EMAIL_OTP, r"email.*(?:code|otp)|(?:code|otp).*email"),
    (ChallengeType.SMS_OTP, r"(?:sms|text message|mobile).*(?:code|otp)|(?:code|otp).*(?:sms|mobile)"),
    (ChallengeType.TOTP, r"authenticator|totp|time.?based"),
    (ChallengeType.PUSH_MFA, r"approve.*(?:notification|sign.?in)|push notification"),
    (ChallengeType.WEBAUTHN, r"webauthn|passkey|security key"),
    (ChallengeType.ARITHMETIC, r"(?:captcha|math question|human verification).{0,80}\d+\s*[+\-*/x×÷]\s*\d+"),
    (ChallengeType.TEXT_IMAGE, r"(?:image|distorted|visual)\s*(?:captcha|characters?|text)|(?:captcha|characters?).{0,40}(?:image|distorted|visual)"),
    (ChallengeType.AUDIO, r"audio\s*(?:captcha|challenge)|listen to (?:the )?(?:captcha|code)"),
    (ChallengeType.IMAGE_SELECTION, r"select (?:all )?(?:images|squares)|traffic lights|crosswalks"),
    (ChallengeType.CHECKBOX, r"i.?m not a robot|checkbox captcha"),
    (ChallengeType.SLIDER, r"drag (?:the )?slider|slide to"),
    (ChallengeType.PUZZLE, r"complete (?:the )?puzzle"),
)
_CAPTCHA = re.compile(r"captcha|verify (?:you are|that you are) human|security verification", re.I)
_OTP_INPUT = re.compile(r"otp|one.?time|verification code|security code|passcode|authenticator", re.I)


class ChallengeDetector:
    """Classify only what evidence supports; uncertainty remains ``UNKNOWN``.

    ``snapshot`` is deliberately a portable dict produced from DOM, a11y text,
    iframe metadata, and optional visual-analysis labels. It must never include
    passwords, tokens, or challenge answers.
    """

    def detect(self, snapshot: dict[str, Any]) -> Challenge | None:
        if not snapshot:
            return None
        text = self._text(snapshot)
        provider = self._provider(snapshot, text)
        signals: list[str] = []
        if provider:
            signals.append(f"provider:{provider}")
        fields = snapshot.get("fields") or []
        locator = self._challenge_input(fields)
        if locator:
            signals.append("challenge_input")
        if _CAPTCHA.search(text):
            signals.append("captcha_text")
        for kind, pattern in _TYPE_WORDS:
            if re.search(pattern, text, re.I):
                signals.append(f"text:{kind.value.lower()}")
                return self._challenge(kind, provider, signals, locator, snapshot)
        if provider:
            return self._challenge(ChallengeType.CHECKBOX, provider, signals, locator, snapshot)
        if locator or _CAPTCHA.search(text):
            kind = ChallengeType.CUSTOM_CAPTCHA if "custom" in text.casefold() else ChallengeType.UNKNOWN
            return self._challenge(kind, None, signals, locator, snapshot)
        return None

    @staticmethod
    def _text(snapshot: dict[str, Any]) -> str:
        bits = [str(snapshot.get(key) or "") for key in ("text", "accessible_text", "visual_labels")]
        bits.extend(str(frame.get("url") or "") for frame in snapshot.get("frames") or [] if isinstance(frame, dict))
        bits.extend(" ".join(str(field.get(key) or "") for key in ("name", "label", "placeholder", "type")) for field in snapshot.get("fields") or [] if isinstance(field, dict))
        return "\n".join(bits)

    @staticmethod
    def _provider(snapshot: dict[str, Any], text: str) -> str | None:
        providers = " ".join(str(x) for x in snapshot.get("provider_indicators") or []) + " " + text
        lowered = providers.casefold()
        return next((name for name, markers in _PROVIDERS.items() if any(marker in lowered for marker in markers)), None)

    @staticmethod
    def _challenge_input(fields: list[Any]) -> str | None:
        for field in fields:
            if not isinstance(field, dict):
                continue
            label = " ".join(str(field.get(key) or "") for key in ("name", "label", "placeholder", "type"))
            if _CAPTCHA.search(label) or _OTP_INPUT.search(label):
                return str(field.get("locator") or field.get("key") or "") or None
        return None

    @staticmethod
    def _challenge(kind: ChallengeType, provider: str | None, signals: list[str], locator: str | None, snapshot: dict[str, Any]) -> Challenge:
        # Independent evidence raises confidence; a single generic word stays cautious.
        confidence = min(0.98, 0.35 + 0.2 * len(set(signals)) + (0.15 if provider else 0))
        frame_url = next((str(item.get("url")) for item in snapshot.get("frames") or [] if isinstance(item, dict) and item.get("url")), None)
        return Challenge(kind, provider, round(confidence, 2), tuple(signals), locator, frame_url)
