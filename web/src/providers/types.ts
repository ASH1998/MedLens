// Provider-neutral types shared by template / Gemini / Anthropic providers.
// Mirrors `medlens/agent.py:ToolCall` and `ToolModelResponse`.

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolModelResponse {
  text: string;
  tool_calls: ToolCall[];
}

export interface AgentMessage {
  role: "user" | "assistant" | "tool";
  content: unknown;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
}

export interface NativeToolProvider {
  name: string;
  generateWithTools(
    systemPrompt: string,
    messages: AgentMessage[],
    tools: unknown,
  ): Promise<ToolModelResponse>;
}
