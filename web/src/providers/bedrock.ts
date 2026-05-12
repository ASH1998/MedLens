// Local-dev Bedrock provider. The browser posts the Claude payload to the Vite
// dev server, and the dev server signs the AWS Bedrock request with ../.env.

import { toAnthropicTools } from "../tools/registry";
import type { AgentMessage, NativeToolProvider, ToolCall, ToolModelResponse } from "./types";

export class BedrockProvider implements NativeToolProvider {
  readonly name = "bedrock";

  async generateWithTools(
    systemPrompt: string,
    messages: AgentMessage[],
    tools: unknown,
  ): Promise<ToolModelResponse> {
    const payload = {
      anthropic_version: "bedrock-2023-05-31",
      max_tokens: 1500,
      temperature: 0.4,
      system: systemPrompt,
      messages: toBedrockMessages(messages),
      tools: (tools as ReturnType<typeof toAnthropicTools> | null) ?? toAnthropicTools(),
      tool_choice: { type: "auto" },
    };
    const res = await fetch("/api/bedrock/claude", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Bedrock proxy ${res.status}: ${body.slice(0, 800)}`);
    }
    const data = (await res.json()) as { content?: BedrockBlock[] };
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

interface BedrockBlock {
  type: "text" | "tool_use";
  text?: string;
  id?: string;
  name?: string;
  input?: unknown;
}

function toBedrockMessages(messages: AgentMessage[]): object[] {
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
