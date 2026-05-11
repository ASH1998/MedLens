import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { runAgentTurn } from "../agent/loop";
import { handleCommand } from "../agent/commands";
import { ChatSession } from "../chat/session";
import type { ToolCallRecord } from "../chat/session";
import type { BlobStore } from "../db/opfs";
import type { DbHandles } from "../db/stores";
import { AnthropicProvider } from "../providers/anthropic";
import { BedrockProvider } from "../providers/bedrock";
import { GeminiProvider } from "../providers/gemini";
import { getApiKey } from "../providers/keystore";
import type { NativeToolProvider } from "../providers/types";
import { MedicationSafetyStore } from "../tools/safety-store";
import { Composer } from "./Composer";
import { FirstRunSetup } from "./FirstRunSetup";
import { MessageList } from "./MessageList";
import type { UiMessage } from "./Message";
import { Settings } from "./Settings";
import type { ProviderChoice } from "./Settings";
import { Sidebar } from "./Sidebar";
import type { MedicalAudience } from "./Sidebar";
import { ToolTrace } from "./ToolTrace";
import {
  loadConversations,
  newConversation,
  saveConversations,
  summaries,
  titleFromMessage,
} from "./conversation-store";
import type { PersistedConversation } from "./conversation-store";
import { streamText } from "./streaming";

type ReadyState =
  | { kind: "setup" }
  | { kind: "ready"; store: BlobStore; handles: DbHandles; safety: MedicationSafetyStore };

const PROVIDER_STORAGE_KEY = "medlens.provider";
const AUDIENCE_STORAGE_KEY = "medlens.medicalAudience";

export function App() {
  const [ready, setReady] = useState<ReadyState>({ kind: "setup" });
  const [conversations, setConversations] = useState<PersistedConversation[]>(() => loadConversations());
  const [activeId, setActiveId] = useState(() => conversations[0].id);
  const [providerChoice, setProviderChoice] = useState<ProviderChoice>(() => loadProviderChoice());
  const [medicalAudience, setMedicalAudience] = useState<MedicalAudience>(() => loadMedicalAudience());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState<ToolCallRecord[]>([]);
  const sessionRef = useRef(new ChatSession());
  const streamAbortRef = useRef<AbortController | null>(null);
  const conversationsRef = useRef(conversations);

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];

  useEffect(() => {
    conversationsRef.current = conversations;
    saveConversations(conversations);
  }, [conversations]);

  useEffect(() => {
    localStorage.setItem(PROVIDER_STORAGE_KEY, providerChoice);
  }, [providerChoice]);

  useEffect(() => {
    localStorage.setItem(AUDIENCE_STORAGE_KEY, medicalAudience);
  }, [medicalAudience]);

  useEffect(() => {
    const conversation = conversationsRef.current.find((c) => c.id === activeId) ?? conversationsRef.current[0];
    const session = new ChatSession({
      provider_name: providerChoice,
      privacy_mode: "cloud",
    });
    if (conversation?.messages) {
      session.transcript = conversation.messages.map((m) => ({ role: m.role, content: m.content }));
    }
    if (conversation?.medications && ready.kind === "ready") {
      session.medications = ready.safety.normalizeMedicationNames(conversation.medications);
    }
    sessionRef.current = session;
    setTrace([]);
  }, [activeId, providerChoice, ready]);

  useEffect(() => {
    const handles = ready.kind === "ready" ? ready.handles : null;
    return () => handles?.close();
  }, [ready]);

  const provider = useMemo(() => makeProvider(providerChoice), [providerChoice]);

  const updateActive = useCallback((updater: (conversation: PersistedConversation) => PersistedConversation) => {
    setConversations((current) => current.map((c) => (c.id === activeId ? updater(c) : c)));
  }, [activeId]);

  const addMessage = useCallback((message: UiMessage) => {
    updateActive((conversation) => ({
      ...conversation,
      title: conversation.messages.length === 0 && message.role === "user" ? titleFromMessage(message.content) : conversation.title,
      messages: [...conversation.messages, message],
      updatedAt: Date.now(),
    }));
  }, [updateActive]);

  const replaceMessage = useCallback((id: string, content: string, pending = false) => {
    updateActive((conversation) => ({
      ...conversation,
      messages: conversation.messages.map((message) =>
        message.id === id ? { ...message, content, pending } : message,
      ),
      medications: sessionRef.current.medicationInputs(),
      updatedAt: Date.now(),
    }));
  }, [updateActive]);

  const send = useCallback(async (message: string) => {
    if (ready.kind !== "ready" || busy) return;
    setBusy(true);
    streamAbortRef.current?.abort();
    const abort = new AbortController();
    streamAbortRef.current = abort;

    const assistantId = crypto.randomUUID();
    addMessage({ id: crypto.randomUUID(), role: "user", content: message });
    addMessage({ id: assistantId, role: "assistant", content: "", pending: true });

    try {
      let finalText: string;
      if (message.trim().startsWith("/")) {
        const result = await handleCommand(message, { store: ready.safety, session: sessionRef.current });
        finalText = formatCommandPayload(result.payload);
      } else {
        const result = await runAgentTurn({
          provider,
          session: sessionRef.current,
          store: ready.safety,
          user_message: message,
          audience_prompt: medicalAudiencePrompt(medicalAudience),
        });
        finalText = result.final_text;
      }
      setTrace([...sessionRef.current.last_trace]);
      await streamText(finalText, (chunk) => replaceMessage(assistantId, chunk, chunk.length < finalText.length), abort.signal);
      replaceMessage(assistantId, finalText, false);
    } catch (err) {
      replaceMessage(assistantId, providerErrorMessage(provider, err), false);
    } finally {
      setBusy(false);
    }
  }, [addMessage, busy, medicalAudience, provider, ready, replaceMessage]);

  if (ready.kind === "setup") {
    return (
      <FirstRunSetup
        onReady={({ store, handles }) =>
          setReady({ kind: "ready", store, handles, safety: new MedicationSafetyStore(handles) })
        }
      />
    );
  }

  function createConversation() {
    const next = newConversation();
    setConversations((current) => [next, ...current]);
    setActiveId(next.id);
  }

  function deleteConversation(id: string) {
    setConversations((current) => {
      const remaining = current.filter((conversation) => conversation.id !== id);
      const next = remaining.length > 0 ? remaining : [newConversation()];
      if (id === activeId) setActiveId(next[0].id);
      return next;
    });
  }

  function resetData() {
    if (ready.kind === "ready") ready.handles.close();
    setReady({ kind: "setup" });
  }

  return (
    <div className={`app-shell ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      {sidebarOpen && (
        <Sidebar
          conversations={summaries(conversations)}
          activeId={activeId}
          medicalAudience={medicalAudience}
          onSelect={setActiveId}
          onNew={createConversation}
          onDelete={deleteConversation}
          onToggle={() => setSidebarOpen(false)}
          onMedicalAudienceChange={setMedicalAudience}
        />
      )}
      <main className="chat-shell">
        <header className="chat-header">
          <div>
            {!sidebarOpen && (
              <button type="button" className="show-sidebar-button" onClick={() => setSidebarOpen(true)} aria-label="Show sidebar">
                ⌘
              </button>
            )}
            <h1>MedLens</h1>
            <p>{providerLabel(provider)} · {sessionRef.current.medicationInputs().length} meds in session</p>
          </div>
          <button type="button" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </header>
        <MessageList messages={active.messages} />
        <ToolTrace trace={trace} />
        <Composer disabled={busy} onSend={send} />
      </main>
      <Settings
        open={settingsOpen}
        provider={providerChoice}
        store={ready.store}
        onClose={() => setSettingsOpen(false)}
        onProviderChange={setProviderChoice}
        onDataDeleted={resetData}
      />
    </div>
  );
}

function makeProvider(choice: ProviderChoice): NativeToolProvider {
  if (choice === "bedrock") return new BedrockProvider();
  if (choice === "gemini") {
    const apiKey = getApiKey("gemini");
    if (apiKey) return new GeminiProvider({ apiKey });
    return missingKeyProvider("gemini");
  }
  if (choice === "anthropic") {
    const apiKey = getApiKey("anthropic");
    if (apiKey) return new AnthropicProvider({ apiKey });
    return missingKeyProvider("anthropic");
  }
  return new BedrockProvider();
}

function providerLabel(provider: NativeToolProvider): string {
  return provider.name === "bedrock" ? "bedrock claude" : provider.name;
}

function formatCommandPayload(payload: unknown): string {
  if (typeof payload === "string") return payload;
  return JSON.stringify(payload, null, 2);
}

function providerErrorMessage(provider: NativeToolProvider, err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  return `${provider.name} request failed.\n\n${message}`;
}

function loadProviderChoice(): ProviderChoice {
  if (typeof localStorage === "undefined") return "bedrock";
  const value = localStorage.getItem(PROVIDER_STORAGE_KEY);
  return value === "bedrock" || value === "gemini" || value === "anthropic" ? value : "bedrock";
}

function loadMedicalAudience(): MedicalAudience {
  if (typeof localStorage === "undefined") return "regular";
  const value = localStorage.getItem(AUDIENCE_STORAGE_KEY);
  return value === "doctor" || value === "regular" || value === "older" ? value : "regular";
}

function medicalAudiencePrompt(audience: MedicalAudience): string {
  if (audience === "doctor") {
    return [
      "The user is a doctor or clinician.",
      "Use concise clinical terminology, interaction mechanisms, severity rationale, monitoring considerations, and evidence caveats.",
      "Do not over-explain basic medical terms, but keep patient-safety uncertainty explicit.",
    ].join(" ");
  }
  if (audience === "older") {
    return [
      "The user needs simple, older-adult-friendly language.",
      "Use short sentences and common words, avoid jargon, and explain any necessary medical term in plain language.",
      "Focus on what to do next, what warning signs to watch for, and when to ask a doctor or pharmacist for help.",
    ].join(" ");
  }
  return [
    "The user is a regular adult without assumed medical training.",
    "Use relaxed plain language, briefly explain unfamiliar terms, and give practical next steps.",
    "Keep the answer reassuring but direct, and do not add technical detail unless it helps the user act safely.",
  ].join(" ");
}

function missingKeyProvider(name: "gemini" | "anthropic"): NativeToolProvider {
  return {
    name,
    async generateWithTools() {
      throw new Error(`${name} API key is not set. Open Settings, save a valid ${name} key, then try again.`);
    },
  };
}
