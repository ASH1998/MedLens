export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

export function Message({ message }: { message: UiMessage }) {
  return (
    <article className={`message message-${message.role}`}>
      <div className="message-avatar">{message.role === "user" ? "U" : "M"}</div>
      <div className="message-body">
        <div className="message-role">{message.role === "user" ? "You" : "MedLens"}</div>
        <div className="message-content">{renderText(message.content || (message.pending ? "Thinking..." : ""))}</div>
      </div>
    </article>
  );
}

function renderText(value: string) {
  return value.split(/\n{2,}/).map((block, index) => {
    const lines = block.split("\n");
    if (lines.every((line) => line.trim().startsWith("- "))) {
      return (
        <ul key={index}>
          {lines.map((line, i) => (
            <li key={i}>{line.replace(/^\s*-\s*/, "")}</li>
          ))}
        </ul>
      );
    }
    return <p key={index}>{block}</p>;
  });
}
