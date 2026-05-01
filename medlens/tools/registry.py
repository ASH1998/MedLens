"""Provider-neutral deterministic tool registry for MedLens agents."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from medlens.artifacts.build_normalization import normalize_lookup_text
from medlens.chat.session import ChatSession, ToolCallRecord
from medlens.tools.local_safety import (
    InteractionEffect,
    KnownInteraction,
    MedicationSafetyStore,
    NormalizedMedication,
    RawDdiSignal,
)


TOOL_SCHEMAS: list[dict[str, object]] = [
    {
        "name": "add_medications",
        "description": "Add medication names to the current chat session.",
        "input_schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]},
    },
    {
        "name": "remove_medications",
        "description": "Remove medication names from the current chat session.",
        "input_schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]},
    },
    {"name": "clear_medications", "description": "Clear the current medication list.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_medications", "description": "List current medications.", "input_schema": {"type": "object", "properties": {}}},
    {
        "name": "normalize_medications",
        "description": "Normalize medication names through the local alias index.",
        "input_schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]},
    },
    {
        "name": "lookup_pair",
        "description": "Look up a known local DDI reference pair.",
        "input_schema": {
            "type": "object",
            "properties": {"drug_a": {"type": "string"}, "drug_b": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["drug_a", "drug_b"],
        },
    },
    {
        "name": "get_pair_effects",
        "description": "Get adverse effects for a local DDI reference pair.",
        "input_schema": {
            "type": "object",
            "properties": {"drug_a": {"type": "string"}, "drug_b": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["drug_a", "drug_b"],
        },
    },
    {
        "name": "get_raw_signals",
        "description": "Get raw supporting DDI signal rows for a pair.",
        "input_schema": {
            "type": "object",
            "properties": {"drug_a": {"type": "string"}, "drug_b": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["drug_a", "drug_b"],
        },
    },
    {
        "name": "build_structured_report",
        "description": "Build a deterministic safety report for supplied or session medications.",
        "input_schema": {"type": "object", "properties": {"medication_names": {"type": "array", "items": {"type": "string"}}}},
    },
    {
        "name": "search_drug_aliases",
        "description": "Search local medication aliases.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
    },
    {
        "name": "severity_consensus",
        "description": "Return per-region severity and rolled-up severity for a pair.",
        "input_schema": {"type": "object", "properties": {"drug_a": {"type": "string"}, "drug_b": {"type": "string"}}, "required": ["drug_a", "drug_b"]},
    },
    {
        "name": "find_pairs_by_effect",
        "description": "Find current-session pairs with effects matching a query.",
        "input_schema": {"type": "object", "properties": {"effect": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["effect"]},
    },
    {
        "name": "evidence_about",
        "description": "Explain local evidence sources, severity scale, or limitations.",
        "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
    },
    {"name": "current_session_summary", "description": "Return provider and session summary.", "input_schema": {"type": "object", "properties": {}}},
]


def to_bedrock_tools() -> list[dict[str, object]]:
    return [
        {"name": schema["name"], "description": schema["description"], "input_schema": schema["input_schema"]}
        for schema in TOOL_SCHEMAS
    ]


def to_gemini_tools() -> list[dict[str, object]]:
    declarations = [
        {"name": schema["name"], "description": schema["description"], "parameters": schema["input_schema"]}
        for schema in TOOL_SCHEMAS
    ]
    return [{"functionDeclarations": declarations}]


def dispatch(name: str, args: Mapping[str, Any] | None, *, store: MedicationSafetyStore, session: ChatSession) -> dict[str, object]:
    """Run a deterministic tool and record a JSON-safe trace item."""
    args_dict = dict(args or {})
    started = time.perf_counter()
    record = ToolCallRecord(name=name, args=dict(args_dict))
    try:
        result = _dispatch(name, args_dict, store=store, session=session)
        record.result = result
        return result
    except Exception as exc:  # noqa: BLE001 - the model needs structured failures.
        result = {"error": str(exc), "code": exc.__class__.__name__}
        record.result = result
        record.error = str(exc)
        return result
    finally:
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        session.last_trace.append(record)


def _dispatch(name: str, args: dict[str, Any], *, store: MedicationSafetyStore, session: ChatSession) -> dict[str, object]:
    if name == "add_medications":
        return _add_medications(_string_list(args.get("names")), store=store, session=session)
    if name == "remove_medications":
        return _remove_medications(_string_list(args.get("names")), store=store, session=session)
    if name == "clear_medications":
        session.medications.clear()
        session.last_report = None
        return {"cleared": True}
    if name == "list_medications":
        return {"medications": [_normalized_to_dict(item) for item in session.medications]}
    if name == "normalize_medications":
        return {"medications": [_normalized_to_dict(item) for item in store.normalize_medication_names(_string_list(args.get("names")))]}
    if name == "lookup_pair":
        interaction = store.lookup_known_interaction(str(args["drug_a"]), str(args["drug_b"]), effect_limit=_limit(args, 8))
        return _interaction_summary(interaction)
    if name == "get_pair_effects":
        interaction = store.lookup_known_interaction(str(args["drug_a"]), str(args["drug_b"]), effect_limit=_limit(args, 20))
        return {"effects": [_effect_to_dict(effect) for effect in interaction.effects]}
    if name == "get_raw_signals":
        interaction = store.lookup_known_interaction(
            str(args["drug_a"]),
            str(args["drug_b"]),
            effect_limit=3,
            raw_signal_limit=_limit(args, 20),
        )
        return {"raw_signals": [_raw_to_dict(raw) for raw in interaction.raw_signals]}
    if name == "build_structured_report":
        names = _string_list(args.get("medication_names")) if args.get("medication_names") is not None else list(session.medication_inputs())
        report = store.build_structured_report(tuple(names), effect_limit=_limit(args, 8))
        session.last_report = report
        return report.to_dict()
    if name == "search_drug_aliases":
        query = str(args["query"])
        return {"query": query, "matches": store.search_drug_aliases(query, limit=_limit(args, 10))}
    if name == "severity_consensus":
        return _severity_consensus(str(args["drug_a"]), str(args["drug_b"]), store=store)
    if name == "find_pairs_by_effect":
        return _find_pairs_by_effect(str(args["effect"]), store=store, session=session, limit=_limit(args, 10))
    if name == "evidence_about":
        return _evidence_about(str(args["topic"]))
    if name == "current_session_summary":
        return {
            "provider": session.provider_name,
            "model": session.provider_model,
            "meds_count": len(session.medications),
            "last_report_id": id(session.last_report) if session.last_report is not None else None,
            "privacy_note": _privacy_note(session),
        }
    return {"error": f"Unknown tool: {name}", "code": "unknown_tool"}


def _add_medications(names: list[str], *, store: MedicationSafetyStore, session: ChatSession) -> dict[str, object]:
    normalized = store.normalize_medication_names(tuple(names))
    existing_inputs = {item.input_name.casefold() for item in session.medications}
    existing_canonicals = {item.canonical_name for item in session.medications if item.canonical_name}
    added: list[dict[str, object]] = []
    already_present: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []

    for item in normalized:
        if item.input_name.casefold() in existing_inputs or (item.canonical_name and item.canonical_name in existing_canonicals):
            already_present.append(_normalized_to_dict(item))
            continue
        session.medications.append(item)
        existing_inputs.add(item.input_name.casefold())
        if item.canonical_name:
            existing_canonicals.add(item.canonical_name)
        target = added if item.resolved else unresolved
        target.append(_normalized_to_dict(item))
    session.last_report = None
    return {"added": added, "already_present": already_present, "unresolved": unresolved}


def _remove_medications(names: list[str], *, store: MedicationSafetyStore, session: ChatSession) -> dict[str, object]:
    normalized = store.normalize_medication_names(tuple(names))
    remove_inputs = {item.input_name.casefold() for item in normalized}
    remove_canonicals = {item.canonical_name for item in normalized if item.canonical_name}
    kept: list[NormalizedMedication] = []
    removed: list[dict[str, object]] = []
    removed_inputs: set[str] = set()
    removed_canonicals: set[str] = set()

    for item in session.medications:
        if item.input_name.casefold() in remove_inputs or (item.canonical_name and item.canonical_name in remove_canonicals):
            removed.append(_normalized_to_dict(item))
            removed_inputs.add(item.input_name.casefold())
            if item.canonical_name:
                removed_canonicals.add(item.canonical_name)
        else:
            kept.append(item)
    session.medications = kept
    session.last_report = None
    not_found = [
        item.input_name
        for item in normalized
        if item.input_name.casefold() not in removed_inputs and (item.canonical_name is None or item.canonical_name not in removed_canonicals)
    ]
    return {"removed": removed, "not_found": not_found}


def _severity_consensus(drug_a: str, drug_b: str, *, store: MedicationSafetyStore) -> dict[str, object]:
    interaction = store.lookup_known_interaction(drug_a, drug_b, raw_signal_limit=1000)
    if not interaction.found:
        return {"found": False, "drug_a": interaction.drug_a, "drug_b": interaction.drug_b}

    by_region_rank: dict[str, tuple[str, int]] = {}
    for raw in interaction.raw_signals:
        rank = _severity_rank(raw.severity)
        current = by_region_rank.get(raw.region)
        if current is None or rank > current[1]:
            by_region_rank[raw.region] = (raw.severity, rank)
    by_region = {region: severity for region, (severity, _rank) in sorted(by_region_rank.items())}
    unique = set(by_region.values())
    return {
        "found": True,
        "drug_a": interaction.drug_a,
        "drug_b": interaction.drug_b,
        "single_region": len(by_region) <= 1,
        "by_region": by_region,
        "rolled_up": interaction.severity,
        "disagreement": len(unique) > 1,
    }


def _find_pairs_by_effect(effect: str, *, store: MedicationSafetyStore, session: ChatSession, limit: int) -> dict[str, object]:
    query = normalize_lookup_text(effect)
    if not query:
        return {"matches": []}
    report = store.build_structured_report(session.medication_inputs(), effect_limit=100)
    matches: list[dict[str, object]] = []
    for finding in report.findings:
        matched_phrases = [
            item.adverse_effect
            for item in finding.effects
            if query in normalize_lookup_text(item.adverse_effect) or normalize_lookup_text(item.adverse_effect) in query
        ]
        if matched_phrases:
            matches.append(
                {
                    "drug_a": finding.drug_a,
                    "drug_b": finding.drug_b,
                    "severity": finding.severity,
                    "regions": list(finding.source_regions),
                    "matched_phrases": matched_phrases[:5],
                }
            )
        if len(matches) >= limit:
            break
    return {"matches": matches}


def _evidence_about(topic: str) -> dict[str, object]:
    topic = topic.casefold().strip()
    content = {
        "sources": "MedLens uses local SQLite artifacts built from curated regional DDI-ADE CSV files for this MVP.",
        "severity_scale": "Severity rolls up to the highest local signal: Major, Moderate, Minor, or None.",
        "limitations": "This is screening/reference evidence only, not patient-specific medical advice or a diagnosis.",
    }
    return {"topic": topic, "text": content.get(topic, content["limitations"])}


def _interaction_summary(interaction: KnownInteraction) -> dict[str, object]:
    return {
        "found": interaction.found,
        "drug_a": interaction.drug_a,
        "drug_b": interaction.drug_b,
        "severity": interaction.severity,
        "row_count": interaction.row_count,
        "regions": list(interaction.source_regions),
        "top_effects": [_effect_to_dict(effect) for effect in interaction.effects],
    }


def _normalized_to_dict(item: NormalizedMedication) -> dict[str, object]:
    return {
        "input": item.input_name,
        "input_name": item.input_name,
        "normalized": item.canonical_name,
        "canonical_name": item.canonical_name,
        "status": "resolved" if item.resolved else "unresolved",
        "resolved": item.resolved,
        "matched_alias": item.matched_alias,
    }


def _effect_to_dict(effect: InteractionEffect) -> dict[str, object]:
    return {
        "adverse_effect": effect.adverse_effect,
        "severity": effect.severity,
        "count": effect.row_count,
        "row_count": effect.row_count,
        "regions": list(effect.source_regions),
    }


def _raw_to_dict(raw: RawDdiSignal) -> dict[str, object]:
    return {
        "region": raw.region,
        "severity": raw.severity,
        "source_basis": raw.source_basis,
        "source_url": raw.source_urls,
        "mechanism": raw.mechanism_or_rationale,
        "caveats": raw.use_case_note,
    }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _limit(args: Mapping[str, Any], default: int) -> int:
    try:
        return max(1, min(1000, int(args.get("limit", default))))
    except (TypeError, ValueError):
        return default


def _severity_rank(severity: str | None) -> int:
    return {"Major": 3, "Moderate": 2, "Minor": 1, "high": 3, "medium": 2, "moderate": 2, "low": 1}.get(severity or "", 0)


def _privacy_note(session: ChatSession) -> str:
    if session.privacy_mode == "cloud":
        return f"meds and questions leave device -> {session.provider_name}"
    return "100% offline -> template"


def json_safe(value: object) -> object:
    return json.loads(json.dumps(value, default=str))
