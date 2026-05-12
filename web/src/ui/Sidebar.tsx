import type { ConversationSummary } from "./conversation-store";

export type MedicalAudience = "regular" | "doctor" | "older";

export function Sidebar({
  conversations,
  activeId,
  medicalAudience,
  onSelect,
  onNew,
  onDelete,
  onToggle,
  onMedicalAudienceChange,
}: {
  conversations: ConversationSummary[];
  activeId: string;
  medicalAudience: MedicalAudience;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onToggle: () => void;
  onMedicalAudienceChange: (audience: MedicalAudience) => void;
}) {
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <div className="brand-mark" aria-hidden="true">M</div>
        <button type="button" onClick={onToggle} aria-label="Toggle sidebar">⌘</button>
      </div>
      <button type="button" className="new-chat-button" onClick={onNew}>
        <span aria-hidden="true">✎</span>
        New chat
      </button>
      <nav className="conversation-list">
        <p className="sidebar-section-label">Recents</p>
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`conversation-row ${conversation.id === activeId ? "active" : ""}`}
          >
            <button type="button" className="conversation-select" onClick={() => onSelect(conversation.id)}>
              <span>{conversation.title}</span>
              <small>{conversation.updatedLabel}</small>
            </button>
            <button
              type="button"
              className="conversation-delete"
              aria-label={`Delete ${conversation.title}`}
              onClick={(event) => {
                event.stopPropagation();
                onDelete(conversation.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </nav>
      <div className="audience-control">
        <label htmlFor="medical-audience">How technical?</label>
        <select
          id="medical-audience"
          value={medicalAudience}
          onChange={(event) => onMedicalAudienceChange(event.target.value as MedicalAudience)}
        >
          <option value="regular">Regular person</option>
          <option value="doctor">Doctor / clinician</option>
          <option value="older">Simple language</option>
        </select>
      </div>
    </aside>
  );
}
