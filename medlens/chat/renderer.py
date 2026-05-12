"""Terminal rendering helpers for MedLens chat."""

from __future__ import annotations

import json
from pathlib import Path

from medlens.chat.session import ChatSession, ToolCallRecord
from medlens.tools.local_safety import MedicationSafetyReport


class ChatRenderer:
    """Rich renderer with a plain-text fallback when optional deps are absent."""

    def __init__(self) -> None:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
            from rich.panel import Panel
            from rich.table import Table
        except ModuleNotFoundError:
            self.console = None
            self.Markdown = None
            self.Panel = None
            self.Table = None
        else:
            self.console = Console()
            self.Markdown = Markdown
            self.Panel = Panel
            self.Table = Table

    def welcome(self, session: ChatSession, store_paths: tuple[Path, Path]) -> None:
        if session.privacy_mode == "on_device":
            mode = "on-device (offline template)"
        else:
            mode = f"cloud ({session.provider_name} - {session.provider_model})"
        sizes = []
        for path in store_paths:
            if path.exists():
                sizes.append(f"{path.name}: {path.stat().st_size // 1024} KiB")
        artifacts = ", ".join(sizes) or "not found"
        text = (
            "Hi - I'm MedLens. Tell me what medications you're taking and I'll check them against my local "
            "DDI evidence. You can also ask about a specific pair, mechanisms, or what to flag for your "
            "prescriber.\n\n"
            f"Mode: {mode}\n"
            f"Local evidence: {artifacts}"
        )
        self._panel("MedLens terminal chat", text)

    def medications(self, session: ChatSession) -> None:
        if self.console and self.Table:
            table = self.Table(title="Medications")
            table.add_column("Input")
            table.add_column("Normalized")
            table.add_column("Status")
            for item in session.medications:
                table.add_row(item.input_name, item.canonical_name or "-", "resolved" if item.resolved else "unresolved")
            self.console.print(table)
            return
        print("Medications:")
        if not session.medications:
            print("- none")
        for item in session.medications:
            print(f"- {item.input_name} -> {item.canonical_name or 'unresolved'}")

    def report(self, report: MedicationSafetyReport) -> None:
        if self.console and self.Panel:
            self.console.print(self.Panel(_report_to_text(report), title="Structured report"))
        else:
            print(_report_to_text(report))

    def assistant(self, text: str, report: MedicationSafetyReport | None = None, show_grounding: bool = True) -> None:
        cue = self._grounding_cue(report) if show_grounding else ""
        body = f"{text}\n\n{cue}" if cue else text
        if self.console and self.Panel and self.Markdown:
            self.console.print(self.Panel(self.Markdown(body), title="Assistant"))
        else:
            print()
            print(body)
            print()

    def result(self, title: str, payload: object) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True, default=str)
        self._panel(title, str(text))

    def trace(self, records: list[ToolCallRecord] | list[object]) -> None:
        if not records:
            self.result("Trace", "No tool calls in the previous turn.")
            return
        preview: list[dict[str, object]] = []
        for record in records:
            if isinstance(record, ToolCallRecord):
                preview.append(
                    {
                        "name": record.name,
                        "args": record.args,
                        "duration_ms": record.duration_ms,
                        "error": record.error,
                        "result": _preview_payload(record.result),
                    }
                )
            else:
                preview.append({"record": str(record)})
        self.result("Trace", preview)

    def error(self, message: str) -> None:
        self._panel("Error", message)

    def _panel(self, title: str, text: str) -> None:
        if self.console and self.Panel:
            self.console.print(self.Panel(text, title=title))
        else:
            print(f"{title}\n{text}")

    def _grounding_cue(self, report: MedicationSafetyReport | None) -> str:
        if report is None:
            return ""
        unresolved = ", ".join(item.input_name for item in report.unresolved_medications)
        if report.evidence_status == "verified_reference_findings_with_unresolved_inputs" and unresolved:
            return f"_Couldn't match locally: {unresolved}._"
        if report.evidence_status == "no_reference_findings_with_unresolved_inputs" and unresolved:
            return f"_Couldn't match locally: {unresolved}._"
        return ""


def _report_to_text(report: MedicationSafetyReport) -> str:
    lines = [
        f"Overall severity: {report.overall_severity}",
        f"Evidence status: {report.evidence_status}",
        f"Checked pairs: {report.checked_pair_count}",
        "",
        "Medications:",
    ]
    for item in report.normalized_medications:
        lines.append(f"- {item.input_name} -> {item.canonical_name or 'unresolved'}")

    lines.extend(["", "Findings:"])
    if report.findings:
        for finding in report.findings:
            lines.append(f"- {finding.drug_a} + {finding.drug_b}: {finding.severity} ({finding.row_count} rows)")
            if finding.source_regions:
                lines.append(f"  regions: {', '.join(finding.source_regions)}")
            if finding.effects:
                effects = ", ".join(effect.adverse_effect for effect in finding.effects[:3])
                lines.append(f"  top effects: {effects}")
            source_line = _source_line(finding)
            if source_line:
                lines.append(f"  sources: {source_line}")
    else:
        lines.append("- none found in local DDI reference evidence")
        lines.append("- no pair-specific source is available because no local finding matched")
    return "\n".join(lines)


def _source_line(finding: object) -> str:
    bases = list(getattr(finding, "source_bases", ())[:2])
    urls = list(getattr(finding, "source_urls", ())[:2])
    parts: list[str] = []
    if bases:
        parts.append("basis: " + "; ".join(bases))
    if urls:
        parts.append("urls: " + " | ".join(urls))
    return "; ".join(parts)


def _preview_payload(value: object) -> object:
    if isinstance(value, dict):
        if "findings" in value and "overall_severity" in value:
            return {
                "overall_severity": value.get("overall_severity"),
                "evidence_status": value.get("evidence_status"),
                "checked_pair_count": value.get("checked_pair_count"),
                "findings_count": len(value.get("findings", [])) if isinstance(value.get("findings"), list) else 0,
            }
        return {key: _preview_payload(item) for key, item in list(value.items())[:8]}
    if isinstance(value, list):
        return [_preview_payload(item) for item in value[:5]]
    return value
