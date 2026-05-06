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
    "captopril",
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
- Use normalization.sqlite-backed tools for medicine names, aliases, OCR recovery, brand/common medicine profiles, strengths/forms, India common-use context, and common medicine search.
- Use evidence.sqlite-backed tools for DDI pairs, effects, severity, mechanisms, raw signals, evidence source coverage, and import issues.
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

        lines = [f"Overall local evidence severity: {report['overall_severity']}."]
        if findings:
            lines.append("Here is what I would pay attention to:")
            for finding in findings:
                effects = ", ".join(effect["adverse_effect"] for effect in finding.get("effects", [])[:3])
                suffix = f" The main things to watch for are {effects}." if effects else ""
                lines.append(
                    f"- {finding['drug_a']} + {finding['drug_b']} is a {finding['severity']} finding.{suffix}"
                )
        else:
            lines.append("I did not find a flagged interaction for these medicines in the local evidence.")

        if unresolved:
            names = ", ".join(item["input_name"] for item in unresolved)
            lines.append(f"I couldn't match these locally, so I did not check them: {names}.")

        if findings:
            lines.append("For a Major finding, it is worth asking a pharmacist or prescriber before combining these.")
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
        interaction_list_drug = _interaction_list_drug_candidate(last_user)
        if interaction_list_drug and "list_interactions_for_drug" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="template-drug-interactions-1",
                        name="list_interactions_for_drug",
                        args={"drug": interaction_list_drug, "limit": 12},
                    ),
                ),
            )
        interaction_list_result = _last_tool_result(tool_results, "list_interactions_for_drug")
        if interaction_list_result:
            return ToolModelResponse(text=_response_from_drug_interactions(interaction_list_result))

        if _evidence_sources_intent(last_user) and "list_evidence_sources" not in called_tools:
            return ToolModelResponse(text="", tool_calls=(ToolCall(id="template-evidence-sources-1", name="list_evidence_sources", args={}),))
        evidence_sources_result = _last_tool_result(tool_results, "list_evidence_sources")
        if evidence_sources_result:
            return ToolModelResponse(text=_response_from_evidence_sources(evidence_sources_result))

        import_issue_query = _import_issue_query(last_user)
        if import_issue_query is not None and "list_import_issues" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="template-import-issues-1", name="list_import_issues", args={"query": import_issue_query, "limit": 10}),),
            )
        import_issues_result = _last_tool_result(tool_results, "list_import_issues")
        if import_issues_result:
            return ToolModelResponse(text=_response_from_import_issues(import_issues_result))

        common_profile_names = _common_profile_candidates(last_user)
        if common_profile_names and not _called_common_profiles(tool_results, common_profile_names):
            return ToolModelResponse(
                text="",
                tool_calls=tuple(
                    ToolCall(id=f"template-common-profile-{index}", name="get_common_medicine_profile", args={"name": name, "limit": 3})
                    for index, name in enumerate(common_profile_names[:3], start=1)
                ),
            )
        common_profile_results = _all_tool_results(tool_results, "get_common_medicine_profile")
        if common_profile_results:
            return ToolModelResponse(text=_response_from_common_profiles(common_profile_results))

        common_search_query = _common_search_query(last_user)
        if common_search_query and "search_common_medicines" not in called_tools:
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="template-common-search-1", name="search_common_medicines", args={"query": common_search_query, "limit": 8}),),
            )
        common_search_result = _last_tool_result(tool_results, "search_common_medicines")
        if common_search_result:
            return ToolModelResponse(text=_response_from_common_search(common_search_result))

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
        r"\b(i|am|i'm|im|what|which|cant|can't|cannot|be|taking|taken|take|avoid|interact|interacts|interactions|can|could|should|is|are|ok|okay|safe|together|the|a|an|tablet|tablets|capsule|capsules|drug|drugs|medicine|medicines|meds?|my)\b",
        " ",
        first_line,
        flags=re.IGNORECASE,
    )
    pieces = re.split(r"[,;/]|\s+\band\b\s+|\s+\bwith\b\s+|\s+\balong\s+with\b\s+", text, flags=re.IGNORECASE)
    candidates: list[str] = []
    for piece in pieces:
        piece = re.split(r"[.?!]", piece, maxsplit=1)[0]
        value = " ".join(re.findall(r"[A-Za-z0-9]+", piece)).strip()
        known = _known_terms_in_text(value)
        if known:
            candidates.extend(known)
            continue
        if len(value) >= 3 and value.casefold() not in {"what should watch for", "watch for"}:
            candidates.append(value)
    if len(candidates) == 1:
        known = _known_terms_in_text(candidates[0])
        if len(known) >= 2:
            return known
    return candidates


def _interaction_list_drug_candidate(user_text: str) -> str:
    first_line = user_text.splitlines()[0] if user_text.splitlines() else user_text
    normalized = first_line.casefold()
    intent_terms = (
        "what medicines",
        "which medicines",
        "what drugs",
        "which drugs",
        "can't be taken",
        "cant be taken",
        "cannot be taken",
        "should i avoid",
        "interact with",
        "interacts with",
    )
    if not any(term in normalized for term in intent_terms):
        return ""
    for pattern in (
        r"\b(?:with|against|for)\s+([A-Za-z][A-Za-z0-9 -]{1,60})",
        r"\b(?:avoid|taking|taken)\s+([A-Za-z][A-Za-z0-9 -]{1,60})",
    ):
        match = re.search(pattern, first_line, flags=re.IGNORECASE)
        if match:
            candidate = re.split(r"[.?!,;]", match.group(1), maxsplit=1)[0]
            candidate = " ".join(re.findall(r"[A-Za-z0-9]+", candidate)).strip()
            if len(candidate) >= 3:
                return candidate
    known = _known_terms_in_text(first_line)
    return known[0] if known else ""


def _evidence_sources_intent(user_text: str) -> bool:
    normalized = user_text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "evidence sources",
            "data sources",
            "source files",
            "which datasets",
            "what datasets",
            "import counts",
            "rows imported",
        )
    )


def _import_issue_query(user_text: str) -> str | None:
    normalized = user_text.casefold()
    if not any(phrase in normalized for phrase in ("import issue", "import issues", "unresolved import", "unresolved rows", "failed to import", "normalization gaps")):
        return None
    for pattern in (r"\b(?:for|about|with)\s+([A-Za-z][A-Za-z0-9 -]{1,60})",):
        match = re.search(pattern, user_text, flags=re.IGNORECASE)
        if match:
            candidate = re.split(r"[.?!,;]", match.group(1), maxsplit=1)[0]
            return " ".join(re.findall(r"[A-Za-z0-9]+", candidate)).strip()
    return ""


def _common_profile_candidates(user_text: str) -> list[str]:
    first_line = user_text.splitlines()[0] if user_text.splitlines() else user_text
    normalized = first_line.casefold()
    profile_terms = (
        "what is",
        "what are",
        "what's",
        "whats",
        "profile",
        "composition",
        "strength",
        "dosage",
        "brand",
        "otc",
        "rx",
        "used for",
        "use of",
    )
    if not any(term in normalized for term in profile_terms):
        return []
    candidates = _candidate_medications(user_text) or _extract_medication_candidates(user_text)
    if candidates:
        return list(dict.fromkeys(candidates))
    for pattern in (
        r"\b(?:what is|what are|what's|whats|profile for|about|composition of|strength of|use of)\s+([A-Za-z][A-Za-z0-9 +/.-]{1,80})",
        r"\b(?:is|are)\s+([A-Za-z][A-Za-z0-9 +/.-]{1,80})\s+(?:otc|rx|prescription|used)",
    ):
        match = re.search(pattern, first_line, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = re.split(r"[.?!;]", match.group(1), maxsplit=1)[0]
        candidate = re.sub(r"\b(used for|useful for|medicine|tablet|capsule|brand|generic)\b.*$", " ", candidate, flags=re.IGNORECASE)
        value = " ".join(re.findall(r"[A-Za-z0-9]+", candidate)).strip()
        if len(value) >= 3 and value.casefold() not in {"these", "this medicine", "this"}:
            return [value]
    return []


def _common_search_query(user_text: str) -> str:
    first_line = user_text.splitlines()[0] if user_text.splitlines() else user_text
    normalized = first_line.casefold()
    if not any(phrase in normalized for phrase in ("common medicines for", "medicines for", "medicine for", "drugs for", "search common")):
        return ""
    match = re.search(r"\b(?:for|search common)\s+([A-Za-z][A-Za-z0-9 -]{1,80})", first_line, flags=re.IGNORECASE)
    if not match:
        return ""
    candidate = re.split(r"[.?!,;]", match.group(1), maxsplit=1)[0]
    return " ".join(re.findall(r"[A-Za-z0-9]+", candidate)).strip()


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


def _all_tool_results(tool_results: list[dict[str, object]], name: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for message in tool_results:
        if message.get("name") != name:
            continue
        content = message.get("content")
        if isinstance(content, dict):
            results.append(content)
    return results


def _called_common_profiles(tool_results: list[dict[str, object]], names: list[str]) -> bool:
    return sum(1 for message in tool_results if message.get("name") == "get_common_medicine_profile") >= min(len(names), 3)


def _response_from_report(report: dict[str, object]) -> str:
    findings = report.get("findings", [])
    unresolved = report.get("unresolved_medications", [])
    checked_pair_count = report.get("checked_pair_count", 0)
    overall = report.get("overall_severity", "None")
    lines = [f"I checked {_pair_count_text(checked_pair_count)}. In my local reference set, this is marked {overall}."]
    if isinstance(findings, list) and findings:
        first_finding = next((item for item in findings if isinstance(item, dict)), None)
        if first_finding is not None:
            lines.extend(_finding_explanation_lines(first_finding))
            source_lines = _source_lines_from_finding(first_finding)
            if source_lines:
                lines.append("")
                lines.append("Sources:")
                lines.extend(source_lines)
            else:
                lines.append("")
                lines.append("Sources:")
                lines.append(f"- {first_finding.get('drug_a')} + {first_finding.get('drug_b')}: no URL on file")
        if len(findings) > 3:
            lines.append("")
            lines.append(f"There are {len(findings) - 3} more findings available. Ask for details and I can walk through them.")
        elif len(findings) > 1:
            lines.append("")
            lines.append(f"There are {len(findings) - 1} more findings available. Ask for details and I can walk through them.")
    else:
        lines.append("I did not find a flagged interaction for these medicines in the local evidence. That does not prove the combination is safe; it only means this local reference set did not flag it.")
        lines.append("If you have symptoms or a condition that changes risk, a pharmacist can help interpret it.")

    if isinstance(unresolved, list) and unresolved:
        names = ", ".join(str(item.get("input_name")) for item in unresolved if isinstance(item, dict))
        lines.append(f"I could not match this locally, so I did not check it: {names}.")

    return "\n".join(lines)


def _response_from_drug_interactions(result: dict[str, object]) -> str:
    drug = result.get("drug")
    canonical = ""
    resolved = False
    if isinstance(drug, dict):
        canonical = str(drug.get("canonical_name") or drug.get("input_name") or drug.get("input") or "")
        resolved = bool(drug.get("resolved"))
    if not resolved:
        name = canonical or "that medicine"
        return f"I could not match {name} in the local medicine index, so I cannot list interaction partners for it yet."

    interactions = result.get("interactions", [])
    if not isinstance(interactions, list) or not interactions:
        return (
            f"I did not find locally flagged interaction partners for {canonical}. "
            "That does not prove every combination is safe; it only means this local reference set did not flag any."
        )

    shown = [item for item in interactions[:8] if isinstance(item, dict)]
    lines = [
        f"I am showing {len(interactions)} locally flagged medicine(s) for {canonical}.",
        "This is not a universal do-not-take list; it is a list of combinations worth checking with a pharmacist or prescriber.",
        "",
        "Highest-priority matches:",
    ]
    for item in shown:
        partner = str(item.get("partner") or "unknown medicine")
        severity = str(item.get("severity") or "flagged")
        effects = _effect_names_from_interaction_item(item, 2)
        effect_text = f" Main concern: {', '.join(effects)}." if effects else ""
        lines.append(f"- {partner}: {severity}.{effect_text}")
    if len(interactions) > len(shown):
        lines.append(f"- {len(interactions) - len(shown)} more locally flagged match(es) available.")

    source_lines = _source_lines_from_interaction_items(shown[:3])
    if source_lines:
        lines.append("")
        lines.append("Sources:")
        lines.extend(source_lines)
    return "\n".join(lines)


def _response_from_common_profiles(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for result in results:
        normalized = result.get("normalized", {})
        canonical = ""
        if isinstance(normalized, dict):
            canonical = str(normalized.get("canonical_name") or normalized.get("input_name") or normalized.get("input") or "")
        matches = result.get("matches", [])
        if not isinstance(matches, list) or not matches:
            query = str(result.get("query") or canonical or "that medicine")
            lines.append(f"I could not find an India common-medicine profile for {query} in the local normalization database.")
            continue
        first = next((item for item in matches if isinstance(item, dict)), None)
        if first is None:
            continue
        name = canonical or str(first.get("canonical_name") or first.get("generic_or_common_name") or "this medicine")
        common = str(first.get("generic_or_common_name") or name)
        lines.append(f"{common} maps locally to {name}.")
        use = str(first.get("common_daily_life_use_india") or "")
        if use:
            lines.append(f"Common India use context: {use}.")
        form = str(first.get("dosage_form") or "")
        strength = str(first.get("composition_or_strength_pattern") or "")
        if form or strength:
            lines.append(f"Forms/strengths in the local catalogue: {', '.join(part for part in (form, strength) if part)}.")
        brands = str(first.get("common_brand_examples_india") or "")
        if brands:
            lines.append(f"Brand examples in the dataset: {brands}.")
        rx = str(first.get("otc_or_rx") or "")
        if rx:
            lines.append(f"Availability context: {rx}.")
        flags = str(first.get("patient_risk_flags_india") or "")
        if flags:
            lines.append(f"Risk flags to notice: {flags}.")
        urls = str(first.get("source_urls") or "")
        if urls:
            lines.append("Sources: " + urls)
        if len(results) > 1:
            lines.append("")
    return "\n".join(lines).strip()


def _response_from_common_search(result: dict[str, object]) -> str:
    query = str(result.get("query") or "that search")
    matches = result.get("matches", [])
    if not isinstance(matches, list) or not matches:
        return f"I did not find common India medicine catalogue matches for {query}."
    lines = [f"I found {len(matches)} common India medicine catalogue match(es) for {query}:"]
    for item in matches[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("generic_or_common_name") or item.get("canonical_name") or "medicine")
        category = str(item.get("therapeutic_category") or "")
        brands = str(item.get("common_brand_examples_india") or "")
        use = str(item.get("common_daily_life_use_india") or "")
        detail = "; ".join(part for part in (category, use, f"brands: {brands}" if brands else "") if part)
        lines.append(f"- {name}: {detail}")
    return "\n".join(lines)


def _response_from_evidence_sources(result: dict[str, object]) -> str:
    sources = result.get("sources", [])
    if not isinstance(sources, list) or not sources:
        return "I did not find source-file import records in the local evidence database."
    lines = ["The local evidence database currently has these source files loaded:"]
    for item in sources:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- {source_file}: {rows_imported}/{rows_seen} rows imported, {rows_unresolved} unresolved, {unique_pairs_imported} unique pairs ({region}).".format(
                source_file=item.get("source_file"),
                rows_imported=item.get("rows_imported"),
                rows_seen=item.get("rows_seen"),
                rows_unresolved=item.get("rows_unresolved"),
                unique_pairs_imported=item.get("unique_pairs_imported"),
                region=item.get("region"),
            )
        )
    return "\n".join(lines)


def _response_from_import_issues(result: dict[str, object]) -> str:
    issues = result.get("issues", [])
    if not isinstance(issues, list) or not issues:
        return "I did not find unresolved import issues for that query in the local evidence database."
    lines = [f"I found {len(issues)} unresolved import issue(s) in the local evidence database:"]
    for item in issues[:10]:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- {source_file} row {row_number}: {drug1} + {drug2} ({reason}).".format(
                source_file=item.get("source_file"),
                row_number=item.get("row_number"),
                drug1=item.get("drug1"),
                drug2=item.get("drug2"),
                reason=item.get("reason"),
            )
        )
    return "\n".join(lines)


def _effect_names_from_interaction_item(item: dict[str, object], limit: int) -> list[str]:
    effects = item.get("top_effects", [])
    if not isinstance(effects, list):
        return []
    names: list[str] = []
    for effect in effects[:limit]:
        if isinstance(effect, dict) and effect.get("adverse_effect"):
            names.append(str(effect["adverse_effect"]))
    return names


def _source_lines_from_interaction_items(items: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        drug_a = str(item.get("drug_a") or "medicine A")
        drug_b = str(item.get("drug_b") or "medicine B")
        urls = item.get("source_urls", [])
        if isinstance(urls, list) and urls:
            lines.append(f"- {drug_a} + {drug_b}: {urls[0]}")
        else:
            lines.append(f"- {drug_a} + {drug_b}: no URL on file")
    return lines


def _finding_explanation_lines(finding: dict[str, object]) -> list[str]:
    drug_a = str(finding.get("drug_a") or "medicine A")
    drug_b = str(finding.get("drug_b") or "medicine B")
    severity = str(finding.get("severity") or "flagged")
    effects = _effect_names_from_finding(finding, 3)

    lines = [f"I found a {severity} interaction between {drug_a} and {drug_b}."]
    if effects:
        lines.append(f"The main concern is {', '.join(effects)}.")
        plain_note = _plain_effect_note(effects[0])
        if plain_note:
            lines.append(plain_note)
    if severity == "Major":
        lines.append(
            "Because this is marked Major, it is worth asking a pharmacist or prescriber before using them together."
        )
    elif effects:
        lines.append("If you are using them together, keep an eye on those symptoms and ask a pharmacist if they show up.")
    return lines


def _effect_names_from_finding(finding: dict[str, object], limit: int) -> list[str]:
    effects = finding.get("effects", [])
    if not isinstance(effects, list):
        return []
    names: list[str] = []
    for effect in effects[:limit]:
        if isinstance(effect, dict) and effect.get("adverse_effect"):
            names.append(str(effect["adverse_effect"]))
    return names


def _plain_effect_note(effect_name: str) -> str:
    normalized = effect_name.casefold()
    if "gastrointestinal bleeding" in normalized:
        return "In plain language, gastrointestinal bleeding means bleeding in the stomach or intestines."
    if "intracranial hemorrhage" in normalized:
        return "In plain language, intracranial hemorrhage means bleeding inside the skull."
    if "qt prolongation" in normalized:
        return "In plain language, QT prolongation is an electrical heart-rhythm change that can become dangerous in some people."
    if "torsades" in normalized:
        return "In plain language, torsades de pointes is a dangerous abnormal heart rhythm."
    if "acute anemia" in normalized:
        return "In plain language, acute anemia means a sudden drop in red blood cells or hemoglobin."
    return ""


def _source_lines_from_finding(finding: dict[str, object]) -> list[str]:
    drug_a = str(finding.get("drug_a") or "medicine A")
    drug_b = str(finding.get("drug_b") or "medicine B")
    regions = _short_list(finding.get("source_regions"), 4)
    bases = _compact_basis_items(finding.get("source_bases"), 3)
    urls = _short_list(finding.get("source_urls"), 20)
    if not urls:
        return []
    meta_parts: list[str] = []
    if regions:
        meta_parts.append("regions: " + ", ".join(regions))
    if bases:
        meta_parts.append("basis: " + "; ".join(bases))
    meta = f" ({'; '.join(meta_parts)})" if meta_parts else ""
    lines = [f"- {drug_a} + {drug_b}: {url}{meta}" for url in urls[:3]]
    if len(urls) > 3:
        lines.append(f"- {drug_a} + {drug_b}: {len(urls) - 3} more source URL(s) on file; use /sources for the full list.")
    return lines


def _short_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if str(item)]


def _compact_basis_items(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        for piece in str(raw).split(";"):
            item = piece.strip()
            if item and item not in items:
                items.append(item)
            if len(items) >= limit:
                return items
    return items


def _pair_count_text(value: object) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 0
    noun = "medicine pair" if count == 1 else "medicine pairs"
    return f"{count} {noun}"


def _educational_fallback_text() -> str:
    return (
        "I can help with medication interaction questions, but I am not the right tool for diagnosing symptoms or choosing a treatment. "
        "A pharmacist or clinician can help connect symptoms with your own health history and medicines. If there is chest pain, trouble "
        "breathing, heavy bleeding, sudden weakness, or severe allergic symptoms, seek urgent care."
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
