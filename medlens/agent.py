"""Provider-agnostic MedLens agent over deterministic safety tools."""

from __future__ import annotations

import json
import os
import re
import datetime as _dt
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from medlens.tools.local_safety import MedicationSafetyReport, MedicationSafetyStore


KNOWN_TEMPLATE_MED_TERMS = (
    "acetaminophen",
    "paracetamol",
    "ondansetron",
    "fluorouracil",
    "azithromycin",
    "ibuprofen",
    "advil",
    "warfarin",
    "dolo 650",
)


AGENT_SYSTEM_PROMPT = """You are MedLens, an expert clinical pharmacist talking with a patient.

Tone:
- Speak like a knowledgeable pharmacist sitting across the counter, not a legal disclaimer.
- Be warm, direct, and substantive. Lead with the answer, then explain.
- Plain language, but don't dumb it down. If a mechanism is interesting, share it.
- Mix short paragraphs with a bullet list only when bullets actually help.
- Avoid stiff phrases like "screening output", "patient-specific medical advice", "contact your clinician before any change". One natural closing line is enough; skip the closing line entirely when there's no finding to act on.
- Never stack two or three disclaimers in a row. The patient knows this is software.

What to actually say:
- For each flagged pair: name the interaction, the severity, the top effects, and (if the tool returned them) the mechanism, regional source, and source URL. Bring these in as a clinician would, not as a checklist.
- For Major findings, it is appropriate to suggest the patient bring this up with their prescriber or pharmacist - say it once, in normal sentence form.
- For Moderate or Minor findings, describe what to be aware of. Closing nudges are usually unnecessary.
- For unresolved medication names: say plainly that you couldn't match it in your local database and didn't check it.
- For pairs with no local signal: say there's no flagged interaction in your local evidence. Do NOT call the combination "safe".

Hard evidence rules (these are non-negotiable):
- Use the MedLens tool output as your only source. Do not invent interactions, effects, mechanisms, severities, or sources.
- Severity, top effects, regions, source basis, and source URLs must come straight from the tool result for that pair.
- Do not reference a pair, severity, effect, or source that did not appear in a tool result this turn.
- Citations are required. For every matched finding you discuss, end the answer with a short "Sources" section listing every source_urls entry the tool returned for that pair (one URL per line, plus regions and source_bases when present). Do not omit URLs that are in the tool result. If the tool returned no URL, say "no URL on file" instead of staying silent.
"""


class LlmProvider(Protocol):
    """Minimal provider interface for remote or local language models."""

    name: str

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return model text for the given prompt pair."""


@dataclass(frozen=True)
class ToolCall:
    """Provider-neutral native tool request."""

    id: str
    name: str
    args: dict[str, object]


@dataclass(frozen=True)
class ToolModelResponse:
    """Provider-neutral assistant response that may request tool calls."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class AgentResult:
    """Final agent output plus the report it was grounded in."""

    report: MedicationSafetyReport
    response: str
    provider_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider_name,
            "response": self.response,
            "report": self.report.to_dict(),
        }


class MedicationSafetyAgent:
    """Build a deterministic report, then ask an LLM to explain it."""

    def __init__(self, store: MedicationSafetyStore, provider: LlmProvider) -> None:
        self.store = store
        self.provider = provider

    def answer(
        self,
        medication_names: list[str] | tuple[str, ...],
        question: str | None = None,
        effect_limit: int = 5,
    ) -> AgentResult:
        report = self.store.build_structured_report(medication_names, effect_limit=effect_limit)
        prompt = build_agent_prompt(report, question=question)
        response = self.provider.generate(AGENT_SYSTEM_PROMPT, prompt).strip()
        return AgentResult(report=report, response=response, provider_name=self.provider.name)


class TemplateProvider:
    """Offline deterministic provider used for tests and local fallback."""

    name = "template"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        del system_prompt
        payload = _extract_report_payload(user_prompt)
        report = payload["report"]
        findings = report["findings"]
        unresolved = report["unresolved_medications"]

        lines = [
            f"Checked {report['checked_pair_count']} pair(s) against the local DDI evidence.",
            f"Overall local evidence severity: {report['overall_severity']}.",
        ]
        if findings:
            lines.append("Here is what stood out:")
            for finding in findings:
                effects = ", ".join(effect["adverse_effect"] for effect in finding.get("effects", [])[:3])
                suffix = f" Watch for: {effects}." if effects else ""
                lines.append(
                    f"- {finding['drug_a']} + {finding['drug_b']} ({finding['severity']}, "
                    f"{finding['row_count']} supporting rows).{suffix}"
                )
        else:
            lines.append("No flagged interaction in the local DDI evidence for these pairs.")

        if unresolved:
            names = ", ".join(item["input_name"] for item in unresolved)
            lines.append(f"I couldn't match these locally, so I didn't check them: {names}.")

        lines.append("This is a local screening output - bring anything Major up with your prescriber or pharmacist before changing meds.")
        return "\n".join(lines)

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ToolModelResponse:
        """Deterministic native-tool provider for offline tests and demos."""
        del system_prompt, tools
        last_user = _last_user_text(messages)
        tool_results = _tool_results(messages)
        called_tools = {str(item.get("name", "")) for item in tool_results}

        pending_unresolved = _pending_unresolved_from_transcript(messages)
        candidates = _candidate_medications(last_user) or _extract_medication_candidates(last_user)
        followup_candidates = _extract_followup_medication_candidate(last_user, pending_unresolved)
        if followup_candidates:
            candidates = list(dict.fromkeys([*_recognized_from_transcript(messages), *followup_candidates]))
        session_meds = _current_session_medications(last_user)

        if pending_unresolved and not candidates and not tool_results:
            return ToolModelResponse(
                text=(
                    f"I still need the exact medicine name for {', '.join(pending_unresolved)}. "
                    "Please type the brand/generic name exactly as written, including strength if present."
                )
            )

        if candidates and "normalize_medications" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="template-normalize-1", name="normalize_medications", args={"names": candidates}),),
            )

        normalized = _last_tool_result(tool_results, "normalize_medications")
        unresolved = _unresolved_names(normalized)
        resolved = _resolved_inputs(normalized)
        if unresolved and not _searched_all_unresolved(tool_results, unresolved):
            return ToolModelResponse(
                text="",
                tool_calls=tuple(
                    ToolCall(id=f"template-search-{index}", name="search_drug_aliases", args={"query": name, "limit": 5})
                    for index, name in enumerate(unresolved[:4], start=1)
                ),
            )
        if unresolved:
            return ToolModelResponse(text=_clarification_from_unresolved(unresolved, resolved, tool_results))

        if resolved and "add_medications" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="template-add-1", name="add_medications", args={"names": resolved}),),
            )

        if (resolved or session_meds) and "build_structured_report" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="template-report-1", name="build_structured_report", args={}),),
            )

        report = _last_tool_result(tool_results, "build_structured_report")
        if not report:
            return ToolModelResponse(text=_educational_fallback_text())
        return ToolModelResponse(text=_response_from_report(report))


class GeminiProvider:
    """Google Gemini provider using the public generateContent HTTP API."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500},
        }
        data = _post_json(url, payload, headers={"Content-Type": "application/json"})
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts).strip()
        if not text:
            raise RuntimeError(f"Gemini returned an empty response: {data}")
        return text

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ToolModelResponse:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": _gemini_contents(messages),
            "tools": tools,
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500},
        }
        data = _post_json(url, payload, headers={"Content-Type": "application/json"})
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if "text" in part:
                text_parts.append(str(part.get("text", "")))
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                tool_calls.append(
                    ToolCall(
                        id=f"gemini-{index}",
                        name=str(function_call.get("name", "")),
                        args=dict(function_call.get("args") or {}),
                    )
                )
        return ToolModelResponse(text="".join(text_parts).strip(), tool_calls=tuple(tool_calls))


class BedrockProvider:
    """AWS Bedrock Runtime provider for Claude-compatible models."""

    name = "bedrock"

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        model: str,
        session_token: str | None = None,
    ) -> None:
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region
        self.model = model
        self.session_token = session_token

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "temperature": 0.4,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        data = _post_bedrock_json(
            region=self.region,
            model=self.model,
            payload=payload,
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key,
            session_token=self.session_token,
        )
        blocks = data.get("content") or []
        text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text").strip()
        if not text:
            raise RuntimeError(f"Bedrock returned an empty response: {data}")
        return text

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ToolModelResponse:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "temperature": 0.4,
            "system": system_prompt,
            "messages": _bedrock_messages(messages),
            "tools": tools,
            "tool_choice": {"type": "auto"},
        }
        data = _post_bedrock_json(
            region=self.region,
            model=self.model,
            payload=payload,
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key,
            session_token=self.session_token,
        )
        blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            if block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        args=dict(block.get("input") or {}),
                    )
                )
        return ToolModelResponse(text="".join(text_parts).strip(), tool_calls=tuple(tool_calls))


def build_provider(provider_name: str = "auto", env_path: Path | str = Path(".env")) -> LlmProvider:
    """Create a provider from environment variables.

    `auto` prefers Gemini, then Bedrock, then the offline template provider.
    """
    env = load_env(env_path)
    provider_name = provider_name.lower()

    if provider_name == "auto":
        if env.get("GOOGLE_API_KEY"):
            provider_name = "gemini"
        elif env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY") and env.get("CLAUDE_MODEL"):
            provider_name = "bedrock"
        else:
            provider_name = "template"

    if provider_name in {"template", "offline", "local"}:
        return TemplateProvider()
    if provider_name in {"gemini", "google"}:
        api_key = _required_env(env, "GOOGLE_API_KEY")
        model = env.get("GOOGLE_MODEL") or env.get("GOOGLE_MODEL_OLD") or "gemini-1.5-flash"
        return GeminiProvider(api_key=api_key, model=model)
    if provider_name in {"bedrock", "aws", "claude"}:
        return BedrockProvider(
            access_key_id=_required_env(env, "AWS_ACCESS_KEY_ID"),
            secret_access_key=_required_env(env, "AWS_SECRET_ACCESS_KEY"),
            region=env.get("AWS_REGION") or "us-east-1",
            model=_required_env(env, "CLAUDE_MODEL"),
            session_token=env.get("AWS_SESSION_TOKEN"),
        )

    raise ValueError(f"Unknown provider: {provider_name}")


def build_agent_prompt(report: MedicationSafetyReport, question: str | None = None) -> str:
    payload = {
        "user_question": question or "Explain the local medication safety report.",
        "report": _compact_report(report),
        "response_contract": {
            "must_include": [
                "overall local evidence severity",
                "flagged pairs if any",
                "unresolved medications if any",
                "local screening limitation",
                "clinician/pharmacist guidance",
            ],
            "must_not_include": [
                "new interactions not in report.findings",
                "new adverse effects not in report.findings.effects",
                "claims that unresolved medications were checked",
                "instructions to stop or change medication without a clinician",
            ],
        },
    }
    return "Explain this MedLens report using only the JSON below:\n\n" + json.dumps(payload, indent=2, sort_keys=True)


def load_env(path: Path | str = Path(".env")) -> dict[str, str]:
    """Load process env plus simple KEY=VALUE pairs from a dotenv file."""
    env = dict(os.environ)
    env_path = Path(path)
    if not env_path.exists():
        return env
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        env.setdefault(key.strip(), value)
    return env


def _compact_report(report: MedicationSafetyReport) -> dict[str, object]:
    data = report.to_dict()
    for finding in data["findings"]:
        if isinstance(finding, dict):
            finding["raw_signals"] = finding.get("raw_signals", [])[:3]
    return data


def _required_env(env: dict[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise RuntimeError(f"Missing {key}.")
    return value


def _post_json(url: str, payload: dict[str, object], headers: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM provider HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM provider request failed: {exc}") from exc


def _post_bedrock_json(
    region: str,
    model: str,
    payload: dict[str, object],
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None = None,
) -> dict[str, object]:
    service = "bedrock"
    host = f"bedrock-runtime.{region}.amazonaws.com"
    request_uri, canonical_uri = _bedrock_model_invoke_uris(model)
    url = f"https://{host}{request_uri}"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    now = _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    body_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Host": host,
        "X-Amz-Date": amz_date,
    }
    if session_token:
        headers["X-Amz-Security-Token"] = session_token

    signed_headers = ";".join(key.lower() for key in sorted(headers))
    canonical_headers = "".join(f"{key.lower()}:{headers[key].strip()}\n" for key in sorted(headers))
    canonical_request = "\n".join(
        [
            "POST",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            body_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _aws_signing_key(secret_access_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bedrock HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Bedrock request failed: {exc}") from exc


def _aws_signing_key(secret_access_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret_access_key).encode("utf-8")
    date_key = hmac.new(key, date_stamp.encode("utf-8"), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _bedrock_model_invoke_uris(model: str) -> tuple[str, str]:
    """Return request and canonical URI paths for Bedrock InvokeModel.

    `urllib` re-quotes percent escapes in request paths. If the model id contains
    a colon, signing only `/model/foo%3A0/invoke` produces a signature mismatch
    because Bedrock expects the canonical path as `/model/foo%253A0/invoke`.
    """
    request_model = urllib.parse.quote(model, safe="")
    canonical_model = urllib.parse.quote(request_model, safe="")
    return f"/model/{request_model}/invoke", f"/model/{canonical_model}/invoke"


def _extract_report_payload(user_prompt: str) -> dict[str, object]:
    marker = "Explain this MedLens report using only the JSON below:"
    _, _, payload = user_prompt.partition(marker)
    if not payload.strip():
        raise ValueError("TemplateProvider expected an agent prompt payload.")
    return json.loads(payload)


def _last_user_text(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _candidate_medications(user_text: str) -> list[str]:
    marker = "Candidate medications detected by CLI args:"
    for line in user_text.splitlines():
        if line.startswith(marker):
            _, _, value = line.partition(":")
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _current_session_medications(user_text: str) -> list[str]:
    marker = "Current session medications:"
    for line in user_text.splitlines():
        if line.startswith(marker):
            _, _, value = line.partition(":")
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _extract_medication_candidates(user_text: str) -> list[str]:
    """Small offline stand-in for LLM extraction in TemplateProvider."""
    first_line = user_text.splitlines()[0] if user_text.splitlines() else user_text
    normalized = first_line.casefold()
    if "," not in first_line and not any(token in normalized for token in ("take", "taking", "med", "medicine", "along with", "with")):
        return []
    text = re.sub(
        r"\b(i|am|i'm|im|taking|take|the|a|an|tablet|tablets|capsule|capsules|medicine|medicines|meds?|my)\b",
        " ",
        first_line,
        flags=re.IGNORECASE,
    )
    pieces = re.split(r"[,;/]|\s+\band\b\s+|\s+\bwith\b\s+|\s+\balong\s+with\b\s+", text, flags=re.IGNORECASE)
    candidates: list[str] = []
    for piece in pieces:
        value = " ".join(re.findall(r"[A-Za-z0-9]+", piece)).strip()
        if len(value) >= 3 and value.casefold() not in {"what should watch for", "watch for"}:
            candidates.append(value)
    if len(candidates) == 1:
        known = _known_terms_in_text(candidates[0])
        if len(known) >= 2:
            return known
    return candidates


def _known_terms_in_text(value: str) -> list[str]:
    normalized = value.casefold()
    found: list[str] = []
    for term in KNOWN_TEMPLATE_MED_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", normalized):
            found.append(term)
    return found


def _extract_followup_medication_candidate(user_text: str, pending_unresolved: list[str]) -> list[str]:
    if not pending_unresolved:
        return []
    first_line = user_text.splitlines()[0] if user_text.splitlines() else user_text
    text = re.sub(r"\b(it'?s|it is|brand name|generic name|medicine|medication|called|name is|the)\b", " ", first_line, flags=re.IGNORECASE)
    value = " ".join(re.findall(r"[A-Za-z0-9]+", text)).strip()
    if len(value) < 3 or value.casefold() in {"brand", "brand name", "generic"}:
        return []
    return [value]


def _pending_unresolved_from_transcript(messages: list[dict[str, object]]) -> list[str]:
    pattern = re.compile(r"could not confidently match ([^.]+)\.", re.IGNORECASE)
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content", ""))
        match = pattern.search(content)
        if match:
            return [item.strip() for item in match.group(1).split(",") if item.strip()]
    return []


def _recognized_from_transcript(messages: list[dict[str, object]]) -> list[str]:
    pattern = re.compile(r"I recognized: ([^.]+)\.", re.IGNORECASE)
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content", ""))
        match = pattern.search(content)
        if match:
            return [item.strip() for item in match.group(1).split(",") if item.strip()]
    return []


def _unresolved_names(normalized_result: dict[str, object] | None) -> list[str]:
    if not normalized_result:
        return []
    medications = normalized_result.get("medications", [])
    if not isinstance(medications, list):
        return []
    return [
        str(item.get("input_name") or item.get("input"))
        for item in medications
        if isinstance(item, dict) and not item.get("resolved")
    ]


def _resolved_inputs(normalized_result: dict[str, object] | None) -> list[str]:
    if not normalized_result:
        return []
    medications = normalized_result.get("medications", [])
    if not isinstance(medications, list):
        return []
    return [
        str(item.get("input_name") or item.get("input"))
        for item in medications
        if isinstance(item, dict) and item.get("resolved")
    ]


def _searched_all_unresolved(tool_results: list[dict[str, object]], unresolved: list[str]) -> bool:
    searched = {
        str(message.get("content", {}).get("query", ""))
        for message in tool_results
        if message.get("name") == "search_drug_aliases" and isinstance(message.get("content"), dict)
    }
    if not searched:
        # Older tool result shape does not include args in content, so count
        # search calls as sufficient when all unresolved names were requested
        # in the same round.
        return sum(1 for message in tool_results if message.get("name") == "search_drug_aliases") >= len(unresolved)
    return all(name in searched for name in unresolved)


def _clarification_from_unresolved(
    unresolved: list[str],
    resolved: list[str],
    tool_results: list[dict[str, object]],
) -> str:
    suggestions: list[str] = []
    for message in tool_results:
        if message.get("name") != "search_drug_aliases":
            continue
        content = message.get("content")
        if not isinstance(content, dict):
            continue
        matches = content.get("matches", [])
        if isinstance(matches, list):
            for match in matches[:2]:
                if isinstance(match, dict) and match.get("canonical"):
                    suggestions.append(str(match["canonical"]))
    unresolved_text = ", ".join(unresolved)
    resolved_text = f" I recognized: {', '.join(resolved)}." if resolved else ""
    if suggestions:
        return (
            f"I could not confidently match {unresolved_text}.{resolved_text} "
            f"Possible local matches include: {', '.join(dict.fromkeys(suggestions[:3]))}. "
            "Please confirm the exact brand/generic name and strength before I check interactions."
        )
    return (
        f"I could not confidently match {unresolved_text}.{resolved_text} "
        "Please confirm the exact brand/generic name and strength before I check interactions."
    )


def _tool_results(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [message for message in messages if message.get("role") == "tool"]


def _last_tool_result(tool_results: list[dict[str, object]], name: str) -> dict[str, object] | None:
    for message in reversed(tool_results):
        if message.get("name") != name:
            continue
        content = message.get("content")
        if isinstance(content, dict):
            return content
    return None


def _response_from_report(report: dict[str, object]) -> str:
    findings = report.get("findings", [])
    unresolved = report.get("unresolved_medications", [])
    checked_pair_count = report.get("checked_pair_count", 0)
    overall = report.get("overall_severity", "None")
    lines = [
        f"Checked {checked_pair_count} pair(s) in the local DDI evidence. Overall severity: {overall}.",
    ]
    if isinstance(findings, list) and findings:
        for finding in findings[:3]:
            if not isinstance(finding, dict):
                continue
            effects = finding.get("effects", [])
            effect_names: list[str] = []
            if isinstance(effects, list):
                effect_names = [str(effect.get("adverse_effect")) for effect in effects[:2] if isinstance(effect, dict)]
            suffix = f" - watch for {', '.join(effect_names)}" if effect_names else ""
            source_line = _source_line_from_finding(finding)
            source_suffix = f" Source: {source_line}." if source_line else ""
            lines.append(
                f"- {finding.get('drug_a')} + {finding.get('drug_b')} "
                f"({finding.get('severity')}, {finding.get('row_count')} rows{_regions_suffix(finding)}){suffix}.{source_suffix}"
            )
        if len(findings) > 3:
            lines.append(f"- {len(findings) - 3} more findings available. Ask for details to see them all.")
        else:
            lines.append("Ask for details if you want mechanisms, raw signal rows, or what to discuss with your prescriber.")
    else:
        lines.append("No flagged interaction in the local DDI evidence for these pairs.")
        lines.append("Ask for details if you want me to walk through what I checked or look up something specific.")

    if isinstance(unresolved, list) and unresolved:
        names = ", ".join(str(item.get("input_name")) for item in unresolved if isinstance(item, dict))
        lines.append(f"Couldn't match locally, so I didn't check: {names}.")

    return "\n".join(lines)


def _source_line_from_finding(finding: dict[str, object]) -> str:
    regions = _short_list(finding.get("source_regions"), 4)
    bases = _short_list(finding.get("source_bases"), 3)
    urls = _short_list(finding.get("source_urls"), 4)
    parts: list[str] = []
    if regions:
        parts.append("regions: " + ", ".join(regions))
    if bases:
        parts.append("basis: " + "; ".join(bases))
    if urls:
        parts.append("urls: " + " | ".join(urls))
    return "; ".join(parts)


def _regions_suffix(finding: dict[str, object]) -> str:
    regions = _short_list(finding.get("source_regions"), 4)
    if not regions:
        return ""
    return f", regions: {', '.join(regions)}"


def _short_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if str(item)]


def _educational_fallback_text() -> str:
    return (
        "I focus on medication interactions, so I'm not the right tool for diagnosing symptoms or recommending treatment. "
        "A pharmacist or clinician can help with that. For red-flag symptoms - chest pain, trouble breathing, "
        "heavy bleeding, or sudden weakness - go straight to urgent care."
    )


def _bedrock_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id", "")),
                            "content": json.dumps(message.get("content", {}), separators=(",", ":"), default=str),
                        }
                    ],
                }
            )
        elif role == "assistant" and isinstance(message.get("tool_calls"), list):
            blocks: list[dict[str, object]] = []
            text = str(message.get("content", "")).strip()
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls", []):
                if isinstance(call, dict):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id", "")),
                            "name": str(call.get("name", "")),
                            "input": dict(call.get("args") or {}),
                        }
                    )
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": role if role in {"user", "assistant"} else "user", "content": str(message.get("content", ""))})
    return converted


def _gemini_contents(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    contents: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        if role == "tool":
            contents.append(
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": str(message.get("name", "")),
                                "response": message.get("content", {}),
                            }
                        }
                    ],
                }
            )
        elif role == "assistant" and isinstance(message.get("tool_calls"), list):
            parts: list[dict[str, object]] = []
            text = str(message.get("content", "")).strip()
            if text:
                parts.append({"text": text})
            for call in message.get("tool_calls", []):
                if isinstance(call, dict):
                    parts.append({"functionCall": {"name": str(call.get("name", "")), "args": dict(call.get("args") or {})}})
            contents.append({"role": "model", "parts": parts})
        else:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": str(message.get("content", ""))}]})
    return contents
