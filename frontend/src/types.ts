export type AppItem = {
  id: string;
  owner_user_id: string;
  name: string;
  description: string;
  system_prompt: string;
  model_provider: string;
  model_name: string;
  model_credential_id: string;
  model_base_url: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  created_at: string;
  updated_at: string;
};

export type WorkflowItem = {
  id: string;
  app_id: string;
  name: string;
  description: string;
  draft_spec: Record<string, unknown>;
  published_version_id: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkflowVersionItem = {
  id: string;
  workflow_id: string;
  version_number: number;
  spec_json: Record<string, unknown>;
  created_at: string;
};

export type ModelCredential = {
  id: string;
  provider: string;
  name: string;
  masked_api_key: string;
  created_at: string;
  updated_at: string;
};

export type KnowledgeBase = {
  id: string;
  owner_user_id: string;
  scope: string;
  app_id: string;
  conversation_id: string;
  name: string;
  description: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  embedding_credential_id: string;
  embedding_base_url: string;
  qdrant_collection: string;
  locked: boolean;
  chunk_size: number;
  chunk_overlap: number;
  chunk_strategy: string;
  enable_parent_child: boolean;
  config_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type KnowledgeDocument = {
  id: string;
  knowledge_base_id: string;
  filename: string;
  mime_type: string;
  status: string;
  error: string;
  metadata_json: Record<string, unknown>;
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

export type RunItem = {
  id: string;
  app_id: string;
  workflow_id: string;
  workflow_version_id: string;
  conversation_id: string;
  status: string;
  latency_ms: number;
  error: string;
  created_at: string;
};

export type RunStepItem = {
  id: string;
  run_id: string;
  type: string;
  name: string;
  input_json: Record<string, unknown>;
  output_json: Record<string, unknown>;
  latency_ms: number;
  error: string;
  started_at: string;
  ended_at: string | null;
};

export type MessageItem = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
};

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};
