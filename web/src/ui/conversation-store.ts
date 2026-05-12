import type { UiMessage } from "./Message";

const KEY = "medlens.conversations.v1";

export interface PersistedConversation {
  id: string;
  title: string;
  messages: UiMessage[];
  medications: string[];
  updatedAt: number;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updatedLabel: string;
}

export function newConversation(): PersistedConversation {
  const now = Date.now();
  return { id: crypto.randomUUID(), title: "New chat", messages: [], medications: [], updatedAt: now };
}

export function loadConversations(): PersistedConversation[] {
  if (typeof localStorage === "undefined") return [newConversation()];
  const raw = localStorage.getItem(KEY);
  if (!raw) return [newConversation()];
  try {
    const parsed = JSON.parse(raw) as PersistedConversation[];
    return parsed.length > 0 ? parsed : [newConversation()];
  } catch {
    return [newConversation()];
  }
}

export function saveConversations(conversations: PersistedConversation[]): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(KEY, JSON.stringify(conversations.slice(0, 30)));
}

export function summaries(conversations: PersistedConversation[]): ConversationSummary[] {
  return conversations.map((conversation) => ({
    id: conversation.id,
    title: conversation.title,
    updatedLabel: new Date(conversation.updatedAt).toLocaleDateString(),
  }));
}

export function titleFromMessage(message: string): string {
  const compact = message.replace(/\s+/g, " ").trim();
  return compact.length > 36 ? `${compact.slice(0, 35)}...` : compact || "New chat";
}
