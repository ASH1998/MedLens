// Anthropic provider — fetch against api.anthropic.com/v1/messages with native
// tool-use. Browser usage requires the explicit
// `anthropic-dangerous-direct-browser-access: true` header so Anthropic's CORS
// gate accepts cross-origin requests from the PWA.

import { toAnthropicTools } from "../tools/registry";
import type { AgentMessage, NativeToolProvider, ToolCall, ToolModelResponse } from "./types";

export interface AnthropicOptions {
  apiKey: string;
  model?: string;
}

export class AnthropicProvider implements NativeToolProvider {
  readonly name = "anthropic";
  readonly model: string;

  constructor(private readonly opts: AnthropicOptions) {
    this.model = opts.model ?? "claude-sonnet-4-6";
  }

  async generateWithTools(
    systemPrompt: string,
    messages: AgentMessage[],
    tools: unknown,
  ): Promise<ToolModelResponse> {
    const payload = {
      model: this.model,
      max_tokens: 1500,
      temperature: 0.4,
      system: systemPrompt,
      messages: toAnthropicMessages(messages),
      tools: (tools as ReturnType<typeof toAnthropicTools> | null) ?? toAnthropicTools(),
      tool_choice: { type: "auto" },
    };
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": this.opts.apiKey,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Anthropic ${res.status}: ${body.slice(0, 400)}`);
    }
    const data = (await res.json()) as { content?: AnthropicBlock[] };
    const blocks = data.content ?? [];
    const textChunks: string[] = [];
    const tool_calls: ToolCall[] = [];
    for (const block of blocks) {
      if (block.type === "text" && typeof block.text === "string") textChunks.push(block.text);
      if (block.type === "tool_use") {
        tool_calls.push({
          id: String(block.id ?? ""),
          name: String(block.name ?? ""),
          args: { ...((block.input as Record<string, unknown>) ?? {}) },
        });
      }
    }
    return { text: textChunks.join("").trim(), tool_calls };
  }
}

interface AnthropicBlock {
  type: "text" | "tool_use";
  text?: string;
  id?: string;
  name?: string;
  input?: unknown;
}

function toAnthropicMessages(messages: AgentMessage[]): object[] {
  const out: object[] = [];
  for (const m of messages) {
    if (m.role === "tool") {
      out.push({
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: String(m.tool_call_id ?? ""),
            content: JSON.stringify(m.content ?? {}),
          },
        ],
      });
    } else if (m.role === "assistant" && Array.isArray(m.tool_calls)) {
      const blocks: object[] = [];
      const text = String(m.content ?? "").trim();
      if (text) blocks.push({ type: "text", text });
      for (const call of m.tool_calls) {
        blocks.push({ type: "tool_use", id: call.id, name: call.name, input: call.args });
      }
      out.push({ role: "assistant", content: blocks });
    } else {
      out.push({ role: m.role === "assistant" ? "assistant" : "user", content: String(m.content ?? "") });
    }
  }
  return out;
}
