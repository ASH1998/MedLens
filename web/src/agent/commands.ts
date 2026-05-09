// Slash command handling for the browser chat. Mirrors the terminal command
// surface from `medlens/chat/commands.py`, with async dispatch for sql.js.

import { dispatch } from "../tools/registry";
import type { MedicationSafetyStore } from "../tools/safety-store";
import type { ChatSession } from "../chat/session";

export const HELP_TEXT = `Commands:
/meds - show current medications
/meds aspirin, warfarin - replace the medication list
/add ibuprofen - add medications
/remove ibuprofen - remove medications
/check or /report - rerun the structured report
/why <drug a>, <drug b> - show severity consensus
/sources - show evidence provenance
/trace - show previous tool trace
/clear - clear medications and transcript
/provider - show provider and privacy mode
/help - show this help`;

export interface CommandResult {
  kind: "help" | "trace" | "tool" | "medications" | "report" | "error";
  payload: unknown;
}

export function splitMedicationNames(value: string): string[] {
  const normalized = value.replace(/\s+\band\b\s+/gi, ",");
  return normalized
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export async function handleCommand(
  message: string,
  opts: { store: MedicationSafetyStore; session: ChatSession },
): Promise<CommandResult> {
  const [rawCommand, ...rest] = message.trim().split(/\s+/);
  const command = (rawCommand ?? "").toLowerCase();
  const tail = rest.join(" ").trim();
  const { store, session } = opts;

  if (command === "/help") return { kind: "help", payload: HELP_TEXT };
  if (command === "/trace") return { kind: "trace", payload: session.last_trace };
  if (command === "/provider") {
    return { kind: "tool", payload: await dispatch("current_session_summary", {}, { store, session }) };
  }
  if (command === "/sources") {
    return { kind: "tool", payload: await dispatch("evidence_about", { topic: "sources" }, { store, session }) };
  }
  if (command === "/clear") {
    session.transcript = [];
    return { kind: "tool", payload: await dispatch("clear_medications", {}, { store, session }) };
  }
  if (command === "/meds") {
    if (tail) {
      await dispatch("clear_medications", {}, { store, session });
      return {
        kind: "tool",
        payload: await dispatch("add_medications", { names: splitMedicationNames(tail) }, { store, session }),
      };
    }
    return { kind: "medications", payload: await dispatch("list_medications", {}, { store, session }) };
  }
  if (command === "/add") {
    return {
      kind: "tool",
      payload: await dispatch("add_medications", { names: splitMedicationNames(tail) }, { store, session }),
    };
  }
  if (command === "/remove") {
    return {
      kind: "tool",
      payload: await dispatch("remove_medications", { names: splitMedicationNames(tail) }, { store, session }),
    };
  }
  if (command === "/check" || command === "/report") {
    return { kind: "report", payload: await dispatch("build_structured_report", {}, { store, session }) };
  }
  if (command === "/why") {
    const names = splitMedicationNames(tail);
    if (names.length < 2) return { kind: "error", payload: "Usage: /why <drug a>, <drug b>" };
    return {
      kind: "tool",
      payload: await dispatch("severity_consensus", { drug_a: names[0], drug_b: names[1] }, { store, session }),
    };
  }
  return { kind: "error", payload: `Unknown command: ${command}. Type /help.` };
}
