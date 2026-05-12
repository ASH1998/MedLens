"""Command line harness for deterministic and agentic MedLens reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from medlens.agent import MedicationSafetyAgent, build_provider
from medlens.agent_loop import run_agent_turn
from medlens.chat.app import run_terminal_chat
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyReport, MedicationSafetyStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("medications", nargs="*", help="Medication names. If omitted, reads one medication per stdin line.")
    parser.add_argument(
        "--normalization-db",
        type=Path,
        default=Path("data/artifacts/normalization.sqlite"),
        help="Path to normalization.sqlite.",
    )
    parser.add_argument(
        "--evidence-db",
        type=Path,
        default=Path("data/artifacts/evidence.sqlite"),
        help="Path to evidence.sqlite.",
    )
    parser.add_argument("--effect-limit", type=int, default=5, help="Maximum adverse effects per finding.")
    parser.add_argument("--format", choices=("json", "text", "agent", "agent-json"), default="json", help="Output format.")
    parser.add_argument(
        "--provider",
        choices=("auto", "template", "gemini", "google", "bedrock", "aws", "claude"),
        default="template",
        help="LLM provider for --format agent/agent-json.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Dotenv file for provider API keys.")
    parser.add_argument("--question", help="Optional user question for the agent.")
    parser.add_argument("--chat", action="store_true", help="Start an interactive terminal chat session.")
    parser.add_argument("--debug-trace", type=Path, help="Write agent tool traces to this JSONL file.")
    return parser.parse_args(argv)


def read_medications(args: argparse.Namespace) -> tuple[str, ...]:
    if args.medications:
        return tuple(item.strip() for item in args.medications if item.strip())
    return tuple(line.strip() for line in sys.stdin if line.strip())


def split_medications(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())


def report_to_text(report: MedicationSafetyReport) -> str:
    lines = [
        f"Overall severity: {report.overall_severity}",
        f"Evidence status: {report.evidence_status}",
        f"Checked pairs: {report.checked_pair_count}",
        "",
        "Medications:",
    ]
    for item in report.normalized_medications:
        if item.resolved:
            lines.append(f"- {item.input_name} -> {item.canonical_name}")
        else:
            lines.append(f"- {item.input_name} -> unresolved")

    lines.extend(["", "Findings:"])
    if report.findings:
        for finding in report.findings:
            lines.append(f"- {finding.drug_a} + {finding.drug_b}: {finding.severity} ({finding.row_count} rows)")
            if finding.source_regions:
                lines.append(f"  regions: {', '.join(finding.source_regions)}")
            if finding.effects:
                effects = ", ".join(effect.adverse_effect for effect in finding.effects[:5])
                lines.append(f"  top effects: {effects}")
    else:
        lines.append("- none found in local DDI reference evidence")

    lines.extend(["", "Limitations:"])
    lines.extend(f"- {limitation}" for limitation in report.limitations)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.chat:
        return run_chat(args)

    medications = read_medications(args)
    if not medications:
        print("No medications provided.", file=sys.stderr)
        return 2

    store = MedicationSafetyStore(args.normalization_db, args.evidence_db)
    report = store.build_structured_report(medications, effect_limit=args.effect_limit)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif args.format == "text":
        print(report_to_text(report))
    else:
        provider = build_provider(args.provider, env_path=args.env_file)
        session = ChatSession(
            provider_name=provider.name,
            provider_model=str(getattr(provider, "model", provider.name)),
            privacy_mode="on_device" if provider.name == "template" else "cloud",
        )
        question = args.question or " ".join(medications)
        result = run_agent_turn(
            provider=provider,
            session=session,
            store=store,
            user_message=question,
            candidate_medications=tuple(medications),
            effect_limit=args.effect_limit,
        )
        if args.debug_trace:
            write_debug_trace(args.debug_trace, result.to_debug_dict())
        if args.format == "agent-json":
            print(
                json.dumps(
                    {
                        "provider": result.provider_name,
                        "response": result.final_text,
                        "report": result.report.to_dict() if result.report is not None else None,
                        "used_tools": list(result.used_tools),
                        "fallback_used": result.fallback_used,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(result.final_text)
    return 0


def run_chat(args: argparse.Namespace) -> int:
    store = MedicationSafetyStore(args.normalization_db, args.evidence_db)
    provider = build_provider(args.provider, env_path=args.env_file)
    agent = MedicationSafetyAgent(store, provider)
    provider_model = str(getattr(provider, "model", provider.name))
    session = ChatSession(
        provider_name=provider.name,
        provider_model=provider_model,
        privacy_mode="on_device" if provider.name == "template" else "cloud",
    )
    return run_terminal_chat(
        store=store,
        agent=agent,
        session=session,
        initial_medications=tuple(args.medications),
        initial_question=args.question,
        effect_limit=args.effect_limit,
        debug_trace_path=args.debug_trace,
    )


def write_debug_trace(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str))
        handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
