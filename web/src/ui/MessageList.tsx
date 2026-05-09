import { Message } from "./Message";
import type { UiMessage } from "./Message";

export function MessageList({ messages }: { messages: UiMessage[] }) {
  if (messages.length === 0) {
    return (
      <div className="empty-state">
        <h2>Medication Safety Check</h2>
        <p>Ask about a medicine pair or add a list such as Advil and Warfarin.</p>
      </div>
    );
  }
  return (
    <div className="message-list">
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
    </div>
  );
}
