import type {
  AppItem,
  AppTool,
  AuthResponse,
  KnowledgeDocument,
  MessageItem,
  ModelCredential,
  RunItem,
  RuntimeKnowledgeDocumentUpload,
  ToolItem,
  UserItem,
} from "./types";

const API_BASE = "http://localhost:8000/api";
const AUTH_TOKEN_KEY = "dify_like_auth_token";

let authToken = typeof window !== "undefined" ? window.localStorage.getItem(AUTH_TOKEN_KEY) ?? "" : "";

function applyAuthToken(token: string) {
  authToken = token.trim();
  if (typeof window === "undefined") {
    return;
  }
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
        name: "电商客服 Agent",
        description: "用于演示订单查询、FAQ 检索和运行日志。",
        system_prompt: "你是一个专业、耐心、简洁的电商客服智能体。",
        model_provider: "mock",
        model_name: "mock-react",
        model_credential_id: "",
        model_base_url: "",
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
  listTools: () => request<ToolItem[]>("/tools"),
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
  listRuntimeRagDocuments: (appId: string, conversationId: string | null) =>
    request<KnowledgeDocument[]>(
      `/apps/${appId}/rag/documents${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ""}`,
    ),
  uploadRuntimeRagDocument: async (appId: string, conversationId: string | null, file: File) => {
    const form = new FormData();
    if (conversationId) form.append("conversation_id", conversationId);
    form.append("file", file);
    const headers = getRequestHeaders();
    const response = await fetch(`${API_BASE}/apps/${appId}/rag/documents`, {
      method: "POST",
      body: form,
      headers,
    });
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    return response.json() as Promise<RuntimeKnowledgeDocumentUpload>;
  },
  listAppTools: (appId: string) => request<AppTool[]>(`/apps/${appId}/tools`),
  updateAppTools: (appId: string, toolNames: string[]) =>
    request<AppTool[]>(`/apps/${appId}/tools`, {
      method: "PUT",
      body: JSON.stringify({ tool_names: toolNames }),
    }),
  listRuns: (appId: string) => request<RunItem[]>(`/apps/${appId}/runs`),
  listMessages: (conversationId: string) => request<MessageItem[]>(`/conversations/${conversationId}/messages`),
};

export async function streamChat(
  appId: string,
  query: string,
  conversationId: string | null,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  const headers = getRequestHeaders();
  const response = await fetch(`${API_BASE}/apps/${appId}/chat`, {
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
