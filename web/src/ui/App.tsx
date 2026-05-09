import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { runAgentTurn } from "../agent/loop";
import { handleCommand } from "../agent/commands";
import { ChatSession } from "../chat/session";
import type { ToolCallRecord } from "../chat/session";
import type { BlobStore } from "../db/opfs";
import type { DbHandles } from "../db/stores";
import { AnthropicProvider } from "../providers/anthropic";
import { GeminiProvider } from "../providers/gemini";
import { getApiKey } from "../providers/keystore";
import { TemplateProvider } from "../providers/template";
import type { NativeToolProvider } from "../providers/types";
import { MedicationSafetyStore } from "../tools/safety-store";
import { Composer } from "./Composer";
import { FirstRunSetup } from "./FirstRunSetup";
import { MessageList } from "./MessageList";
import type { UiMessage } from "./Message";
import { Settings } from "./Settings";
import type { ProviderChoice } from "./Settings";
import { Sidebar } from "./Sidebar";
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

export function App() {
  const [ready, setReady] = useState<ReadyState>({ kind: "setup" });
  const [conversations, setConversations] = useState<PersistedConversation[]>(() => loadConversations());
  const [activeId, setActiveId] = useState(() => conversations[0].id);
  const [providerChoice, setProviderChoice] = useState<ProviderChoice>("template");
  const [settingsOpen, setSettingsOpen] = useState(false);
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
    const conversation = conversationsRef.current.find((c) => c.id === activeId) ?? conversationsRef.current[0];
    const session = new ChatSession({
      provider_name: providerChoice,
      privacy_mode: providerChoice === "template" ? "offline" : "cloud",
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
        const result = await runAgentTurnWithFallback({
          provider,
          providerChoice,
          session: sessionRef.current,
          store: ready.safety,
          userMessage: message,
        });
        finalText = result.final_text;
      }
      setTrace([...sessionRef.current.last_trace]);
      await streamText(finalText, (chunk) => replaceMessage(assistantId, chunk, chunk.length < finalText.length), abort.signal);
      replaceMessage(assistantId, finalText, false);
    } catch (err) {
      replaceMessage(assistantId, (err as Error).message, false);
    } finally {
      setBusy(false);
    }
  }, [addMessage, busy, provider, providerChoice, ready, replaceMessage]);

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

  function resetData() {
    if (ready.kind === "ready") ready.handles.close();
    setReady({ kind: "setup" });
  }

  return (
    <div className="app-shell">
      <Sidebar
        conversations={summaries(conversations)}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={createConversation}
      />
      <main className="chat-shell">
        <header className="chat-header">
          <div>
            <h1>{active.title}</h1>
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
  if (choice === "gemini") {
    const apiKey = getApiKey("gemini");
    if (apiKey) return new GeminiProvider({ apiKey });
  }
  if (choice === "anthropic") {
    const apiKey = getApiKey("anthropic");
    if (apiKey) return new AnthropicProvider({ apiKey });
  }
  return new TemplateProvider();
}

function providerLabel(provider: NativeToolProvider): string {
  return provider.name === "template" ? "offline template" : provider.name;
}

function formatCommandPayload(payload: unknown): string {
  if (typeof payload === "string") return payload;
  return JSON.stringify(payload, null, 2);
}

async function runAgentTurnWithFallback(args: {
  provider: NativeToolProvider;
  providerChoice: ProviderChoice;
  session: ChatSession;
  store: MedicationSafetyStore;
  userMessage: string;
}) {
  try {
    return await runAgentTurn({
      provider: args.provider,
      session: args.session,
      store: args.store,
      user_message: args.userMessage,
    });
  } catch (err) {
    if (args.providerChoice === "template") throw err;
    args.session.provider_name = "template";
    args.session.privacy_mode = "offline";
    const fallback = await runAgentTurn({
      provider: new TemplateProvider(),
      session: args.session,
      store: args.store,
      user_message: args.userMessage,
    });
    return {
      ...fallback,
      final_text: `The selected cloud provider failed, so I used the offline template instead.\n\n${fallback.final_text}`,
    };
  }
}
