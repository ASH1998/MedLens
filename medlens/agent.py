"""Provider-agnostic MedLens agent over deterministic safety tools."""

from __future__ import annotations

import json
import os
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


AGENT_SYSTEM_PROMPT = """You are MedLens, a medication-safety assistant.

Rules:
- Use only the structured MedLens report as evidence.
- Do not invent interactions, adverse effects, mechanisms, or severity.
- If a medication is unresolved, say it was not checked locally.
- If no local finding exists, say no local DDI reference signal was found.
- Keep the response practical and calm.
- Tell the user this is a screening output, not patient-specific medical advice.
- Recommend contacting a clinician/pharmacist for medication changes, and urgent care for severe symptoms.
"""


class LlmProvider(Protocol):
    """Minimal provider interface for remote or local language models."""

    name: str

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return model text for the given prompt pair."""


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
            f"MedLens checked {report['checked_pair_count']} medication pairs.",
            f"Overall local evidence severity: {report['overall_severity']}.",
        ]
        if findings:
            lines.append("Flagged local DDI reference signals:")
            for finding in findings:
                effects = ", ".join(effect["adverse_effect"] for effect in finding.get("effects", [])[:3])
                suffix = f" Top effects: {effects}." if effects else ""
                lines.append(
                    f"- {finding['drug_a']} + {finding['drug_b']}: "
                    f"{finding['severity']} ({finding['row_count']} supporting rows).{suffix}"
                )
        else:
            lines.append("No known local DDI reference signal was found for the resolved medication pairs.")

        if unresolved:
            names = ", ".join(item["input_name"] for item in unresolved)
            lines.append(f"Not checked locally because they were unresolved: {names}.")

        lines.append("This is a local screening output, not patient-specific medical advice.")
        lines.append("Do not start, stop, or change medicines without a clinician or pharmacist.")
        return "\n".join(lines)


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
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
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
            "max_tokens": 900,
            "temperature": 0.2,
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
