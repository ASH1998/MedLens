"""State container for MedLens terminal chat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from medlens.tools.local_safety import MedicationSafetyReport, NormalizedMedication


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, object]
    result: dict[str, object] | list[object] | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ChatSession:
    provider_name: str
    provider_model: str = "template"
    privacy_mode: Literal["on_device", "cloud"] = "on_device"
    medications: list[NormalizedMedication] = field(default_factory=list)
    transcript: list[dict[str, str]] = field(default_factory=list)
    last_report: MedicationSafetyReport | None = None
    last_trace: list[ToolCallRecord] = field(default_factory=list)
    pending_unclear_medications: tuple[str, ...] = ()
    pending_clear_medications: tuple[str, ...] = ()

    def medication_inputs(self) -> tuple[str, ...]:
        return tuple(item.input_name for item in self.medications)

    def resolved_canonicals(self) -> tuple[str, ...]:
        seen: set[str] = set()
        values: list[str] = []
        for item in self.medications:
            if not item.resolved or item.canonical_name is None:
                continue
            if item.canonical_name in seen:
                continue
            seen.add(item.canonical_name)
            values.append(item.canonical_name)
        return tuple(values)

    def clear_turn_trace(self) -> None:
        self.last_trace = []

    def clear_pending_clarification(self) -> None:
        self.pending_unclear_medications = ()
        self.pending_clear_medications = ()
