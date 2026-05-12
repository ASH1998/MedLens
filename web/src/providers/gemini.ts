// Google Gemini provider — fetch against generativelanguage.googleapis.com
// with native function-calling. Mirrors `medlens/agent.py:GeminiProvider`.

import { toGeminiTools } from "../tools/registry";
import type { AgentMessage, NativeToolProvider, ToolCall, ToolModelResponse } from "./types";

export interface GeminiOptions {
  apiKey: string;
  model?: string;
}

export class GeminiProvider implements NativeToolProvider {
  readonly name = "gemini";
  readonly model: string;

  constructor(private readonly opts: GeminiOptions) {
    this.model = opts.model ?? "gemini-2.5-flash";
  }

  async generateWithTools(
    systemPrompt: string,
    messages: AgentMessage[],
    tools: unknown,
  ): Promise<ToolModelResponse> {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${this.model}:generateContent?key=${encodeURIComponent(this.opts.apiKey)}`;
    const payload = {
      systemInstruction: { parts: [{ text: systemPrompt }] },
      contents: toGeminiContents(messages),
      tools: (tools as { functionDeclarations: object[] }[] | null) ?? toGeminiTools(),
      generationConfig: { temperature: 0.4, maxOutputTokens: 1500 },
    };
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Gemini ${res.status}: ${body.slice(0, 400)}`);
    }
    const data = (await res.json()) as {
      candidates?: { content?: { parts?: GeminiPart[] } }[];
    };
    const parts = data.candidates?.[0]?.content?.parts ?? [];
    const textChunks: string[] = [];
    const tool_calls: ToolCall[] = [];
    parts.forEach((part, index) => {
      if (typeof part.text === "string") textChunks.push(part.text);
      if (part.functionCall) {
        tool_calls.push({
          id: `gemini-${index}`,
          name: String(part.functionCall.name ?? ""),
          args: { ...(part.functionCall.args ?? {}) },
        });
      }
    });
    return { text: textChunks.join("").trim(), tool_calls };
  }
}

interface GeminiPart {
  text?: string;
  functionCall?: { name: string; args?: Record<string, unknown> };
}

function toGeminiContents(messages: AgentMessage[]): object[] {
  const out: object[] = [];
  for (const m of messages) {
    if (m.role === "tool") {
      out.push({
        role: "function",
        parts: [
          {
            functionResponse: {
              name: String(m.name ?? ""),
              response: m.content ?? {},
            },
          },
        ],
      });
    } else if (m.role === "assistant" && Array.isArray(m.tool_calls)) {
      const parts: object[] = [];
      const text = String(m.content ?? "").trim();
      if (text) parts.push({ text });
      for (const call of m.tool_calls) {
        parts.push({ functionCall: { name: call.name, args: call.args } });
      }
      out.push({ role: "model", parts });
    } else {
      out.push({
        role: m.role === "assistant" ? "model" : "user",
        parts: [{ text: String(m.content ?? "") }],
      });
    }
  }
  return out;
}
