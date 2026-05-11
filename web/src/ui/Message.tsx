export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

export function Message({ message }: { message: UiMessage }) {
  return (
    <article className={`message message-${message.role}`}>
      <div className="message-body">
        <div className="message-content">{renderText(message.content || (message.pending ? "Thinking..." : ""))}</div>
      </div>
    </article>
  );
}

function renderText(value: string) {
  return value.split(/\n{2,}/).map((block, index) => {
    const lines = block.split("\n");
    if (isSourcesBlock(lines, block)) {
      return (
        <div className="source-block" key={index}>
          {lines.map((line, i) =>
            i === 0 ? <p key={i}>{renderInline(line)}</p> : <p key={i}>{renderInline(line)}</p>,
          )}
        </div>
      );
    }
    if (lines.every((line) => line.trim().startsWith("- "))) {
      return (
        <ul key={index}>
          {lines.map((line, i) => (
            <li key={i}>{renderInline(line.replace(/^\s*-\s*/, ""))}</li>
          ))}
        </ul>
      );
    }
    if (lines.length > 1 && isStrongLine(lines[0]) && lines.slice(1).every((line) => line.trim().startsWith("- "))) {
      return (
        <div className="message-section" key={index}>
          <p>{renderInline(lines[0])}</p>
          <ul>
            {lines.slice(1).map((line, i) => (
              <li key={i}>{renderInline(line.replace(/^\s*-\s*/, ""))}</li>
            ))}
          </ul>
        </div>
      );
    }
    return <p key={index}>{renderInline(block)}</p>;
  });
}

function isStrongLine(value: string): boolean {
  return /^\s*\*\*[^*]+:\*\*\s*$/.test(value) || /^\s*\*\*[^*]+\*\*\s*$/.test(value);
}

function renderInline(value: string) {
  const parts = value.split(/(\*\*[^*]+\*\*|https?:\/\/[^\s)]+|www\.[^\s)]+)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (/^(https?:\/\/|www\.)/.test(part)) {
      const href = part.startsWith("http") ? part : `https://${part}`;
      return (
        <a key={index} href={href} target="_blank" rel="noreferrer">
          {part}
        </a>
      );
    }
    return part;
  });
}

function isSourcesBlock(lines: string[], block: string): boolean {
  const first = lines[0]?.trim().toLowerCase() ?? "";
  return first === "sources" || first.startsWith("sources:") || /^sources\b/i.test(block.trim());
}
