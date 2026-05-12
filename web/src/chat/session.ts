// Chat session state — TS port of `medlens/chat/session.py`. Holds the in-memory
// medication list, transcript, last deterministic report, and tool-call trace
// for the current turn. Persistence (sidebar / multi-conversation) is handled
// separately in Phase 6 via Zustand + idb-keyval.

import type { MedicationSafetyReport, NormalizedMedication } from "../tools/types";

export interface ToolCallRecord {
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  error?: string;
  duration_ms?: number;
}

export interface TranscriptMessage {
  role: "user" | "assistant" | "tool";
  content: unknown;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
}

export class ChatSession {
  medications: NormalizedMedication[] = [];
  transcript: TranscriptMessage[] = [];
  last_report: MedicationSafetyReport | null = null;
  last_trace: ToolCallRecord[] = [];
  provider_name = "template";
  provider_model: string | null = null;
  privacy_mode: "offline" | "cloud" = "offline";

  constructor(init?: Partial<Pick<ChatSession, "provider_name" | "provider_model" | "privacy_mode">>) {
    if (init?.provider_name) this.provider_name = init.provider_name;
    if (init?.provider_model !== undefined) this.provider_model = init.provider_model;
    if (init?.privacy_mode) this.privacy_mode = init.privacy_mode;
  }

  /** Inputs in stable order for tools that default to "current session meds". */
  medicationInputs(): string[] {
    return this.medications.map((m) => m.input_name);
  }

  clearTurnTrace(): void {
    this.last_trace = [];
  }
}
