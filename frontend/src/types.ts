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

export type WorkflowMcpServerItem = {
  id: string;
  workflow_id: string;
  enabled: boolean;
  server_name: string;
  server_slug: string;
  description: string;
  auth_type: string;
  created_at: string;
  updated_at: string;
};

export type WorkflowMcpServerProvisionItem = WorkflowMcpServerItem & {
  token: string | null;
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

export type ExternalMcpServerItem = {
  id: string;
  owner_user_id: string;
  name: string;
  description: string;
  transport_type: string;
  server_url: string;
  auth_type: string;
  has_auth_secret: boolean;
  has_custom_headers: boolean;
  custom_header_names: string[];
  has_mcp_session: boolean;
  oauth_authorization_url: string;
  oauth_token_url: string;
  oauth_client_id: string;
  oauth_scopes: string;
  oauth_resource: string;
  oauth_connected: boolean;
  oauth_token_expires_at: string | null;
  oauth_last_error: string;
  status: string;
  last_sync_at: string | null;
  last_sync_error: string;
  tool_manifest_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ExternalMcpToolItem = {
  server_id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
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
  timeline?: ChatTimelineItem[];
  status?: "streaming" | "completed" | "error";
};

export type ChatTimelineItem =
  | {
      id: string;
      kind: "retrieval";
      chunks: Record<string, unknown>[];
    }
  | {
      id: string;
      kind: "generation";
      message_id: string;
      phase: "start" | "resume";
      thinking: string;
    }
  | {
      id: string;
      kind: "tool";
      tool_call_id: string;
      name: string;
      input: Record<string, unknown>;
      output?: unknown;
      status: "running" | "completed";
    }
  | {
      id: string;
      kind: "notice";
      level: "warning" | "error";
      message: string;
    };

export type PlatformSkillItem = {
  id: string;
  owner_user_id: string;
  name: string;
  description: string;
  version: string;
  status: string;
  visibility: string;
  publish_status: string;
  source_skill_id: string;
  source_app_id: string;
  source_workflow_id: string;
  source_run_id: string;
  published_at: string | null;
  revoked_at: string | null;
  usage_count: number;
  last_used_at: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type AssistantWorkflowRecommendation = {
  workflow_id: string;
  app_id: string;
  app_name: string;
  workflow_name: string;
  description: string;
  version_id: string;
};

export type AssistantLoadedSkill = {
  skill_id: string;
  name: string;
  version: string;
  visibility: string;
  loaded_files: string[];
  load_stages: string[];
  summary: string;
  score: number;
  match_summary: string;
  deferred_references: string[];
  loaded_references: string[];
};

export type PlatformAssistantChatResponse = {
  conversation_id: string;
  answer: string;
  messages: ChatMessage[];
  recommendations: AssistantWorkflowRecommendation[];
  loaded_skills: AssistantLoadedSkill[];
  load_stages: Record<string, unknown>[];
  deferred_references: Record<string, unknown>[];
  loaded_references: Record<string, unknown>[];
  suggested_app: Record<string, unknown>;
  suggested_workflow: Record<string, unknown>;
  draft_explanation: Record<string, unknown>;
  created_app: AppItem | null;
  created_workflow: WorkflowItem | null;
  allowed_actions: string[];
  model_status: string;
  model_message: string;
};

export type PlatformAssistantApplyResponse = {
  app: AppItem;
  workflow: WorkflowItem;
};
