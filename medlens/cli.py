"""Command line harness for deterministic and agentic MedLens reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from medlens.agent import MedicationSafetyAgent, build_provider
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
        agent = MedicationSafetyAgent(store, provider)
        result = agent.answer(medications, question=args.question, effect_limit=args.effect_limit)
        if args.format == "agent-json":
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(result.response)
    return 0


def run_chat(args: argparse.Namespace) -> int:
    store = MedicationSafetyStore(args.normalization_db, args.evidence_db)
    provider = build_provider(args.provider, env_path=args.env_file)
    agent = MedicationSafetyAgent(store, provider)
    medications = tuple(args.medications)

    print("MedLens terminal chat")
    print("Commands: /meds item1, item2 | /report | /quit")
    if not medications:
        medications = split_medications(input("Medications: "))
    if not medications:
        print("No medications provided.", file=sys.stderr)
        return 2

    def answer(question: str | None = None) -> None:
        result = agent.answer(medications, question=question, effect_limit=args.effect_limit)
        print()
        print(result.response)
        print()

    try:
        answer(args.question)
        while True:
            message = input("medlens> ").strip()
            if not message:
                continue
            command = message.lower()
            if command in {"/q", "/quit", "/exit"}:
                return 0
            if command.startswith("/meds"):
                updated = message[len("/meds") :].strip()
                if not updated:
                    updated = input("Medications: ")
                medications = split_medications(updated)
                if not medications:
                    print("No medications provided.")
                    continue
                answer("Re-check this updated medication list.")
                continue
            if command == "/report":
                report = store.build_structured_report(medications, effect_limit=args.effect_limit)
                print(report_to_text(report))
                continue
            answer(message)
    except KeyboardInterrupt:
        print()
        return 130
    except RuntimeError as exc:
        print(f"MedLens agent error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
