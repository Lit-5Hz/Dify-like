import type {
  AppItem,
  AuthResponse,
  KnowledgeBase,
  KnowledgeDocument,
  MessageItem,
  ModelCredential,
  PlatformAssistantApplyResponse,
  PlatformAssistantChatResponse,
  PlatformSkillItem,
  RunItem,
  ExternalMcpServerItem,
  ExternalMcpToolItem,
  ToolItem,
  UserItem,
  WorkflowItem,
  WorkflowMcpServerItem,
  WorkflowMcpServerProvisionItem,
  WorkflowVersionItem,
} from "./types";

const API_BASE = "http://localhost:8000/api";
const AUTH_TOKEN_KEY = "dify_like_auth_token";

let authToken = typeof window !== "undefined" ? window.localStorage.getItem(AUTH_TOKEN_KEY) ?? "" : "";

function applyAuthToken(token: string) {
  authToken = token.trim();
  if (typeof window === "undefined") return;
  if (authToken) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, authToken);
  } else {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

async function readErrorMessage(response: Response) {
  const text = (await response.text()) || response.statusText;
  try {
    const payload = JSON.parse(text) as { detail?: string };
    return payload.detail || text;
  } catch {
    return text;
  }
}

function getRequestHeaders(init?: RequestInit) {
  const headers = new Headers(init?.headers ?? {});
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = getRequestHeaders(init);
  const body = init?.body;
  if (body && !(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

export const api = {
  setAuthToken: applyAuthToken,
  getAuthToken: () => authToken,
  me: () => request<UserItem>("/auth/me"),
  login: (payload: { email: string; password: string }) =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  register: (payload: { email: string; password: string }) =>
    request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listApps: () => request<AppItem[]>("/apps"),
  createApp: () =>
    request<AppItem>("/apps", {
      method: "POST",
      body: JSON.stringify({
        name: "知识库问答应用",
        description: "创建者维护知识库，使用者只能运行已发布 Workflow。",
        system_prompt: "你是一个专业、耐心、简洁的智能体。优先依据检索到的知识库上下文回答。",
        model_provider: "deepseek",
        model_name: "deepseek-v4-pro",
        model_credential_id: "",
        model_base_url: "https://api.deepseek.com/v1",
        temperature: 70,
        top_p: 100,
        max_tokens: 1024,
      }),
    }),
  updateApp: (appId: string, payload: Partial<AppItem>) =>
    request<AppItem>(`/apps/${appId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteApp: (appId: string) =>
    request<{ ok: boolean }>(`/apps/${appId}`, {
      method: "DELETE",
    }),
  listWorkflows: (appId: string) => request<WorkflowItem[]>(`/apps/${appId}/workflows`),
  createWorkflow: (appId: string, payload: { name: string; description?: string; draft_spec?: Record<string, unknown> }) =>
    request<WorkflowItem>(`/apps/${appId}/workflows`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getWorkflow: (workflowId: string) => request<WorkflowItem>(`/workflows/${workflowId}`),
  updateWorkflow: (workflowId: string, payload: Partial<Pick<WorkflowItem, "name" | "description" | "draft_spec">>) =>
    request<WorkflowItem>(`/workflows/${workflowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteWorkflow: (workflowId: string) =>
    request<{ ok: boolean }>(`/workflows/${workflowId}`, {
      method: "DELETE",
    }),
  publishWorkflow: (workflowId: string) =>
    request<WorkflowVersionItem>(`/workflows/${workflowId}/publish`, {
      method: "POST",
    }),
  listWorkflowVersions: (workflowId: string) => request<WorkflowVersionItem[]>(`/workflows/${workflowId}/versions`),
  getWorkflowMcpServer: (workflowId: string) => request<WorkflowMcpServerItem | null>(`/workflows/${workflowId}/mcp-server`),
  upsertWorkflowMcpServer: (
    workflowId: string,
    payload: { enabled: boolean; server_name: string; server_slug: string; description: string },
  ) =>
    request<WorkflowMcpServerProvisionItem>(`/workflows/${workflowId}/mcp-server`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  rotateWorkflowMcpServerToken: (workflowId: string) =>
    request<WorkflowMcpServerProvisionItem>(`/workflows/${workflowId}/mcp-server/rotate-token`, {
      method: "POST",
    }),
  listKnowledgeBases: () => request<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (payload: { name: string; description: string }) =>
    request<KnowledgeBase>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateKnowledgeBase: (kbId: string, payload: Partial<KnowledgeBase>) =>
    request<KnowledgeBase>(`/knowledge-bases/${kbId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteKnowledgeBase: (kbId: string) =>
    request<{ ok: boolean }>(`/knowledge-bases/${kbId}`, {
      method: "DELETE",
    }),
  listKnowledgeDocuments: (kbId: string) => request<KnowledgeDocument[]>(`/knowledge-bases/${kbId}/documents`),
  uploadKnowledgeDocument: async (kbId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const headers = getRequestHeaders();
    const response = await fetch(`${API_BASE}/knowledge-bases/${kbId}/documents`, {
      method: "POST",
      body: form,
      headers,
    });
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    return response.json() as Promise<KnowledgeDocument>;
  },
  deleteKnowledgeDocument: (kbId: string, documentId: string) =>
    request<{ ok: boolean }>(`/knowledge-bases/${kbId}/documents/${documentId}`, {
      method: "DELETE",
    }),
  rebuildKnowledgeBase: (kbId: string) =>
    request<KnowledgeBase>(`/knowledge-bases/${kbId}/rebuild`, {
      method: "POST",
    }),
  retrievalCapabilities: () => request<Record<string, unknown>>("/retrieval/capabilities"),
  listTools: () => request<ToolItem[]>("/tools"),
  listExternalMcpServers: () => request<ExternalMcpServerItem[]>("/mcp/servers"),
  createExternalMcpServer: (payload: {
    name: string;
    description: string;
    transport_type: string;
    server_url: string;
    auth_type: string;
    auth_secret?: string;
    custom_headers?: Record<string, string>;
    oauth_authorization_url?: string;
    oauth_token_url?: string;
    oauth_client_id?: string;
    oauth_client_secret?: string;
    oauth_scopes?: string;
    oauth_resource?: string;
  }) =>
    request<ExternalMcpServerItem>("/mcp/servers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getExternalMcpServer: (serverId: string) => request<ExternalMcpServerItem>(`/mcp/servers/${serverId}`),
  updateExternalMcpServer: (
    serverId: string,
    payload: Partial<{
      name: string;
      description: string;
      transport_type: string;
      server_url: string;
      auth_type: string;
      auth_secret: string;
      custom_headers: Record<string, string>;
      oauth_authorization_url: string;
      oauth_token_url: string;
      oauth_client_id: string;
      oauth_client_secret: string;
      oauth_scopes: string;
      oauth_resource: string;
    }>,
  ) =>
    request<ExternalMcpServerItem>(`/mcp/servers/${serverId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteExternalMcpServer: (serverId: string) =>
    request<{ ok: boolean }>(`/mcp/servers/${serverId}`, {
      method: "DELETE",
    }),
  syncExternalMcpServer: (serverId: string) =>
    request<ExternalMcpServerItem>(`/mcp/servers/${serverId}/sync`, {
      method: "POST",
    }),
  connectExternalMcpServerOAuth: (serverId: string) =>
    request<{ authorization_url: string }>(`/mcp/servers/${serverId}/oauth/connect`, {
      method: "POST",
    }),
  disconnectExternalMcpServerOAuth: (serverId: string) =>
    request<ExternalMcpServerItem>(`/mcp/servers/${serverId}/oauth/disconnect`, {
      method: "POST",
    }),
  listExternalMcpTools: (serverId: string) => request<ExternalMcpToolItem[]>(`/mcp/servers/${serverId}/tools`),
  listModelCredentials: () => request<ModelCredential[]>("/model-credentials"),
  createModelCredential: (payload: { provider: string; name: string; api_key: string }) =>
    request<ModelCredential>("/model-credentials", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteModelCredential: (credentialId: string) =>
    request<{ ok: boolean }>(`/model-credentials/${credentialId}`, {
      method: "DELETE",
    }),
  listWorkflowRuns: (workflowId: string) => request<RunItem[]>(`/workflows/${workflowId}/runs`),
  listMessages: (conversationId: string) => request<MessageItem[]>(`/conversations/${conversationId}/messages`),
  listSkills: () => request<PlatformSkillItem[]>("/skills"),
  listPlatformSkills: () => request<PlatformSkillItem[]>("/skills/platform"),
  listVisibleSkills: () => request<PlatformSkillItem[]>("/skills/visible"),
  publishSkill: (skillId: string) =>
    request<PlatformSkillItem>(`/skills/${skillId}/publish`, {
      method: "POST",
    }),
  revokeSkill: (skillId: string) =>
    request<PlatformSkillItem>(`/skills/${skillId}/revoke`, {
      method: "POST",
    }),
  deleteSkill: (skillId: string) =>
    request<{ ok: boolean }>(`/skills/${skillId}`, {
      method: "DELETE",
    }),
  synthesizeSkill: (payload: { run_id: string; feedback?: string; skill_name?: string; skill_id?: string }) =>
    request<PlatformSkillItem>("/skills/synthesize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  platformAssistantChat: (payload: { message: string; conversation_id?: string | null; skill_ids?: string[] }) =>
    request<PlatformAssistantChatResponse>("/platform-assistant/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  platformAssistantApply: (payload: {
    app_name: string;
    app_description?: string;
    workflow_name: string;
    workflow_description?: string;
    draft_spec: Record<string, unknown>;
  }) =>
    request<PlatformAssistantApplyResponse>("/platform-assistant/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

export async function downloadSkill(skillId: string) {
  const headers = getRequestHeaders();
  const response = await fetch(`${API_BASE}/skills/${skillId}/download`, { headers });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.blob();
}

export async function streamChat(
  workflowId: string,
  query: string,
  conversationId: string | null,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  const headers = getRequestHeaders();
  const response = await fetch(`${API_BASE}/workflows/${workflowId}/chat`, {
    method: "POST",
    headers: {
      ...Object.fromEntries(headers.entries()),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, conversation_id: conversationId, stream: true }),
  });
  if (!response.ok || !response.body) {
    throw new Error(await readErrorMessage(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      const eventLine = part.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      onEvent(eventLine.slice(7), JSON.parse(dataLine.slice(6)));
    }
  }
}
