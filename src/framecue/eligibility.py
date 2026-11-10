"""Privacy eligibility preflight, run BEFORE any screenplay content is sent.

Why this exists: OpenRouter's ``provider.zdr=true`` routes to zero-data-retention endpoints
*plus* "Safety Retention" endpoints when the account allows them. Self-serve accounts allow
them by default, and a request-level parameter cannot override the account setting. Safety
Retention endpoints may keep flagged prompts for abuse review. OpenRouter's docs say they "don't
meet a strict Zero Data Retention requirement". So ``zdr=true`` on its own does not guarantee
strict ZDR.

This module reads OpenRouter's public ZDR endpoint list (``GET /api/v1/endpoints/zdr``, no key
needed, nothing of ours is sent). For the configured model it then decides:

- whether at least one endpoint supports strict JSON-schema output AND is strict ZDR
  (``safety_retention`` false);
- which providers serve only Safety Retention endpoints. These go into ``provider.ignore``,
  and any response that names one of them aborts the run;
- otherwise, that the run is ineligible, with an exact reason. Safety Retention endpoints are
  only accepted when ``allow_safety_retention`` is explicitly set.

The result never relaxes ``require_parameters``, ``data_collection`` or ``zdr``.
"""

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from framecue.llm_client import LLMSettings

REQUIRED_PARAMETERS = frozenset({"response_format", "structured_outputs"})


@dataclass(frozen=True)
class EndpointInfo:
    tag: str
    provider_name: str
    safety_retention: bool
    structured_outputs: bool
    status: int | None
    prompt_price: float | None
    completion_price: float | None

    @property
    def provider_slug(self) -> str:
        return self.tag.split("/", 1)[0]


@dataclass(frozen=True)
class Eligibility:
    """``eligible`` is True only when at least one endpoint qualifies under the configured
    policy. ``reason`` is a stable code; ``detail`` is a human sentence with no secrets."""

    model: str
    eligible: bool
    reason: str
    detail: str
    usable: tuple[EndpointInfo, ...] = ()
    excluded: tuple[EndpointInfo, ...] = ()
    ignore_providers: tuple[str, ...] = ()
    forbidden_provider_names: frozenset[str] = field(default_factory=frozenset)
    allow_safety_retention: bool = False

    def summary(self) -> str:
        usable = ", ".join(e.tag for e in self.usable) or "none"
        excluded = ", ".join(e.tag for e in self.excluded) or "none"
        return (
            f"eligibility[{self.model}]: {'ELIGIBLE' if self.eligible else 'BLOCKED'} "
            f"({self.reason}) usable=[{usable}] excluded=[{excluded}] "
            f"ignore={list(self.ignore_providers)}"
        )


def zdr_list_url(chat_endpoint: str) -> str:
    """``https://openrouter.ai/api/v1/chat/completions`` → ``.../api/v1/endpoints/zdr``."""
    parts = urlsplit(chat_endpoint)
    path = parts.path
    suffix = "/chat/completions"
    base = path[: -len(suffix)] if path.endswith(suffix) else path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"{base}/endpoints/zdr", "", ""))


def fetch_zdr_endpoints(
    settings: LLMSettings, transport: httpx.BaseTransport | None = None
) -> list[dict]:
    """Fetch the public ZDR list. No Authorization header is sent: the list is public and the
    key is not needed. Raises ``httpx.HTTPError`` or ``ValueError`` on failure."""
    with httpx.Client(timeout=settings.timeout_s, transport=transport) as http:
        response = http.get(zdr_list_url(settings.endpoint))
        response.raise_for_status()
        data = response.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("ZDR endpoint list has an unexpected shape")
    return [row for row in rows if isinstance(row, dict)]


def assess(rows: list[dict], settings: LLMSettings) -> Eligibility:
    """Pure decision over ZDR-list rows for ``settings.model``."""
    model = settings.model
    allow_sr = settings.allow_safety_retention
    endpoints = [_endpoint(r) for r in rows if r.get("model_id") == model]
    if not endpoints:
        return Eligibility(
            model,
            False,
            "no_zdr_endpoint",
            "the ZDR endpoint list has no endpoint for this model",
            allow_safety_retention=allow_sr,
        )
    structured = [e for e in endpoints if e.structured_outputs]
    if not structured:
        return Eligibility(
            model,
            False,
            "no_structured_output_zdr_endpoint",
            "no ZDR endpoint for this model supports response_format + structured_outputs",
            excluded=tuple(endpoints),
            allow_safety_retention=allow_sr,
        )
    strict = [e for e in structured if not e.safety_retention]
    retaining = [e for e in structured if e.safety_retention]
    if allow_sr:
        return Eligibility(
            model,
            True,
            "eligible_with_safety_retention" if retaining else "eligible_strict_zdr",
            "Safety Retention endpoints explicitly allowed by configuration"
            if retaining
            else "strict ZDR endpoints available",
            usable=tuple(structured),
            allow_safety_retention=True,
        )
    if not strict:
        return Eligibility(
            model,
            False,
            "only_safety_retention_endpoints",
            "every ZDR-listed endpoint for this model is a Safety Retention endpoint (prompts "
            "flagged by provider classifiers may be retained); choose another model or set "
            "FRAMECUE_LLM_ALLOW_SAFETY_RETENTION=true deliberately",
            excluded=tuple(retaining),
        )
    strict_slugs = {e.provider_slug for e in strict}
    retaining_slugs = {e.provider_slug for e in retaining}
    shared = strict_slugs & retaining_slugs
    if shared:
        # Cannot exclude the retaining endpoints without also excluding strict ones.
        return Eligibility(
            model,
            False,
            "cannot_exclude_safety_retention",
            f"providers {sorted(shared)} serve both strict and Safety Retention endpoints",
            usable=tuple(strict),
            excluded=tuple(retaining),
        )
    strict_names = {e.provider_name for e in strict}
    return Eligibility(
        model,
        True,
        "eligible_strict_zdr",
        "strict ZDR endpoints available"
        + ("; Safety Retention providers are ignored" if retaining_slugs else ""),
        usable=tuple(strict),
        excluded=tuple(retaining),
        ignore_providers=tuple(sorted(retaining_slugs)),
        forbidden_provider_names=frozenset(
            e.provider_name for e in retaining if e.provider_name not in strict_names
        ),
    )


def check_eligibility(
    settings: LLMSettings, transport: httpx.BaseTransport | None = None
) -> Eligibility:
    """Fetch and assess. A fetch failure is reported as ineligible, never as eligible.

    GroqCloud has no per-request routing or public endpoint-retention list: retention is an
    organisation setting (Data Controls → Zero Data Retention). That cannot be verified via the
    API, so the result says the owner's account setting is relied on, and nothing is fetched.
    """
    if settings.provider == "groq":
        return Eligibility(
            settings.model,
            True,
            "groq_account_zdr",
            "GroqCloud: privacy relies on the organisation's Zero Data Retention setting "
            "(reported enabled by the owner; not verifiable via the API)",
        )
    if not settings.zdr:
        return Eligibility(
            settings.model, False, "zdr_disabled", "FRAMECUE_LLM_ZDR is false; refusing"
        )
    try:
        rows = fetch_zdr_endpoints(settings, transport)
    except (httpx.HTTPError, ValueError) as exc:
        return Eligibility(
            settings.model,
            False,
            "eligibility_check_failed",
            f"could not read the ZDR endpoint list ({type(exc).__name__})",
        )
    return assess(rows, settings)


def _endpoint(row: dict) -> EndpointInfo:
    params = set(row.get("supported_parameters") or [])
    pricing = row.get("pricing") or {}
    return EndpointInfo(
        tag=str(row.get("tag") or row.get("provider_name") or "unknown"),
        provider_name=str(row.get("provider_name") or "unknown"),
        # Missing flag is treated as retaining: unknown is not strict.
        safety_retention=row.get("safety_retention") is not False,
        structured_outputs=params >= REQUIRED_PARAMETERS,
        status=row.get("status") if isinstance(row.get("status"), int) else None,
        prompt_price=_price(pricing.get("prompt")),
        completion_price=_price(pricing.get("completion")),
    )


def _price(value: object) -> float | None:
    try:
        return float(value) * 1_000_000 if value is not None else None  # USD per Mtok
    except (TypeError, ValueError):
        return None
