export type AppItem = {
  id: string;
  name: string;
  description: string;
  status: string;
  system_prompt: string;
  model_provider: string;
  model_name: string;
  model_credential_id: string;
  model_base_url: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  workflow_spec: Record<string, unknown>;
};

export type ModelCredential = {
  id: string;
  provider: string;
  name: string;
  masked_api_key: string;
  created_at: string;
  updated_at: string;
};

export type ToolItem = {
  name: string;
  label: string;
  description: string;
};

export type UserItem = {
  id: string;
  email: string;
  created_at: string;
  updated_at: string;
};

export type AuthResponse = {
  token: string;
  user: UserItem;
};

export type AppTool = {
  tool_name: string;
  enabled: boolean;
};

export type RunItem = {
  id: string;
  status: string;
  latency_ms: number;
  error: string;
  created_at: string;
};

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};
