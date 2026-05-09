import type { ConversationSummary } from "./conversation-store";

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
}: {
  conversations: ConversationSummary[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <strong>MedLens</strong>
        <button type="button" onClick={onNew} aria-label="New chat">
          +
        </button>
      </div>
      <nav className="conversation-list">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            className={conversation.id === activeId ? "active" : ""}
            onClick={() => onSelect(conversation.id)}
          >
            <span>{conversation.title}</span>
            <small>{conversation.updatedLabel}</small>
          </button>
        ))}
      </nav>
    </aside>
  );
}
