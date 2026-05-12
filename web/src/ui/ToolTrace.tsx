import type { ToolCallRecord } from "../chat/session";

export function ToolTrace({ trace }: { trace: ToolCallRecord[] }) {
  if (trace.length === 0) return null;
  return (
    <details className="tool-trace">
      <summary>Tool trace · {trace.length}</summary>
      <div className="trace-list">
        {trace.map((record, index) => (
          <div className="trace-row" key={`${record.name}-${index}`}>
            <div>
              <strong>{record.name}</strong>
              <span>{record.duration_ms ?? 0} ms</span>
            </div>
            <pre>{JSON.stringify(record.args, null, 2)}</pre>
          </div>
        ))}
      </div>
    </details>
  );
}
