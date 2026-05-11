import { useState } from "react";
import type { FormEvent } from "react";

export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (message: string) => void }) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = value.trim();
    if (!next || disabled) return;
    setValue("");
    onSend(next);
  }

  return (
    <form className="composer" onSubmit={submit}>
      <button type="button" className="composer-tool" disabled={disabled} aria-label="Add">
        +
      </button>
      <textarea
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit(event);
          }
        }}
        placeholder="Ask anything"
        rows={1}
      />
      <button type="submit" disabled={disabled || !value.trim()} aria-label="Send">
        ↑
      </button>
    </form>
  );
}
