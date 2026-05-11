import { useEffect, useRef } from "react";
import { Message } from "./Message";
import type { UiMessage } from "./Message";

export function MessageList({ messages }: { messages: UiMessage[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, messages.at(-1)?.content, messages.at(-1)?.pending]);

  if (messages.length === 0) {
    return (
      <div className="empty-state">
        <h2>How can I help with your medicines?</h2>
        <div className="suggestion-row">
          <button type="button">Check interactions</button>
          <button type="button">Explain a medicine</button>
          <button type="button">Review my list</button>
        </div>
      </div>
    );
  }
  return (
    <div className="message-list">
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
      <div ref={endRef} aria-hidden="true" />
    </div>
  );
}
