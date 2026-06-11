import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Circle,
  Code2,
  Database,
  FileText,
  FileUp,
  GitBranch,
  History,
  KeyRound,
  Link2,
  Loader2,
  LogIn,
  LogOut,
  MessageSquare,
  Play,
  Plus,
  RefreshCw,
  Save,
  Send,
  Settings2,
  Trash2,
  UserRound,
  Wrench,
} from "lucide-react";
import { api, streamChat } from "./api";
import type {
  AppItem,
  ChatMessage,
  ExternalMcpServerItem,
  KnowledgeBase,
  KnowledgeDocument,
  ModelCredential,
  RunItem,
  RunStepItem,
  ToolItem,
  UserItem,
  WorkflowItem,
  WorkflowMcpServerItem,
  WorkflowMcpServerProvisionItem,
  WorkflowVersionItem,
} from "./types";
import "./styles.css";

const MODEL_PROVIDERS = ["mock", "openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];
const QUERY_LLM_PROVIDERS = MODEL_PROVIDERS.filter((provider) => provider !== "mock");
const CREDENTIAL_PROVIDERS = ["openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm", "zhipu", "zhipuai"];
const QUERY_ENHANCEMENT_STRATEGIES = ["rewrite", "hyde", "multi_query"];
const MCP_TRANSPORT_TYPES = ["streamable_http"];
const MCP_AUTH_TYPES = ["none", "bearer"];
const NEW_EXTERNAL_MCP_SERVER_ID = "__new_external_mcp_server__";
const PROCESSING_DOCUMENT_STATUSES = new Set(["queued", "parsing", "chunking", "embedding"]);

type NavView = "workspace" | "knowledge" | "credentials";
type AppView = "studio" | "mcp" | "logs";
type AuthAction = "login" | "register";
type WorkflowNode = Record<string, unknown>;

type WorkflowEdge = {
  source: string;
  target: string;
};

type AgentNodeModel = {
  provider?: string;
  model_name?: string;
  credential_id?: string;
  base_url?: string;
  temperature?: string | number;
  top_p?: string | number;
  max_tokens?: string | number;
  model_context_window?: string | number;
  context_reserved_output_tokens?: string | number;
  context_safety_margin?: string | number;
};

type RetrievalNodeModel = {
  enabled?: boolean;
  knowledge_base_ids?: string[];
  retrieval_top_k?: string | number;
  rerank_enabled?: boolean;
  query_enhancement_enabled?: boolean;
  query_enhancement_strategy?: string;
  query_llm_provider?: string;
  query_llm_model?: string;
  query_llm_credential_id?: string;
  query_llm_base_url?: string;
  query_llm_temperature?: string | number;
};

type AgentToolConfig = {
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
};

type OrphanedMcpToolReference = {
  node_id?: string;
  server_id: string;
  name: string;
};

type WorkflowMcpServerDraft = {
  configured: boolean;
  enabled: boolean;
  server_name: string;
  server_slug: string;
  description: string;
  auth_type: string;
};

type ExternalMcpHeaderDraft = {
  name: string;
  value: string;
  saved: boolean;
};

type ExternalMcpServerDraft = {
  name: string;
  description: string;
  transport_type: string;
  server_url: string;
  auth_type: string;
  auth_secret: string;
  custom_headers: ExternalMcpHeaderDraft[];
  custom_headers_dirty: boolean;
};

const NAV_ITEMS: Array<{ id: NavView; label: string; description: string; icon: React.ElementType }> = [
  { id: "workspace", label: "工作室", description: "Apps", icon: Bot },
  { id: "knowledge", label: "知识库", description: "Knowledge Bases", icon: Database },
  { id: "credentials", label: "模型凭证", description: "Provider Keys", icon: KeyRound },
];

const APP_NAV_ITEMS: Array<{ id: AppView; label: string; description: string; icon: React.ElementType }> = [
  { id: "studio", label: "应用编排", description: "Workflow Studio", icon: GitBranch },
  { id: "mcp", label: "MCP 设置", description: "Server & Client", icon: Link2 },
  { id: "logs", label: "运行日志", description: "Runs & Steps", icon: History },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function defaultCredentialProvider(provider: string) {
  return provider && provider !== "mock" ? provider : "openai_compatible";
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return "未同步";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "-";
}

function slugifyMcpServerName(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
}

function defaultQueryLlmBaseUrl(provider: string) {
  if (provider === "openai") return "https://api.openai.com/v1";
  if (provider === "deepseek") return "https://api.deepseek.com/v1";
  if (provider === "dashscope" || provider === "qwen") return "https://dashscope.aliyuncs.com/compatible-mode/v1";
  return "";
}

function getWorkflowNodes(workflow: WorkflowItem): WorkflowNode[] {
  const nodes = workflow.draft_spec.nodes;
  return Array.isArray(nodes) ? nodes.filter(isRecord) : [];
}

function getWorkflowEdges(workflow: WorkflowItem): WorkflowEdge[] {
  const edges = workflow.draft_spec.edges;
  if (!Array.isArray(edges)) return [];
  return edges.flatMap((edge) => {
    if (Array.isArray(edge) && edge.length >= 2) {
      return [{ source: String(edge[0]), target: String(edge[1]) }];
    }
    if (!isRecord(edge)) return [];
    const source = String(edge.source ?? edge.from ?? "");
    const target = String(edge.target ?? edge.to ?? "");
    return source && target ? [{ source, target }] : [];
  });
}

function getWorkflowNodeId(node: WorkflowNode, index: number) {
  return String(node.id ?? node.type ?? `node-${index}`);
}

function getWorkflowNodeType(node: WorkflowNode) {
  return String(node.type ?? "unknown");
}

function isRetrievalNode(node: WorkflowNode) {
  const type = getWorkflowNodeType(node);
  return node.id === "retrieval" || type === "retrieval";
}

function isAgentNode(node: WorkflowNode) {
  const type = getWorkflowNodeType(node);
  return node.id === "agent" || type === "agent" || type === "react_agent";
}

function getWorkflowNodeLabel(node: WorkflowNode, index: number) {
  const id = getWorkflowNodeId(node, index);
  const type = getWorkflowNodeType(node);
  if (id === "start" || type === "start") return "开始";
  if (id === "end" || type === "end") return "结束";
  if (isRetrievalNode(node)) return "检索节点";
  if (isAgentNode(node)) return "Agent 节点";
  return id;
}

function getNodeKindClass(node: WorkflowNode) {
  if (isRetrievalNode(node)) return "retrieval";
  if (isAgentNode(node)) return "agent";
  const type = getWorkflowNodeType(node);
  if (type === "start") return "start";
  if (type === "end") return "end";
  return "default";
}

function getAgentNodeModel(node: WorkflowNode | null): AgentNodeModel {
  return isRecord(node?.model) ? (node.model as AgentNodeModel) : {};
}

function getRetrievalNodeModel(node: WorkflowNode | null): RetrievalNodeModel {
  return (node ?? {}) as RetrievalNodeModel;
}

function getAgentNodeTools(node: WorkflowNode | null): AgentToolConfig[] {
  const tools = node?.tools;
  if (!Array.isArray(tools)) return [];
  return tools.filter(isRecord).flatMap((tool) => {
    const name = typeof tool.name === "string" ? tool.name.trim() : "";
    if (!name) return [];
    return [
      {
        type: typeof tool.type === "string" && tool.type.trim() ? tool.type.trim() : "builtin",
        name,
        enabled: tool.enabled !== false,
        config: isRecord(tool.config) ? tool.config : {},
      },
    ];
  });
}

function getEnabledAgentToolNames(node: WorkflowNode | null): string[] {
  return getAgentNodeTools(node)
    .filter((tool) => tool.type === "builtin" && tool.enabled)
    .map((tool) => tool.name);
}

function buildMcpToolKey(serverId: string, toolName: string) {
  return `${serverId}::${toolName}`;
}

function getEnabledAgentMcpToolKeys(node: WorkflowNode | null): string[] {
  return getAgentNodeTools(node)
    .filter((tool) => tool.type === "mcp" && tool.enabled)
    .map((tool) => buildMcpToolKey(typeof tool.config.server_id === "string" ? tool.config.server_id : "", tool.name));
}

function getOrphanedAgentMcpTools(
  node: WorkflowNode | null,
  externalServers: ExternalMcpServerItem[],
): OrphanedMcpToolReference[] {
  const knownServerIds = new Set(externalServers.map((server) => server.id));
  const seen = new Set<string>();
  return getAgentNodeTools(node).flatMap((tool) => {
    if (tool.type !== "mcp") return [];
    const serverId = typeof tool.config.server_id === "string" ? tool.config.server_id.trim() : "";
    if (!serverId || knownServerIds.has(serverId)) return [];
    const key = buildMcpToolKey(serverId, tool.name);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ server_id: serverId, name: tool.name }];
  });
}

function getWorkflowOrphanedMcpTools(
  workflow: WorkflowItem | null,
  externalServers: ExternalMcpServerItem[],
): OrphanedMcpToolReference[] {
  if (!workflow) return [];
  const seen = new Set<string>();
  return getWorkflowNodes(workflow).flatMap((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    return getOrphanedAgentMcpTools(node, externalServers).flatMap((tool) => {
      const key = `${nodeId}::${buildMcpToolKey(tool.server_id, tool.name)}`;
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ ...tool, node_id: nodeId }];
    });
  });
}

function getExternalServerTools(server: ExternalMcpServerItem | null): Array<{ name: string; description: string }> {
  if (!server || !isRecord(server.tool_manifest_json)) return [];
  const tools = server.tool_manifest_json.tools;
  if (!Array.isArray(tools)) return [];
  return tools.flatMap((tool) => {
    if (!isRecord(tool)) return [];
    const name = typeof tool.name === "string" ? tool.name.trim() : "";
    if (!name) return [];
    return [{ name, description: typeof tool.description === "string" ? tool.description : "" }];
  });
}

function getWorkflowMcpEndpoint(serverSlug: string) {
  if (typeof window === "undefined") {
    return `http://localhost:8000/mcp/${serverSlug}`;
  }
  return `${window.location.protocol}//${window.location.hostname}:8000/mcp/${serverSlug}`;
}

function getMcpSlugFromServerUrl(serverUrl: string) {
  const value = serverUrl.trim();
  if (!value) return "";
  try {
    const pathname = new URL(value).pathname.replace(/\/+$/, "");
    const match = pathname.match(/\/mcp\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]).trim().toLowerCase() : "";
  } catch {
    const pathname = value.split("?")[0].split("#")[0].replace(/\/+$/, "");
    const match = pathname.match(/\/mcp\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]).trim().toLowerCase() : "";
  }
}

function buildDefaultWorkflowMcpServerDraft(workflow: WorkflowItem, existing: WorkflowMcpServerItem | null): WorkflowMcpServerDraft {
  if (existing) {
    return {
      configured: true,
      enabled: existing.enabled,
      server_name: existing.server_name,
      server_slug: existing.server_slug,
      description: existing.description,
      auth_type: existing.auth_type,
    };
  }
  const workflowName = workflow.name.trim() || "Workflow";
  return {
    configured: false,
    enabled: true,
    server_name: workflowName,
    server_slug: slugifyMcpServerName(workflowName) || "workflow",
    description: workflow.description || `运行已发布的 ${workflowName}`,
    auth_type: "bearer",
  };
}

function emptyExternalMcpServerDraft(): ExternalMcpServerDraft {
  return {
    name: "",
    description: "",
    transport_type: "streamable_http",
    server_url: "",
    auth_type: "none",
    auth_secret: "",
    custom_headers: [],
    custom_headers_dirty: false,
  };
}

function updateAgentNodeTools(workflow: WorkflowItem, agentNodeId: string, toolNames: string[]): WorkflowItem {
  const nodes = getWorkflowNodes(workflow);
  const targetNode = nodes.find((node, index) => getWorkflowNodeId(node, index) === agentNodeId) ?? null;
  const existingTools = getAgentNodeTools(targetNode);
  const uniqueToolNames = Array.from(new Set(toolNames));
  const nextBuiltinTools = uniqueToolNames.map((name) => {
    const existingTool = existingTools.find((tool) => tool.type === "builtin" && tool.name === name);
    return {
      type: "builtin",
      name,
      enabled: true,
      config: existingTool?.config ?? {},
    };
  });
  const nextMcpTools = existingTools.filter((tool) => tool.type === "mcp");
  const nextNodes = nodes.map((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    if (nodeId !== agentNodeId || !isAgentNode(node)) return node;
    return { ...node, tools: [...nextBuiltinTools, ...nextMcpTools] };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function updateAgentNodeMcpTool(
  workflow: WorkflowItem,
  agentNodeId: string,
  serverId: string,
  toolName: string,
  enabled: boolean,
): WorkflowItem {
  const nodes = getWorkflowNodes(workflow);
  const targetNode = nodes.find((node, index) => getWorkflowNodeId(node, index) === agentNodeId) ?? null;
  const existingTools = getAgentNodeTools(targetNode);
  const nextBuiltinTools = existingTools.filter((tool) => tool.type === "builtin");
  const remainingMcpTools = existingTools.filter(
    (tool) => !(tool.type === "mcp" && String(tool.config.server_id ?? "") === serverId && tool.name === toolName),
  );
  const nextMcpTools = enabled
    ? [...remainingMcpTools, { type: "mcp", name: toolName, enabled: true, config: { server_id: serverId } }]
    : remainingMcpTools;
  const nextNodes = nodes.map((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    if (nodeId !== agentNodeId || !isAgentNode(node)) return node;
    return { ...node, tools: [...nextBuiltinTools, ...nextMcpTools] };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function updateAgentNodeModel(workflow: WorkflowItem, agentNodeId: string, key: keyof AgentNodeModel, value: string | number): WorkflowItem {
  const nextNodes = getWorkflowNodes(workflow).map((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    if (nodeId !== agentNodeId || !isAgentNode(node)) return node;
    const model = isRecord(node.model) ? node.model : {};
    return { ...node, model: { ...model, [key]: value } };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function updateRetrievalNode(
  workflow: WorkflowItem,
  retrievalNodeId: string,
  key: keyof RetrievalNodeModel,
  value: string | number | boolean | string[],
): WorkflowItem {
  const nextNodes = getWorkflowNodes(workflow).map((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    if (nodeId !== retrievalNodeId || !isRetrievalNode(node)) return node;
    return { ...node, id: node.id ?? "retrieval", type: "retrieval", [key]: value };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function pruneRetrievalKnowledgeBaseIds(workflow: WorkflowItem, knowledgeBases: KnowledgeBase[]): WorkflowItem {
  const validIds = new Set(knowledgeBases.map((item) => item.id));
  const nextNodes = getWorkflowNodes(workflow).map((node) => {
    if (!isRetrievalNode(node)) return node;
    const ids = Array.isArray(node.knowledge_base_ids) ? node.knowledge_base_ids : [];
    return {
      ...node,
      id: node.id ?? "retrieval",
      type: "retrieval",
      knowledge_base_ids: ids.map((id) => String(id)).filter((id) => validIds.has(id)),
    };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function pruneOrphanedMcpTools(workflow: WorkflowItem, externalServers: ExternalMcpServerItem[]): WorkflowItem {
  const knownServerIds = new Set(externalServers.map((server) => server.id));
  const nextNodes = getWorkflowNodes(workflow).map((node) => {
    const tools = node.tools;
    if (!Array.isArray(tools)) return node;
    const nextTools = tools.filter((tool) => {
      if (!isRecord(tool) || tool.type !== "mcp") return true;
      const config = isRecord(tool.config) ? tool.config : {};
      const serverId = typeof config.server_id === "string" ? config.server_id.trim() : "";
      return Boolean(serverId && knownServerIds.has(serverId));
    });
    return nextTools.length === tools.length ? node : { ...node, tools: nextTools };
  });
  return { ...workflow, draft_spec: { ...(workflow.draft_spec ?? {}), nodes: nextNodes } };
}

function App() {
  const [activeView, setActiveView] = useState<NavView>("workspace");
  const [activeAppView, setActiveAppView] = useState<AppView>("studio");
  const [user, setUser] = useState<UserItem | null>(null);
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [apps, setApps] = useState<AppItem[]>([]);
  const [selectedApp, setSelectedApp] = useState<AppItem | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowItem | null>(null);
  const [workflowVersions, setWorkflowVersions] = useState<WorkflowVersionItem[]>([]);
  const [workflowMcpServer, setWorkflowMcpServer] = useState<WorkflowMcpServerItem | null>(null);
  const [workflowMcpServersByWorkflowId, setWorkflowMcpServersByWorkflowId] = useState<Record<string, WorkflowMcpServerItem>>({});
  const [workflowMcpDraft, setWorkflowMcpDraft] = useState<WorkflowMcpServerDraft | null>(null);
  const [workflowMcpToken, setWorkflowMcpToken] = useState("");
  const [draftSpecText, setDraftSpecText] = useState("");
  const [draftSpecError, setDraftSpecError] = useState("");
  const [credentials, setCredentials] = useState<ModelCredential[]>([]);
  const [credentialDraft, setCredentialDraft] = useState({ provider: "openai_compatible", name: "", api_key: "" });
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState("");
  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocument[]>([]);
  const [knowledgeDraft, setKnowledgeDraft] = useState({ name: "", description: "" });
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [externalMcpServers, setExternalMcpServers] = useState<ExternalMcpServerItem[]>([]);
  const [selectedExternalMcpServerId, setSelectedExternalMcpServerId] = useState("");
  const [syncingExternalMcpServerId, setSyncingExternalMcpServerId] = useState("");
  const [externalMcpDraft, setExternalMcpDraft] = useState<ExternalMcpServerDraft>(emptyExternalMcpServerDraft);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [runSteps, setRunSteps] = useState<RunStepItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const [selectedWorkflowNodeId, setSelectedWorkflowNodeId] = useState("");
  const [input, setInput] = useState("我的订单10086到哪了？");
  const [trace, setTrace] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const selectedOwnedApp = selectedApp?.owner_user_id === user?.id ? selectedApp : null;
  const selectedWorkflowId = selectedWorkflow?.id ?? "";
  const selectedWorkflowPublished = Boolean(selectedWorkflow?.published_version_id);
  const selectedPublishedVersion = useMemo(
    () => workflowVersions.find((version) => version.id === selectedWorkflow?.published_version_id) ?? null,
    [selectedWorkflow?.published_version_id, workflowVersions],
  );
  const selectedWorkflowHasUnpublishedChanges = Boolean(
    selectedWorkflow &&
      selectedPublishedVersion &&
      stableStringify(selectedWorkflow.draft_spec ?? {}) !== stableStringify(selectedPublishedVersion.spec_json ?? {}),
  );
  const selectedKnowledgeBase = knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId) ?? null;
  const currentAppWorkflowMcpSlugs = useMemo(() => {
    const slugs = new Set<string>();
    workflows.forEach((workflow) => {
      const server = workflowMcpServersByWorkflowId[workflow.id];
      if (server?.server_slug) {
        slugs.add(server.server_slug.trim().toLowerCase());
      }
    });
    if (workflowMcpServer?.server_slug) {
      slugs.add(workflowMcpServer.server_slug.trim().toLowerCase());
    }
    return slugs;
  }, [workflowMcpServer?.server_slug, workflowMcpServersByWorkflowId, workflows]);
  const visibleExternalMcpServers = useMemo(
    () => externalMcpServers.filter((server) => !currentAppWorkflowMcpSlugs.has(getMcpSlugFromServerUrl(server.server_url))),
    [currentAppWorkflowMcpSlugs, externalMcpServers],
  );
  const hiddenSelfExternalMcpServerCount = externalMcpServers.length - visibleExternalMcpServers.length;
  const selectedExternalMcpServer = visibleExternalMcpServers.find((item) => item.id === selectedExternalMcpServerId) ?? null;
  const canEditSelectedApp = Boolean(selectedOwnedApp);
  const canEditSelectedWorkflow = Boolean(selectedOwnedApp && selectedWorkflow);
  const workflowNodes = useMemo(() => (selectedWorkflow ? getWorkflowNodes(selectedWorkflow) : []), [selectedWorkflow]);
  const workflowEdges = useMemo(() => (selectedWorkflow ? getWorkflowEdges(selectedWorkflow) : []), [selectedWorkflow]);
  const selectedWorkflowOrphanedMcpTools = useMemo(
    () => getWorkflowOrphanedMcpTools(selectedWorkflow, externalMcpServers),
    [externalMcpServers, selectedWorkflow],
  );
  const selectedWorkflowNode = useMemo(() => {
    const node = workflowNodes.find((item, index) => getWorkflowNodeId(item, index) === selectedWorkflowNodeId);
    return node ?? workflowNodes[0] ?? null;
  }, [selectedWorkflowNodeId, workflowNodes]);
  const selectedWorkflowNodeIndex = selectedWorkflowNode ? workflowNodes.indexOf(selectedWorkflowNode) : -1;
  const selectedWorkflowNodeType = selectedWorkflowNode ? getWorkflowNodeType(selectedWorkflowNode) : "";
  const selectedWorkflowNodeIsAgent = Boolean(selectedWorkflowNode && isAgentNode(selectedWorkflowNode));
  const selectedWorkflowNodeIsRetrieval = Boolean(selectedWorkflowNode && isRetrievalNode(selectedWorkflowNode));
  const selectedWorkflowNodeKey = selectedWorkflowNode
    ? getWorkflowNodeId(selectedWorkflowNode, Math.max(selectedWorkflowNodeIndex, 0))
    : "";
  const agentNodeModel = useMemo(() => getAgentNodeModel(selectedWorkflowNode), [selectedWorkflowNode]);
  const retrievalNodeModel = useMemo(() => getRetrievalNodeModel(selectedWorkflowNode), [selectedWorkflowNode]);
  const enabledAgentToolNames = useMemo(() => getEnabledAgentToolNames(selectedWorkflowNode), [selectedWorkflowNode]);
  const enabledAgentMcpToolKeys = useMemo(() => getEnabledAgentMcpToolKeys(selectedWorkflowNode), [selectedWorkflowNode]);
  const orphanedAgentMcpTools = useMemo(
    () => getOrphanedAgentMcpTools(selectedWorkflowNode, externalMcpServers),
    [externalMcpServers, selectedWorkflowNode],
  );

  function setActiveConversationId(nextConversationId: string | null) {
    conversationIdRef.current = nextConversationId;
    setConversationId(nextConversationId);
  }

  function setNotice(message: string) {
    setStatusMessage(message);
  }

  function resetWorkspace() {
    setApps([]);
    setWorkflows([]);
    setSelectedApp(null);
    setSelectedWorkflow(null);
    setWorkflowVersions([]);
    setWorkflowMcpServer(null);
    setWorkflowMcpServersByWorkflowId({});
    setWorkflowMcpDraft(null);
    setWorkflowMcpToken("");
    setDraftSpecText("");
    setDraftSpecError("");
    setCredentials([]);
    setKnowledgeBases([]);
    setSelectedKnowledgeBaseId("");
    setKnowledgeDocuments([]);
    setTools([]);
    setExternalMcpServers([]);
    setSelectedExternalMcpServerId("");
    setSyncingExternalMcpServerId("");
    setExternalMcpDraft(emptyExternalMcpServerDraft());
    setRuns([]);
    setRunSteps([]);
    setSelectedRunId("");
    setMessages([]);
    setTrace([]);
    setActiveConversationId(null);
    setSelectedWorkflowNodeId("");
    setBusy(false);
  }

  async function selectRun(run: RunItem | null) {
    setSelectedRunId(run?.id ?? "");
    if (!run) {
      setRunSteps([]);
      return;
    }
    try {
      setRunSteps(await api.listRunSteps(run.id));
    } catch (error) {
      console.warn(error);
      setRunSteps([]);
    }
  }

  async function selectWorkflow(workflow: WorkflowItem | null, preloadedMcpServer?: WorkflowMcpServerItem | null) {
    setSelectedWorkflow(workflow);
    setWorkflowVersions([]);
    setWorkflowMcpServer(null);
    setWorkflowMcpDraft(workflow ? buildDefaultWorkflowMcpServerDraft(workflow, null) : null);
    setWorkflowMcpToken("");
    setDraftSpecText("");
    setDraftSpecError("");
    setRuns([]);
    setRunSteps([]);
    setSelectedRunId("");
    setMessages([]);
    setTrace([]);
    setActiveConversationId(null);
    setSelectedWorkflowNodeId("");
    if (!workflow) return;

    const [versionList, runList, mcpServer] = await Promise.all([
      api.listWorkflowVersions(workflow.id),
      api.listWorkflowRuns(workflow.id),
      preloadedMcpServer !== undefined ? Promise.resolve(preloadedMcpServer) : api.getWorkflowMcpServer(workflow.id).catch(() => null),
    ]);
    setWorkflowVersions(versionList);
    setWorkflowMcpServer(mcpServer);
    if (mcpServer) {
      setWorkflowMcpServersByWorkflowId((current) => ({ ...current, [workflow.id]: mcpServer }));
    }
    setWorkflowMcpDraft(buildDefaultWorkflowMcpServerDraft(workflow, mcpServer));
    setRuns(runList);

    const latestRun = runList[0] ?? null;
    await selectRun(latestRun);
    const latestConversationId = latestRun?.conversation_id ?? null;
    setActiveConversationId(latestConversationId);
    if (!latestConversationId) return;
    try {
      const history = await api.listMessages(latestConversationId);
      setMessages(
        history
          .filter((message) => message.role === "user" || message.role === "assistant" || message.role === "system")
          .map((message) => ({ role: message.role, content: message.content })),
      );
    } catch (error) {
      console.warn(error);
      setActiveConversationId(null);
      setMessages([]);
    }
  }

  async function selectApp(app: AppItem | null, preferredWorkflowId?: string | null) {
    setSelectedApp(app);
    setWorkflows([]);
    setSelectedWorkflow(null);
    setWorkflowVersions([]);
    setWorkflowMcpServer(null);
    setWorkflowMcpServersByWorkflowId({});
    setWorkflowMcpDraft(null);
    setWorkflowMcpToken("");
    setMessages([]);
    setTrace([]);
    setActiveConversationId(null);
    setStatusMessage("");
    if (!app) {
      setRuns([]);
      setRunSteps([]);
      setSelectedRunId("");
      setSelectedWorkflowNodeId("");
      return;
    }

    const workflowList = await api.listWorkflows(app.id);
    setWorkflows(workflowList);
    const mcpServerEntries = await Promise.all(
      workflowList.map(async (workflow) => [workflow.id, await api.getWorkflowMcpServer(workflow.id).catch(() => null)] as const),
    );
    const nextWorkflowMcpServersByWorkflowId = Object.fromEntries(
      mcpServerEntries.flatMap(([workflowId, server]) => (server ? [[workflowId, server]] : [])),
    ) as Record<string, WorkflowMcpServerItem>;
    setWorkflowMcpServersByWorkflowId(nextWorkflowMcpServersByWorkflowId);
    const currentWorkflowId = selectedWorkflow?.app_id === app.id ? selectedWorkflow.id : null;
    const workflow =
      workflowList.find((item) => item.id === preferredWorkflowId) ??
      workflowList.find((item) => item.id === currentWorkflowId) ??
      workflowList[0] ??
      null;
    await selectWorkflow(workflow, workflow ? nextWorkflowMcpServersByWorkflowId[workflow.id] ?? null : undefined);
  }

  async function openApp(app: AppItem) {
    setActiveView("workspace");
    setActiveAppView("studio");
    await selectApp(app);
  }

  async function returnToWorkspaceHome() {
    setActiveView("workspace");
    await selectApp(null);
  }

  async function refresh(preferredAppId?: string | null, preferredWorkflowId?: string | null) {
    if (!user) return;
    const [appList, toolList, credentialList, kbList, mcpServerList] = await Promise.all([
      api.listApps(),
      api.listTools(),
      api.listModelCredentials(),
      api.listKnowledgeBases(),
      api.listExternalMcpServers(),
    ]);
    setApps(appList);
    setTools(toolList);
    setCredentials(credentialList);
    setKnowledgeBases(kbList);
    setExternalMcpServers(mcpServerList);
    if (!selectedKnowledgeBaseId && kbList[0]) {
      setSelectedKnowledgeBaseId(kbList[0].id);
    }

    const currentId = selectedApp?.id ?? null;
    const app =
      appList.find((item) => item.id === preferredAppId) ??
      appList.find((item) => item.id === currentId) ??
      appList[0] ??
      null;
    await selectApp(app, preferredWorkflowId);
  }

  useEffect(() => {
    let cancelled = false;
    async function loadSession() {
      if (!api.getAuthToken()) {
        setAuthLoading(false);
        return;
      }
      try {
        const currentUser = await api.me();
        if (!cancelled) setUser(currentUser);
      } catch {
        api.setAuthToken("");
      } finally {
        if (!cancelled) setAuthLoading(false);
      }
    }
    loadSession().catch(console.error);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!user) return;
    refresh().catch((error) => {
      console.error(error);
      api.setAuthToken("");
      setUser(null);
      resetWorkspace();
    });
  }, [user?.id]);

  useEffect(() => {
    if (!workflowNodes.length) {
      setSelectedWorkflowNodeId("");
      return;
    }
    const selectedStillExists = workflowNodes.some((node, index) => getWorkflowNodeId(node, index) === selectedWorkflowNodeId);
    if (selectedStillExists) return;
    const defaultNode =
      workflowNodes.find((node) => isRetrievalNode(node) || isAgentNode(node)) ??
      workflowNodes.find((node) => getWorkflowNodeType(node) === "start") ??
      workflowNodes[0];
    setSelectedWorkflowNodeId(getWorkflowNodeId(defaultNode, workflowNodes.indexOf(defaultNode)));
  }, [selectedWorkflowNodeId, workflowNodes]);

  useEffect(() => {
    if (!selectedWorkflow) {
      setDraftSpecText("");
      setDraftSpecError("");
      return;
    }
    setDraftSpecText(JSON.stringify(selectedWorkflow.draft_spec ?? {}, null, 2));
    setDraftSpecError("");
  }, [selectedWorkflow?.id, selectedWorkflow?.draft_spec]);

  useEffect(() => {
    if (selectedExternalMcpServer) {
      setExternalMcpDraft({
        name: selectedExternalMcpServer.name,
        description: selectedExternalMcpServer.description,
        transport_type: selectedExternalMcpServer.transport_type,
        server_url: selectedExternalMcpServer.server_url,
        auth_type: selectedExternalMcpServer.auth_type,
        auth_secret: "",
        custom_headers: (selectedExternalMcpServer.custom_header_names ?? []).map((name) => ({
          name,
          value: "",
          saved: true,
        })),
        custom_headers_dirty: false,
      });
      return;
    }
    setExternalMcpDraft(emptyExternalMcpServerDraft());
  }, [selectedExternalMcpServerId, selectedExternalMcpServer?.id]);

  useEffect(() => {
    if (selectedExternalMcpServerId === NEW_EXTERNAL_MCP_SERVER_ID) return;
    if (selectedExternalMcpServerId && visibleExternalMcpServers.some((server) => server.id === selectedExternalMcpServerId)) return;
    setSelectedExternalMcpServerId(visibleExternalMcpServers[0]?.id ?? "");
  }, [selectedExternalMcpServerId, visibleExternalMcpServers]);

  useEffect(() => {
    if (!canEditSelectedApp || !selectedKnowledgeBaseId) {
      setKnowledgeDocuments([]);
      return;
    }
    api.listKnowledgeDocuments(selectedKnowledgeBaseId).then(setKnowledgeDocuments).catch((error) => {
      console.error(error);
      setKnowledgeDocuments([]);
    });
  }, [canEditSelectedApp, selectedKnowledgeBaseId]);

  useEffect(() => {
    if (!canEditSelectedApp || !selectedKnowledgeBaseId) return;
    const hasProcessingDocument = knowledgeDocuments.some((document) =>
      PROCESSING_DOCUMENT_STATUSES.has(String(document.status || "").toLowerCase()),
    );
    if (!hasProcessingDocument) return;

    let cancelled = false;
    const timer = window.setInterval(() => {
      api
        .listKnowledgeDocuments(selectedKnowledgeBaseId)
        .then((documents) => {
          if (!cancelled) setKnowledgeDocuments(documents);
        })
        .catch(console.error);
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [canEditSelectedApp, knowledgeDocuments, selectedKnowledgeBaseId]);

  useEffect(() => {
    if (!selectedOwnedApp) return;
    setCredentialDraft((draft) => ({ ...draft, provider: defaultCredentialProvider(selectedOwnedApp.model_provider) }));
  }, [selectedOwnedApp?.id]);

  async function submitAuth(action: AuthAction) {
    const email = authForm.email.trim();
    const password = authForm.password.trim();
    if (!email || !password) {
      setAuthError("请输入邮箱和密码。");
      return;
    }
    setAuthBusy(true);
    setAuthError("");
    try {
      const response = action === "login" ? await api.login({ email, password }) : await api.register({ email, password });
      api.setAuthToken(response.token);
      setUser(response.user);
      setAuthForm({ email: response.user.email, password: "" });
    } catch (error) {
      api.setAuthToken("");
      setAuthError(error instanceof Error ? error.message : String(error));
    } finally {
      setAuthBusy(false);
    }
  }

  function logout() {
    api.setAuthToken("");
    setUser(null);
    setAuthError("");
    resetWorkspace();
  }

  async function createDemoApp() {
    try {
      setBusy(true);
      const app = await api.createApp();
      await refresh(app.id);
      setActiveView("workspace");
      setActiveAppView("studio");
      setNotice("应用已创建。");
    } catch (error) {
      setNotice(`创建应用失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteApp(app: AppItem, event?: React.MouseEvent<HTMLButtonElement>) {
    event?.stopPropagation();
    const confirmed = window.confirm(`确定删除应用「${app.name}」吗？该应用下的 Workflow、发布版本、会话和运行记录都会一起删除。`);
    if (!confirmed) return;
    try {
      setBusy(true);
      await api.deleteApp(app.id);
      if (selectedApp?.id === app.id) {
        await selectApp(null);
      }
      const [appList, toolList, credentialList, kbList, mcpServerList] = await Promise.all([
        api.listApps(),
        api.listTools(),
        api.listModelCredentials(),
        api.listKnowledgeBases(),
        api.listExternalMcpServers(),
      ]);
      setApps(appList);
      setTools(toolList);
      setCredentials(credentialList);
      setKnowledgeBases(kbList);
      setExternalMcpServers(mcpServerList);
      setActiveView("workspace");
      setNotice("应用已删除。");
    } catch (error) {
      setNotice(`删除应用失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function createWorkflow() {
    if (!selectedOwnedApp) return;
    try {
      const workflow = await api.createWorkflow(selectedOwnedApp.id, {
        name: `Workflow ${workflows.length + 1}`,
        description: "",
      });
      await refresh(selectedOwnedApp.id, workflow.id);
      setNotice("Workflow 已创建。");
    } catch (error) {
      setNotice(`创建 Workflow 失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function saveWorkflowMcpServerConfig() {
    if (!selectedWorkflow || !workflowMcpDraft) return;
    const serverName = workflowMcpDraft.server_name.trim();
    const serverSlug = slugifyMcpServerName(workflowMcpDraft.server_slug);
    if (!serverName || !serverSlug) {
      setNotice("MCP Server 名称和 slug 都不能为空。");
      return;
    }
    try {
      const saved = await api.upsertWorkflowMcpServer(selectedWorkflow.id, {
        enabled: workflowMcpDraft.enabled,
        server_name: serverName,
        server_slug: serverSlug,
        description: workflowMcpDraft.description.trim(),
      });
      applyWorkflowMcpProvision(saved);
      setNotice(saved.token ? "Workflow MCP Server 已创建，请立即保存这次返回的 token。" : "Workflow MCP Server 已更新。");
    } catch (error) {
      setNotice(`保存 Workflow MCP Server 失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function rotateWorkflowMcpServerAccessToken() {
    if (!selectedWorkflow) return;
    try {
      const saved = await api.rotateWorkflowMcpServerToken(selectedWorkflow.id);
      applyWorkflowMcpProvision(saved);
      setNotice("访问 token 已轮换，旧 token 已失效。");
    } catch (error) {
      setNotice(`轮换 token 失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function applyWorkflowMcpProvision(saved: WorkflowMcpServerProvisionItem) {
    const normalized: WorkflowMcpServerItem = {
      id: saved.id,
      workflow_id: saved.workflow_id,
      enabled: saved.enabled,
      server_name: saved.server_name,
      server_slug: saved.server_slug,
      description: saved.description,
      auth_type: saved.auth_type,
      created_at: saved.created_at,
      updated_at: saved.updated_at,
    };
    setWorkflowMcpServer(normalized);
    setWorkflowMcpServersByWorkflowId((current) => ({ ...current, [saved.workflow_id]: normalized }));
    setWorkflowMcpDraft(buildDefaultWorkflowMcpServerDraft(selectedWorkflow as WorkflowItem, normalized));
    setWorkflowMcpToken(saved.token ?? "");
  }

  async function persistCurrentConfig() {
    if (!selectedOwnedApp) return null;
    if (draftSpecError) throw new Error("draft_spec JSON 不是合法 JSON。");
    const updatedApp = await api.updateApp(selectedOwnedApp.id, {
      name: selectedOwnedApp.name,
      description: selectedOwnedApp.description,
      system_prompt: selectedOwnedApp.system_prompt,
      model_provider: selectedOwnedApp.model_provider,
      model_name: selectedOwnedApp.model_name,
      model_credential_id: selectedOwnedApp.model_credential_id,
      model_base_url: selectedOwnedApp.model_base_url,
      temperature: selectedOwnedApp.temperature,
      top_p: selectedOwnedApp.top_p,
      max_tokens: selectedOwnedApp.max_tokens,
    });
    let updatedWorkflow: WorkflowItem | null = null;
    if (selectedWorkflow) {
      const workflowToSave = pruneRetrievalKnowledgeBaseIds(selectedWorkflow, knowledgeBases);
      updatedWorkflow = await api.updateWorkflow(workflowToSave.id, {
        name: workflowToSave.name,
        description: workflowToSave.description,
        draft_spec: workflowToSave.draft_spec,
      });
    }
    return { app: updatedApp, workflow: updatedWorkflow };
  }

  async function saveConfig() {
    if (!selectedOwnedApp) return;
    try {
      setBusy(true);
      const saved = await persistCurrentConfig();
      if (!saved) return;
      await refresh(saved.app.id, saved.workflow?.id ?? selectedWorkflow?.id);
      setNotice("草稿配置已保存。");
    } catch (error) {
      setNotice(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function publishSelectedWorkflow() {
    if (!selectedOwnedApp || !selectedWorkflow) return;
    try {
      setBusy(true);
      const saved = await persistCurrentConfig();
      const workflowId = saved?.workflow?.id ?? selectedWorkflow.id;
      await api.publishWorkflow(workflowId);
      await refresh(selectedOwnedApp.id, workflowId);
      setNotice("Workflow 已发布，聊天和 MCP 将运行新的发布版本。");
    } catch (error) {
      setNotice(`发布失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelectedWorkflow() {
    if (!selectedOwnedApp || !selectedWorkflow) return;
    const confirmed = window.confirm(`确定删除 Workflow「${selectedWorkflow.name}」吗？发布版本和运行记录也会失去这个入口。`);
    if (!confirmed) return;
    try {
      setBusy(true);
      await api.deleteWorkflow(selectedWorkflow.id);
      await refresh(selectedOwnedApp.id);
      setNotice("Workflow 已删除。");
    } catch (error) {
      setNotice(`删除 Workflow 失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function createKnowledgeBase() {
    const name = knowledgeDraft.name.trim();
    if (!name) return;
    setBusy(true);
    try {
      const kb = await api.createKnowledgeBase({ name, description: knowledgeDraft.description.trim() });
      const list = await api.listKnowledgeBases();
      setKnowledgeDraft({ name: "", description: "" });
      setKnowledgeBases(list);
      setSelectedKnowledgeBaseId(kb.id);
      setNotice("知识库已创建。");
    } catch (error) {
      setNotice(`创建知识库失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function uploadKnowledgeFile(file: File | null) {
    if (!selectedKnowledgeBaseId || !file) return;
    setBusy(true);
    try {
      await api.uploadKnowledgeDocument(selectedKnowledgeBaseId, file);
      setKnowledgeDocuments(await api.listKnowledgeDocuments(selectedKnowledgeBaseId));
      setNotice("文档已上传，后台会继续处理索引。");
    } catch (error) {
      setNotice(`上传文档失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteKnowledgeDocument(documentId: string) {
    if (!selectedKnowledgeBaseId) return;
    try {
      await api.deleteKnowledgeDocument(selectedKnowledgeBaseId, documentId);
      setKnowledgeDocuments(await api.listKnowledgeDocuments(selectedKnowledgeBaseId));
      setNotice("文档已删除。");
    } catch (error) {
      setNotice(`删除文档失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function deleteSelectedKnowledgeBase() {
    if (!selectedKnowledgeBase) return;
    try {
      await api.deleteKnowledgeBase(selectedKnowledgeBase.id);
      const list = await api.listKnowledgeBases();
      setKnowledgeBases(list);
      setSelectedKnowledgeBaseId(list[0]?.id ?? "");
      if (selectedApp) await refresh(selectedApp.id, selectedWorkflow?.id);
      setNotice("知识库已删除。");
    } catch (error) {
      setNotice(`删除知识库失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function rebuildSelectedKnowledgeBase() {
    if (!selectedKnowledgeBaseId) return;
    try {
      await api.rebuildKnowledgeBase(selectedKnowledgeBaseId);
      setKnowledgeDocuments(await api.listKnowledgeDocuments(selectedKnowledgeBaseId));
      setNotice("知识库已开始重建。");
    } catch (error) {
      setNotice(`重建知识库失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function createCredential() {
    const provider = credentialDraft.provider.trim();
    const apiKey = credentialDraft.api_key.trim();
    if (!provider || !apiKey) return;
    try {
      const name = credentialDraft.name.trim() || `${provider} credential`;
      const credential = await api.createModelCredential({ provider, name, api_key: apiKey });
      setCredentials(await api.listModelCredentials());
      setCredentialDraft({
        provider: defaultCredentialProvider(selectedOwnedApp?.model_provider ?? "openai_compatible"),
        name: "",
        api_key: "",
      });
      setSelectedApp((current) => {
        if (!current) return current;
        return current.model_credential_id ? current : { ...current, model_credential_id: credential.id };
      });
      setNotice("模型凭证已创建。");
    } catch (error) {
      setNotice(`创建凭证失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function deleteCredential(credentialId: string) {
    try {
      await api.deleteModelCredential(credentialId);
      setCredentials(await api.listModelCredentials());
      if (!selectedOwnedApp) return;
      let nextApp = selectedOwnedApp;
      if (selectedOwnedApp.model_credential_id === credentialId) {
        nextApp = { ...nextApp, model_credential_id: "" };
      }
      let nextWorkflow = selectedWorkflow;
      if (selectedWorkflowNodeIsAgent && String(agentNodeModel.credential_id ?? "") === credentialId) {
        nextWorkflow = nextWorkflow ? updateAgentNodeModel(nextWorkflow, selectedWorkflowNodeKey, "credential_id", "") : nextWorkflow;
      }
      if (selectedWorkflowNodeIsRetrieval && String(retrievalNodeModel.query_llm_credential_id ?? "") === credentialId) {
        nextWorkflow = nextWorkflow ? updateRetrievalNode(nextWorkflow, selectedWorkflowNodeKey, "query_llm_credential_id", "") : nextWorkflow;
      }
      setSelectedApp(nextApp);
      setSelectedWorkflow(nextWorkflow);
      setNotice("模型凭证已删除。");
    } catch (error) {
      setNotice(`删除凭证失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function isExternalMcpUrlOwnedByCurrentApp(serverUrl: string) {
    const slug = getMcpSlugFromServerUrl(serverUrl);
    return Boolean(slug && currentAppWorkflowMcpSlugs.has(slug));
  }

  function buildCustomHeadersPayload(headers: ExternalMcpHeaderDraft[]) {
    const next: Record<string, string> = {};
    headers.forEach((header) => {
      const name = header.name.trim();
      const value = header.value;
      if (name && value) {
        next[name] = value;
      }
    });
    return next;
  }

  function updateExternalHeader(index: number, patch: Partial<ExternalMcpHeaderDraft>) {
    setExternalMcpDraft((current) => ({
      ...current,
      custom_headers: current.custom_headers.map((header, itemIndex) =>
        itemIndex === index ? { ...header, ...patch, saved: false } : header,
      ),
      custom_headers_dirty: true,
    }));
  }

  function addExternalHeader() {
    setExternalMcpDraft((current) => ({
      ...current,
      custom_headers: [...current.custom_headers, { name: "", value: "", saved: false }],
      custom_headers_dirty: true,
    }));
  }

  function removeExternalHeader(index: number) {
    setExternalMcpDraft((current) => ({
      ...current,
      custom_headers: current.custom_headers.filter((_, itemIndex) => itemIndex !== index),
      custom_headers_dirty: true,
    }));
  }

  function clearExternalHeaders() {
    setExternalMcpDraft((current) => ({
      ...current,
      custom_headers: [],
      custom_headers_dirty: true,
    }));
  }

  async function saveExternalMcpServer() {
    const payload: {
      name: string;
      description: string;
      transport_type: string;
      server_url: string;
      auth_type: string;
      auth_secret?: string;
      custom_headers?: Record<string, string>;
    } = {
      name: externalMcpDraft.name.trim(),
      description: externalMcpDraft.description.trim(),
      transport_type: externalMcpDraft.transport_type,
      server_url: externalMcpDraft.server_url.trim(),
      auth_type: externalMcpDraft.auth_type,
    };
    const nextAuthSecret = externalMcpDraft.auth_secret.trim();
    if (!selectedExternalMcpServer || nextAuthSecret) {
      payload.auth_secret = nextAuthSecret;
    }
    if (!selectedExternalMcpServer || externalMcpDraft.custom_headers_dirty) {
      payload.custom_headers = buildCustomHeadersPayload(externalMcpDraft.custom_headers);
    }
    if (!payload.name || !payload.server_url) {
      setNotice("外部 MCP Server 名称和 URL 都不能为空。");
      return;
    }
    if (isExternalMcpUrlOwnedByCurrentApp(payload.server_url)) {
      setNotice("这个 URL 指向当前 App 自己暴露的 MCP Server。请在上方 MCP Server 区域管理发布配置，不要作为外部连接注册。");
      return;
    }
    if (payload.auth_type === "bearer" && !payload.auth_secret && !selectedExternalMcpServer?.has_auth_secret) {
      setNotice("Bearer 认证需要填写 token。");
      return;
    }
    try {
      const saved = selectedExternalMcpServer
        ? await api.updateExternalMcpServer(selectedExternalMcpServer.id, payload)
        : await api.createExternalMcpServer(payload);
      const list = await api.listExternalMcpServers();
      setExternalMcpServers(list);
      setSelectedExternalMcpServerId(saved.id);
      setNotice(selectedExternalMcpServer ? "外部 MCP Server 已更新。" : "外部 MCP Server 已创建。");
    } catch (error) {
      setNotice(`保存外部 MCP Server 失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function syncSelectedExternalMcpServer() {
    if (!selectedExternalMcpServer) return;
    const serverId = selectedExternalMcpServer.id;
    setSyncingExternalMcpServerId(serverId);
    setNotice(`正在同步外部 MCP Server「${selectedExternalMcpServer.name}」的 tools...`);
    try {
      const saved = await api.syncExternalMcpServer(serverId);
      const list = await api.listExternalMcpServers();
      setExternalMcpServers(list.map((item) => (item.id === saved.id ? saved : item)));
      setSelectedExternalMcpServerId(saved.id);
      setNotice(`外部 MCP tools 已同步：${getExternalServerTools(saved).length} 个 tools。`);
    } catch (error) {
      try {
        const latest = await api.getExternalMcpServer(serverId);
        setExternalMcpServers((items) => items.map((item) => (item.id === latest.id ? latest : item)));
        setSelectedExternalMcpServerId(latest.id);
      } catch (refreshError) {
        console.warn(refreshError);
      }
      setNotice(`同步外部 MCP tools 失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSyncingExternalMcpServerId("");
    }
  }

  async function deleteSelectedExternalMcpServer() {
    if (!selectedExternalMcpServer) return;
    try {
      await api.deleteExternalMcpServer(selectedExternalMcpServer.id);
      const list = await api.listExternalMcpServers();
      setExternalMcpServers(list);
      setSelectedExternalMcpServerId(list.find((server) => !isExternalMcpUrlOwnedByCurrentApp(server.server_url))?.id ?? "");
      if (selectedApp) {
        await refresh(selectedApp.id, selectedWorkflow?.id);
      }
      setNotice("外部 MCP Server 已删除。");
    } catch (error) {
      setNotice(`删除外部 MCP Server 失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function newExternalMcpServerDraft() {
    setSelectedExternalMcpServerId(NEW_EXTERNAL_MCP_SERVER_ID);
    setExternalMcpDraft(emptyExternalMcpServerDraft());
  }

  function toggleTool(toolName: string) {
    if (!canEditSelectedWorkflow || !selectedWorkflow || !selectedWorkflowNodeIsAgent) return;
    const next = enabledAgentToolNames.includes(toolName)
      ? enabledAgentToolNames.filter((name) => name !== toolName)
      : [...enabledAgentToolNames, toolName];
    setSelectedWorkflow(updateAgentNodeTools(selectedWorkflow, selectedWorkflowNodeKey, next));
  }

  function toggleMcpTool(serverId: string, toolName: string) {
    if (!canEditSelectedWorkflow || !selectedWorkflow || !selectedWorkflowNodeIsAgent) return;
    const toolKey = buildMcpToolKey(serverId, toolName);
    const enabled = !enabledAgentMcpToolKeys.includes(toolKey);
    setSelectedWorkflow(updateAgentNodeMcpTool(selectedWorkflow, selectedWorkflowNodeKey, serverId, toolName, enabled));
  }

  function removeOrphanedMcpTool(serverId: string, toolName: string) {
    if (!canEditSelectedWorkflow || !selectedWorkflow || !selectedWorkflowNodeIsAgent) return;
    setSelectedWorkflow(updateAgentNodeMcpTool(selectedWorkflow, selectedWorkflowNodeKey, serverId, toolName, false));
  }

  function removeAllOrphanedMcpTools() {
    if (!canEditSelectedWorkflow || !selectedWorkflow) return;
    setSelectedWorkflow(pruneOrphanedMcpTools(selectedWorkflow, externalMcpServers));
  }

  function updateAgentConfig(key: keyof AgentNodeModel, value: string | number) {
    if (!selectedWorkflow || !selectedWorkflowNodeIsAgent) return;
    setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, selectedWorkflowNodeKey, key, value));
  }

  function updateRetrievalConfig(key: keyof RetrievalNodeModel, value: string | number | boolean | string[]) {
    if (!selectedWorkflow || !selectedWorkflowNodeIsRetrieval) return;
    setSelectedWorkflow(updateRetrievalNode(selectedWorkflow, selectedWorkflowNodeKey, key, value));
  }

  function toggleKnowledgeBaseInNode(kbId: string) {
    const validIds = new Set(knowledgeBases.map((item) => item.id));
    const currentIds = Array.isArray(retrievalNodeModel.knowledge_base_ids)
      ? retrievalNodeModel.knowledge_base_ids.filter((item) => validIds.has(item))
      : [];
    const nextIds = currentIds.includes(kbId) ? currentIds.filter((item) => item !== kbId) : [...currentIds, kbId];
    updateRetrievalConfig("knowledge_base_ids", nextIds);
  }

  function updateRetrievalQueryLlmProvider(provider: string) {
    if (!selectedWorkflow || !selectedWorkflowNodeIsRetrieval) return;
    let nextWorkflow = updateRetrievalNode(selectedWorkflow, selectedWorkflowNodeKey, "query_llm_provider", provider);
    nextWorkflow = updateRetrievalNode(nextWorkflow, selectedWorkflowNodeKey, "query_llm_base_url", defaultQueryLlmBaseUrl(provider));
    setSelectedWorkflow(nextWorkflow);
  }

  function updateDraftSpecText(value: string) {
    setDraftSpecText(value);
    if (!selectedWorkflow) return;
    try {
      const parsed = JSON.parse(value);
      if (!isRecord(parsed)) throw new Error("draft_spec 必须是 JSON object。");
      setSelectedWorkflow({ ...selectedWorkflow, draft_spec: parsed });
      setDraftSpecError("");
    } catch (error) {
      setDraftSpecError(error instanceof Error ? error.message : String(error));
    }
  }

  async function sendMessage() {
    if (!selectedWorkflow || !selectedWorkflowPublished || !input.trim()) return;
    const query = input.trim();
    setInput("");
    setBusy(true);
    setTrace([]);
    setMessages((items) => [...items, { role: "user", content: query }, { role: "assistant", content: "" }]);

    try {
      await streamChat(selectedWorkflow.id, query, conversationIdRef.current, (event, data) => {
        if (event === "run_started") {
          setActiveConversationId(String(data.conversation_id));
        }
        if (event === "error") {
          const message = String(data.message ?? "运行出错");
          setTrace((items) => [...items, { event, ...data }]);
          setMessages((items) => {
            const next = [...items];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content ? `${last.content}\n\n${message}` : message };
            }
            return next;
          });
        }
        if (event === "message_delta") {
          setMessages((items) => {
            const next = [...items];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + String(data.content ?? "") };
            }
            return next;
          });
        }
        if (event === "retrieval" || event === "tool_call" || event === "final" || event === "workflow_warning") {
          setTrace((items) => [...items, { event, ...data }]);
        }
      });
      const runList = await api.listWorkflowRuns(selectedWorkflow.id);
      setRuns(runList);
      await selectRun(runList[0] ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice(`聊天运行失败：${message}`);
      setMessages((items) => {
        const next = [...items];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, content: last.content ? `${last.content}\n\n${message}` : message };
        }
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  function renderStatusBadge(workflow: WorkflowItem | null, isSelected = false) {
    if (!workflow) return <span className="badge muted">未选择</span>;
    if (isSelected && selectedWorkflowHasUnpublishedChanges) return <span className="badge warning">有未发布改动</span>;
    if (workflow.published_version_id) return <span className="badge success">已发布</span>;
    return <span className="badge muted">仅草稿</span>;
  }

  function renderWorkflowNodeIcon(node: WorkflowNode) {
    if (isRetrievalNode(node)) return <Database size={16} />;
    if (isAgentNode(node)) return <Bot size={16} />;
    const type = getWorkflowNodeType(node);
    if (type === "end") return <CheckCircle2 size={16} />;
    return <Play size={16} />;
  }

  function renderWorkflowNodeSummary(node: WorkflowNode) {
    if (isRetrievalNode(node)) {
      const ids = Array.isArray(node.knowledge_base_ids) ? node.knowledge_base_ids : [];
      return ids.length ? `已选择 ${ids.length} 个知识库` : "未选择知识库";
    }
    if (isAgentNode(node)) {
      const model = getAgentNodeModel(node);
      const toolCount = getAgentNodeTools(node).filter((tool) => tool.enabled).length;
      return `${String(model.model_name ?? selectedOwnedApp?.model_name ?? "继承 App 模型")} · ${toolCount} 个工具`;
    }
    const type = getWorkflowNodeType(node);
    if (type === "start") return "接收用户输入";
    if (type === "end") return "返回最终答案";
    return type;
  }

  function renderEmptyState(title: string, detail: string, action?: React.ReactNode) {
    return (
      <div className="empty-state">
        <Circle size={20} />
        <strong>{title}</strong>
        <span>{detail}</span>
        {action}
      </div>
    );
  }

  function renderWorkspaceHome() {
    return (
      <div className="workspace-home">
        <section className="home-hero">
          <div>
            <span className="eyebrow">Studio</span>
            <h2>工作室</h2>
            <p>在这里管理 App。进入某个 App 后，再配置 Workflow、MCP 和运行日志。</p>
          </div>
          <button className="primary-button" disabled={busy} onClick={createDemoApp}>
            <Plus size={15} /> 创建应用
          </button>
        </section>
        <section className="app-grid-section">
          <div className="workspace-filters">
            <div className="segmented-tabs">
              <button className="active">全部</button>
              <button disabled>Workflow</button>
              <button disabled>Agent</button>
              <button disabled>Chatflow</button>
            </div>
            <div className="search-shell">
              <input placeholder="搜索应用" />
            </div>
          </div>
          <div className="app-card-grid">
            <button className="create-app-card" disabled={busy} onClick={createDemoApp}>
              <Plus size={18} />
              <strong>创建应用</strong>
              <span>创建一个带默认 Workflow 草稿的应用。</span>
            </button>
            {apps.map((app) => (
              <article
                className="app-card"
                key={app.id}
                role="button"
                tabIndex={0}
                onClick={() => openApp(app)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openApp(app);
                  }
                }}
              >
                <span className="app-card-icon">
                  <Bot size={20} />
                </span>
                <span className="app-card-main">
                  <strong>{app.name}</strong>
                  <small>{app.description || `App ${shortId(app.id)}`}</small>
                </span>
                <span className="app-card-meta">
                  <small>{formatTimestamp(app.updated_at)}</small>
                  <span className="app-card-actions">
                    {app.owner_user_id === user?.id ? (
                      <button className="icon-button danger" title="删除应用" aria-label="删除应用" disabled={busy} onClick={(event) => deleteApp(app, event)}>
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                    <ChevronRight size={16} />
                  </span>
                </span>
              </article>
            ))}
          </div>
          {!apps.length ? renderEmptyState("还没有应用", "创建应用后会自动生成默认 Workflow 草稿。") : null}
        </section>
      </div>
    );
  }

  function renderWorkflowListPanel() {
    return (
      <section className="work-panel">
        <div className="panel-heading compact-heading">
          <div>
            <span className="eyebrow">Workflows</span>
            <h2>流程版本</h2>
          </div>
          <button
            className="icon-button"
            title="新建 Workflow"
            aria-label="新建 Workflow"
            disabled={!canEditSelectedApp}
            onClick={createWorkflow}
          >
            <Plus size={16} />
          </button>
        </div>
        <div className="stack-list">
          {workflows.map((workflow) => {
            const isSelected = selectedWorkflowId === workflow.id;
            return (
              <button
                className={isSelected ? "list-card active" : "list-card"}
                key={workflow.id}
                onClick={() => selectWorkflow(workflow)}
              >
                <span className="list-card-main">
                  <strong>{workflow.name}</strong>
                  <small>{workflow.description || `Workflow ${shortId(workflow.id)}`}</small>
                </span>
                {renderStatusBadge(workflow, isSelected)}
              </button>
            );
          })}
          {selectedApp && !workflows.length ? renderEmptyState("暂无 Workflow", "可以为当前 App 新建一个流程草稿。") : null}
        </div>
        {selectedWorkflow ? (
          <div className="workflow-raw-spec">
            <div className="raw-spec-heading">
              <span>
                <span className="eyebrow">Raw Spec</span>
                <strong>draft_spec JSON</strong>
              </span>
              <Code2 size={15} />
            </div>
            <textarea
              className="json-editor compact-json-editor"
              rows={16}
              spellCheck={false}
              readOnly={!canEditSelectedWorkflow}
              value={draftSpecText}
              onChange={(event) => updateDraftSpecText(event.target.value)}
            />
            {draftSpecError ? <div className="inline-alert error">{draftSpecError}</div> : null}
          </div>
        ) : null}
        {selectedWorkflow ? (
          <div className="workflow-manage-bar">
            <span>
              当前选中 <strong>{selectedWorkflow.name}</strong>
            </span>
            <button className="ghost-danger-button" disabled={!canEditSelectedWorkflow || busy} onClick={deleteSelectedWorkflow}>
              <Trash2 size={15} /> 删除 Workflow
            </button>
          </div>
        ) : null}
      </section>
    );
  }

  function renderAppSettingsPanel() {
    if (!selectedOwnedApp) return null;
    return (
      <section className="work-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">App Settings</span>
            <h2>应用默认设置</h2>
          </div>
        </div>
        <div className="form-grid">
          <label>
            应用名称
            <input value={selectedOwnedApp.name} onChange={(event) => setSelectedApp({ ...selectedOwnedApp, name: event.target.value })} />
          </label>
          <label>
            描述
            <textarea
              rows={2}
              value={selectedOwnedApp.description}
              onChange={(event) => setSelectedApp({ ...selectedOwnedApp, description: event.target.value })}
            />
          </label>
          <label>
            System Prompt
            <textarea
              rows={4}
              value={selectedOwnedApp.system_prompt}
              onChange={(event) => setSelectedApp({ ...selectedOwnedApp, system_prompt: event.target.value })}
            />
          </label>
        </div>
      </section>
    );
  }

  function renderWorkflowCanvas() {
    if (!selectedWorkflow) {
      return (
        <section className="work-panel canvas-panel">
          {renderEmptyState("请选择 Workflow", "左侧选择或创建 Workflow 后，可以配置节点和发布版本。")}
        </section>
      );
    }

    return (
      <section className="work-panel canvas-panel">
        <div className="panel-heading canvas-heading">
          <div>
            <span className="eyebrow">Workflow Graph</span>
            <h2>{selectedWorkflow.name}</h2>
          </div>
          <div className="heading-actions">
            {selectedWorkflow ? renderStatusBadge(selectedWorkflow, true) : null}
            <button className="secondary-button" disabled={busy || !canEditSelectedWorkflow || Boolean(draftSpecError)} onClick={saveConfig}>
              <Save size={15} /> 保存草稿
            </button>
            <button className="primary-button" disabled={busy || !canEditSelectedWorkflow || Boolean(draftSpecError)} onClick={publishSelectedWorkflow}>
              <Play size={15} /> 发布
            </button>
          </div>
        </div>
        {selectedWorkflowHasUnpublishedChanges ? (
          <div className="inline-alert warning">
            当前草稿已经变化。聊天和 MCP 仍运行上一次发布版本，发布后才会切换。
          </div>
        ) : null}
        {!selectedWorkflowPublished ? <div className="inline-alert">该 Workflow 尚未发布，发布后才能聊天或通过 MCP 调用。</div> : null}
        {selectedWorkflowOrphanedMcpTools.length ? (
          <div className="orphan-workflow-alert">
            <div className="inline-alert warning">
              This workflow has {selectedWorkflowOrphanedMcpTools.length} MCP tool reference
              {selectedWorkflowOrphanedMcpTools.length > 1 ? "s" : ""} whose external server no longer exists.
            </div>
            <button className="ghost-danger-button" type="button" onClick={removeAllOrphanedMcpTools}>
              <Trash2 size={14} /> Remove all invalid MCP tools
            </button>
          </div>
        ) : null}
        <div className="workflow-board">
          {workflowNodes.map((node, index) => {
            const id = getWorkflowNodeId(node, index);
            const isSelected = selectedWorkflowNodeId === id || (!selectedWorkflowNodeId && index === 0);
            const outgoing = workflowEdges.filter((edge) => edge.source === id).map((edge) => edge.target);
            return (
              <div className="workflow-column" key={`${id}-${index}`}>
                <button className={isSelected ? "workflow-node active" : "workflow-node"} onClick={() => setSelectedWorkflowNodeId(id)}>
                  <span className={`node-icon ${getNodeKindClass(node)}`}>{renderWorkflowNodeIcon(node)}</span>
                  <span className="node-content">
                    <strong>{getWorkflowNodeLabel(node, index)}</strong>
                    <small>{renderWorkflowNodeSummary(node)}</small>
                  </span>
                  <span className="node-type">{getWorkflowNodeType(node)}</span>
                </button>
                {index < workflowNodes.length - 1 ? (
                  <div className="edge-line">
                    <span />
                    <small>{outgoing.length ? outgoing.join(", ") : "next"}</small>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  function renderRetrievalNodeSettings() {
    const queryEnhancementEnabled = Boolean(retrievalNodeModel.query_enhancement_enabled ?? false);
    const validIds = new Set(knowledgeBases.map((item) => item.id));
    const selectedIds = Array.isArray(retrievalNodeModel.knowledge_base_ids)
      ? retrievalNodeModel.knowledge_base_ids.filter((item) => validIds.has(item))
      : [];
    return (
      <div className="form-grid">
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(retrievalNodeModel.enabled ?? true)}
            onChange={(event) => updateRetrievalConfig("enabled", event.target.checked)}
          />
          <span>
            <strong>启用检索节点</strong>
            <small>关闭后，Workflow 运行时会跳过该检索节点。</small>
          </span>
        </label>
        <div className="field-section-title">知识库选择</div>
        {knowledgeBases.length ? (
          <div className="checkbox-list">
            {knowledgeBases.map((kb) => (
              <label className="check-row bordered" key={kb.id}>
                <input type="checkbox" checked={selectedIds.includes(kb.id)} onChange={() => toggleKnowledgeBaseInNode(kb.id)} />
                <span>
                  <strong>{kb.name}</strong>
                  <small>{kb.description || `${kb.embedding_provider}/${kb.embedding_model}`}</small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="muted-copy">还没有知识库。可以到“知识库”页面创建并上传文档。</p>
        )}
        <div className="two-fields">
          <label>
            召回 Top-K
            <input
              type="number"
              value={Number(retrievalNodeModel.retrieval_top_k ?? 20)}
              onChange={(event) => updateRetrievalConfig("retrieval_top_k", Number(event.target.value))}
            />
          </label>
          <label>
            Rerank
            <select
              value={Boolean(retrievalNodeModel.rerank_enabled ?? false) ? "on" : "off"}
              onChange={(event) => updateRetrievalConfig("rerank_enabled", event.target.value === "on")}
            >
              <option value="off">关闭</option>
              <option value="on">开启</option>
            </select>
          </label>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={queryEnhancementEnabled}
            onChange={(event) => updateRetrievalConfig("query_enhancement_enabled", event.target.checked)}
          />
          <span>
            <strong>Query Enhancement</strong>
            <small>用独立 Query LLM 重写、HyDE 或生成多查询。</small>
          </span>
        </label>
        {queryEnhancementEnabled ? (
          <>
            <div className="inline-alert">
              Query Enhancement 使用这里单独选择的凭证和模型，不复用 Agent 节点模型配置。
            </div>
            <label>
              增强策略
              <select
                value={String(retrievalNodeModel.query_enhancement_strategy ?? "rewrite")}
                onChange={(event) => updateRetrievalConfig("query_enhancement_strategy", event.target.value)}
              >
                {QUERY_ENHANCEMENT_STRATEGIES.map((strategy) => (
                  <option key={strategy} value={strategy}>
                    {strategy}
                  </option>
                ))}
              </select>
            </label>
            <div className="two-fields">
              <label>
                Query LLM Provider
                <select
                  value={String(retrievalNodeModel.query_llm_provider ?? "")}
                  onChange={(event) => updateRetrievalQueryLlmProvider(event.target.value)}
                >
                  <option value="">选择 provider</option>
                  {QUERY_LLM_PROVIDERS.map((provider) => (
                    <option key={provider} value={provider}>
                      {provider}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Temperature
                <input
                  type="number"
                  step="0.1"
                  value={Number(retrievalNodeModel.query_llm_temperature ?? 0.2)}
                  onChange={(event) => updateRetrievalConfig("query_llm_temperature", Number(event.target.value))}
                />
              </label>
            </div>
            <label>
              Query LLM Model
              <input
                placeholder="gpt-4o-mini / deepseek-chat / qwen-plus"
                value={String(retrievalNodeModel.query_llm_model ?? "")}
                onChange={(event) => updateRetrievalConfig("query_llm_model", event.target.value)}
              />
            </label>
            <label>
              Query LLM Credential
              <select
                value={String(retrievalNodeModel.query_llm_credential_id ?? "")}
                onChange={(event) => updateRetrievalConfig("query_llm_credential_id", event.target.value)}
              >
                <option value="">选择 API 凭证</option>
                {credentials.map((credential) => (
                  <option key={credential.id} value={credential.id}>
                    {credential.name} · {credential.provider} · {credential.masked_api_key}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Query LLM Base URL
              <input
                placeholder="openai_compatible / vllm 必填"
                value={String(retrievalNodeModel.query_llm_base_url ?? "")}
                onChange={(event) => updateRetrievalConfig("query_llm_base_url", event.target.value)}
              />
            </label>
          </>
        ) : null}
      </div>
    );
  }

  function renderAgentNodeSettings() {
    if (!selectedOwnedApp || !selectedWorkflow) return null;
    return (
      <div className="form-grid">
        <label>
          模型提供方
          <select value={String(agentNodeModel.provider ?? "")} onChange={(event) => updateAgentConfig("provider", event.target.value)}>
            <option value="">继承 App 默认模型</option>
            {MODEL_PROVIDERS.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          模型名称
          <input
            placeholder={selectedOwnedApp.model_name}
            value={String(agentNodeModel.model_name ?? "")}
            onChange={(event) => updateAgentConfig("model_name", event.target.value)}
          />
        </label>
        <label>
          模型凭证
          <select value={String(agentNodeModel.credential_id ?? "")} onChange={(event) => updateAgentConfig("credential_id", event.target.value)}>
            <option value="">继承 App 凭证</option>
            {credentials.map((credential) => (
              <option key={credential.id} value={credential.id}>
                {credential.name} · {credential.masked_api_key}
              </option>
            ))}
          </select>
        </label>
        <label>
          Base URL
          <input
            placeholder={selectedOwnedApp.model_base_url || "https://api.openai.com/v1"}
            value={String(agentNodeModel.base_url ?? "")}
            onChange={(event) => updateAgentConfig("base_url", event.target.value)}
          />
        </label>
        <div className="two-fields">
          <label>
            Temperature
            <input
              type="number"
              placeholder={String(selectedOwnedApp.temperature)}
              value={String(agentNodeModel.temperature ?? "")}
              onChange={(event) => updateAgentConfig("temperature", event.target.value)}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              placeholder={String(selectedOwnedApp.top_p)}
              value={String(agentNodeModel.top_p ?? "")}
              onChange={(event) => updateAgentConfig("top_p", event.target.value)}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            placeholder={String(selectedOwnedApp.max_tokens)}
            value={String(agentNodeModel.max_tokens ?? "")}
            onChange={(event) => updateAgentConfig("max_tokens", event.target.value)}
          />
        </label>
        <div className="field-section-title">上下文预算</div>
        <div className="two-fields">
          <label>
            Context Window
            <input
              type="number"
              placeholder="8192"
              value={String(agentNodeModel.model_context_window ?? "")}
              onChange={(event) => updateAgentConfig("model_context_window", event.target.value)}
            />
          </label>
          <label>
            Safety Margin
            <input
              type="number"
              placeholder="400"
              value={String(agentNodeModel.context_safety_margin ?? "")}
              onChange={(event) => updateAgentConfig("context_safety_margin", event.target.value)}
            />
          </label>
        </div>
        <label>
          Reserved Output Tokens
          <input
            type="number"
            placeholder="1024"
            value={String(agentNodeModel.context_reserved_output_tokens ?? "")}
            onChange={(event) => updateAgentConfig("context_reserved_output_tokens", event.target.value)}
          />
        </label>
        <div className="field-section-title">内置工具</div>
        {tools.length ? (
          <div className="checkbox-list">
            {tools.map((tool) => (
              <label className="check-row bordered" key={tool.name}>
                <input type="checkbox" checked={enabledAgentToolNames.includes(tool.name)} onChange={() => toggleTool(tool.name)} />
                <span>
                  <strong>{tool.label || tool.name}</strong>
                  <small>{tool.description}</small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="muted-copy">平台暂未返回内置工具 catalog。</p>
        )}
        <div className="field-section-title">外部 MCP 工具</div>
        {orphanedAgentMcpTools.length ? (
          <div className="orphan-tool-list">
            <div className="inline-alert warning">
              This agent references MCP tools whose external server was deleted or is unavailable. Remove them before
              saving the workflow.
            </div>
            {orphanedAgentMcpTools.map((tool) => (
              <div className="orphan-tool-row" key={buildMcpToolKey(tool.server_id, tool.name)}>
                <span>
                  <strong>{tool.name}</strong>
                  <small>Missing server: {tool.server_id}</small>
                </span>
                <button
                  className="ghost-danger-button"
                  type="button"
                  onClick={() => removeOrphanedMcpTool(tool.server_id, tool.name)}
                >
                  <Trash2 size={14} /> Remove
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {visibleExternalMcpServers.length === 0 ? <p className="muted-copy">先到 MCP 页面注册并同步外部 Server。</p> : null}
        {visibleExternalMcpServers.map((server) => {
          const serverTools = getExternalServerTools(server);
          return (
            <div className="tool-server-group" key={server.id}>
              <div className="tool-server-heading">
                <span>
                  <strong>{server.name}</strong>
                  <small>{server.server_url}</small>
                </span>
                <span className={server.status === "active" ? "badge success" : "badge muted"}>{server.status}</span>
              </div>
              {server.last_sync_error ? <div className="inline-alert error">{server.last_sync_error}</div> : null}
              {serverTools.length ? (
                <div className="checkbox-list">
                  {serverTools.map((tool) => {
                    const toolKey = buildMcpToolKey(server.id, tool.name);
                    return (
                      <label className="check-row bordered" key={toolKey}>
                        <input
                          type="checkbox"
                          checked={enabledAgentMcpToolKeys.includes(toolKey)}
                          onChange={() => toggleMcpTool(server.id, tool.name)}
                        />
                        <span>
                          <strong>{tool.name}</strong>
                          <small>{tool.description || "远端 MCP tool"}</small>
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : (
                <p className="muted-copy">该 Server 还没有同步到 tools。</p>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  function renderNodeInspector() {
    if (!selectedWorkflow || !selectedWorkflowNode) {
      return (
        <aside className="studio-inspector">
          {renderEmptyState("没有选中节点", "选择画布中的节点后会显示配置面板。")}
        </aside>
      );
    }
    const title = getWorkflowNodeLabel(selectedWorkflowNode, Math.max(selectedWorkflowNodeIndex, 0));
    return (
      <aside className="studio-inspector">
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Node Inspector</span>
              <h2>{title}</h2>
            </div>
            <span className={`node-icon small ${getNodeKindClass(selectedWorkflowNode)}`}>{renderWorkflowNodeIcon(selectedWorkflowNode)}</span>
          </div>
          <div className="node-meta-row">
            <span>{selectedWorkflowNodeKey}</span>
            <span>{selectedWorkflowNodeType}</span>
          </div>
          {!canEditSelectedWorkflow ? (
            <div className="readonly-card">
              <strong>只读使用模式</strong>
              <small>你可以运行已发布 Workflow，但不能修改草稿节点、工具、知识库或模型配置。</small>
            </div>
          ) : null}
          {canEditSelectedWorkflow && selectedWorkflowNodeIsRetrieval ? renderRetrievalNodeSettings() : null}
          {canEditSelectedWorkflow && selectedWorkflowNodeIsAgent ? renderAgentNodeSettings() : null}
          {canEditSelectedWorkflow && !selectedWorkflowNodeIsRetrieval && !selectedWorkflowNodeIsAgent ? (
            <div className="readonly-card">
              <strong>{renderWorkflowNodeSummary(selectedWorkflowNode)}</strong>
              <small>该节点当前只展示结构，不提供编辑项。</small>
            </div>
          ) : null}
        </section>
      </aside>
    );
  }

  function renderChatPanel() {
    return (
      <section className="work-panel chat-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Debug Chat</span>
            <h2>调试发布版本</h2>
          </div>
          {conversationId ? <span className="badge muted">会话 {shortId(conversationId)}</span> : <span className="badge muted">新会话</span>}
        </div>
        {selectedWorkflowHasUnpublishedChanges ? (
          <div className="inline-alert warning">当前聊天仍运行已发布版本；草稿改动需要发布后才会生效。</div>
        ) : null}
        {selectedWorkflow && !selectedWorkflowPublished ? <div className="inline-alert">未发布 Workflow 不能聊天。</div> : null}
        <div className="messages">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <MessageSquare size={20} />
              <strong>发送一条问题来调试 Workflow</strong>
              <span>回答、检索事件和工具调用会同步记录到右侧运行日志。</span>
            </div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))
          )}
        </div>
        <div className="composer">
          <input
            value={input}
            placeholder="输入调试问题"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") sendMessage();
            }}
          />
          <button className="primary-button" disabled={busy || !selectedWorkflowPublished || !input.trim()} onClick={sendMessage}>
            {busy ? <Loader2 className="spin" size={15} /> : <Send size={15} />} 发送
          </button>
        </div>
      </section>
    );
  }

  function renderStudioView() {
    return (
      <div className="studio-layout">
        <aside className="studio-left">
          {renderAppSettingsPanel()}
          {renderWorkflowListPanel()}
          {selectedWorkflow ? (
            <section className="work-panel">
              <div className="panel-heading compact-heading">
                <div>
                  <span className="eyebrow">Published Versions</span>
                  <h2>发布记录</h2>
                </div>
                <span className="badge muted">{workflowVersions.length}</span>
              </div>
              <div className="version-list">
                {workflowVersions.map((version) => (
                  <div className="version-card" key={version.id}>
                    <strong>v{version.version_number}</strong>
                    <small>{formatTimestamp(version.created_at)}</small>
                    {version.id === selectedWorkflow.published_version_id ? <span className="badge success">当前线上</span> : null}
                  </div>
                ))}
                {!workflowVersions.length ? <p className="muted-copy">还没有发布版本。</p> : null}
              </div>
            </section>
          ) : null}
        </aside>
        <section className="studio-center">
          {renderWorkflowCanvas()}
          {renderChatPanel()}
        </section>
        {renderNodeInspector()}
      </div>
    );
  }

  function renderAppDetailView() {
    if (!selectedApp) return renderWorkspaceHome();
    return (
      <div className="app-detail-view">
        <section className="app-detail-header">
          <div className="app-title-block">
            <button className="secondary-button" onClick={returnToWorkspaceHome}>
              <ChevronRight className="back-icon" size={15} /> 返回工作室
            </button>
            <span className="app-avatar">
              <Bot size={21} />
            </span>
            <span>
              <span className="eyebrow">App</span>
              <h2>{selectedApp.name}</h2>
              <small>{selectedApp.description || `App ${shortId(selectedApp.id)}`}</small>
            </span>
          </div>
          <nav className="app-tabs" aria-label="App 内导航">
            {APP_NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  className={activeAppView === item.id ? "app-tab active" : "app-tab"}
                  onClick={() => setActiveAppView(item.id)}
                >
                  <Icon size={16} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </nav>
        </section>
        <section className="app-detail-content">
          {activeAppView === "mcp" ? renderMcpView() : null}
          {activeAppView === "logs" ? renderLogsView() : null}
          {activeAppView === "studio" ? renderStudioView() : null}
        </section>
      </div>
    );
  }

  function renderKnowledgeView() {
    return (
      <div className="resource-layout">
        <section className="work-panel resource-list-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Knowledge Bases</span>
              <h2>知识库</h2>
            </div>
          </div>
          <div className="form-grid">
            <div className="two-fields wide-left">
              <label>
                名称
                <input value={knowledgeDraft.name} onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, name: event.target.value })} />
              </label>
              <label>
                操作
                <button className="secondary-button field-button" disabled={!knowledgeDraft.name.trim() || busy} onClick={createKnowledgeBase}>
                  <Plus size={15} /> 新建
                </button>
              </label>
            </div>
            <label>
              描述
              <input
                value={knowledgeDraft.description}
                onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, description: event.target.value })}
              />
            </label>
          </div>
          <div className="stack-list roomy">
            {knowledgeBases.map((kb) => (
              <button
                className={selectedKnowledgeBaseId === kb.id ? "list-card active" : "list-card"}
                key={kb.id}
                onClick={() => setSelectedKnowledgeBaseId(kb.id)}
              >
                <span className="list-card-main">
                  <strong>{kb.name}</strong>
                  <small>{kb.description || `${kb.embedding_provider}/${kb.embedding_model}`}</small>
                </span>
                <span className={kb.locked ? "badge success" : "badge muted"}>{kb.locked ? "索引锁定" : "未锁定"}</span>
              </button>
            ))}
            {!knowledgeBases.length ? renderEmptyState("还没有知识库", "创建知识库后上传文档，检索节点才能选择它。") : null}
          </div>
        </section>
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Documents</span>
              <h2>{selectedKnowledgeBase?.name ?? "文档"}</h2>
            </div>
            {selectedKnowledgeBase ? (
              <div className="heading-actions">
                <label className="upload-button">
                  <FileUp size={15} /> 上传文档
                  <input
                    type="file"
                    accept=".txt,.md,.py,.js,.jsx,.ts,.tsx,.java,.go,.json,.yaml,.yml,.csv,.html,.css,.pdf,.docx,.pptx,.xlsx,.xls"
                    disabled={busy}
                    onChange={(event) => uploadKnowledgeFile(event.target.files?.[0] ?? null)}
                  />
                </label>
                <button className="secondary-button" disabled={busy} onClick={rebuildSelectedKnowledgeBase}>
                  <RefreshCw size={15} /> 重建索引
                </button>
                <button className="ghost-danger-button" disabled={busy} onClick={deleteSelectedKnowledgeBase}>
                  <Trash2 size={15} /> 删除知识库
                </button>
              </div>
            ) : null}
          </div>
          {selectedKnowledgeBase ? (
            <>
              <div className="kb-meta-grid">
                <div>
                  <span>Embedding</span>
                  <strong>
                    {selectedKnowledgeBase.embedding_provider}/{selectedKnowledgeBase.embedding_model}
                  </strong>
                </div>
                <div>
                  <span>Dimension</span>
                  <strong>{selectedKnowledgeBase.embedding_dimension}</strong>
                </div>
                <div>
                  <span>Chunk</span>
                  <strong>
                    {selectedKnowledgeBase.chunk_size}/{selectedKnowledgeBase.chunk_overlap}
                  </strong>
                </div>
              </div>
              <div className="table-list">
                {knowledgeDocuments.map((document) => (
                  <div className="table-row" key={document.id}>
                    <FileText size={16} />
                    <span className="table-main">
                      <strong>{document.filename}</strong>
                      <small>
                        {document.status}
                        {document.error ? ` · ${document.error}` : ""}
                      </small>
                    </span>
                    <button className="icon-button danger" title="删除文档" aria-label="删除文档" onClick={() => deleteKnowledgeDocument(document.id)}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
                {!knowledgeDocuments.length ? <p className="muted-copy">该知识库暂无文档。</p> : null}
              </div>
            </>
          ) : (
            renderEmptyState("请选择知识库", "左侧选择知识库后可以上传文档和查看索引状态。")
          )}
        </section>
      </div>
    );
  }

  function renderCredentialsView() {
    return (
      <div className="resource-layout">
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Credentials</span>
              <h2>新增模型凭证</h2>
            </div>
          </div>
          <div className="form-grid">
            <div className="two-fields">
              <label>
                Provider
                <select value={credentialDraft.provider} onChange={(event) => setCredentialDraft({ ...credentialDraft, provider: event.target.value })}>
                  {CREDENTIAL_PROVIDERS.map((provider) => (
                    <option key={provider} value={provider}>
                      {provider}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                名称
                <input value={credentialDraft.name} onChange={(event) => setCredentialDraft({ ...credentialDraft, name: event.target.value })} />
              </label>
            </div>
            <label>
              API Key
              <input
                type="password"
                value={credentialDraft.api_key}
                onChange={(event) => setCredentialDraft({ ...credentialDraft, api_key: event.target.value })}
              />
            </label>
            <button className="primary-button form-submit" disabled={!credentialDraft.api_key.trim()} onClick={createCredential}>
              <Plus size={15} /> 创建凭证
            </button>
          </div>
        </section>
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Provider Keys</span>
              <h2>凭证列表</h2>
            </div>
            <span className="badge muted">{credentials.length}</span>
          </div>
          <div className="table-list">
            {credentials.map((credential) => (
              <div className="table-row" key={credential.id}>
                <KeyRound size={16} />
                <span className="table-main">
                  <strong>{credential.name}</strong>
                  <small>
                    {credential.provider} · {credential.masked_api_key} · {formatTimestamp(credential.updated_at)}
                  </small>
                </span>
                <button className="icon-button danger" title="删除凭证" aria-label="删除凭证" onClick={() => deleteCredential(credential.id)}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            {!credentials.length ? <p className="muted-copy">还没有模型凭证。</p> : null}
          </div>
        </section>
      </div>
    );
  }

  function renderWorkflowMcpPanel() {
    if (!selectedWorkflow || !workflowMcpDraft) {
      return (
        <section className="work-panel">
          {renderEmptyState("请选择 Workflow", "选择一个 Workflow 后可以把它暴露为 MCP Server。")}
        </section>
      );
    }
    const endpoint = getWorkflowMcpEndpoint(slugifyMcpServerName(workflowMcpDraft.server_slug));
    const canManageWorkflowMcp = canEditSelectedWorkflow;
    return (
      <section className="work-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">MCP Server</span>
            <h2>暴露当前 Workflow</h2>
          </div>
          {workflowMcpServer ? (
            <span className={workflowMcpServer.enabled ? "badge success" : "badge muted"}>{workflowMcpServer.enabled ? "启用" : "停用"}</span>
          ) : (
            <span className="badge muted">未创建</span>
          )}
        </div>
        <div className="form-grid">
          <label className="check-row">
            <input
              type="checkbox"
              disabled={!canManageWorkflowMcp}
              checked={workflowMcpDraft.enabled}
              onChange={(event) => setWorkflowMcpDraft({ ...workflowMcpDraft, enabled: event.target.checked })}
            />
            <span>
              <strong>启用 MCP Endpoint</strong>
              <small>外部 MCP Client 将调用当前 Workflow 的已发布版本。</small>
            </span>
          </label>
          <div className="two-fields">
            <label>
              Server Name
              <input
                disabled={!canManageWorkflowMcp}
                value={workflowMcpDraft.server_name}
                onChange={(event) => {
                  const serverName = event.target.value;
                  const nextSlug = workflowMcpDraft.configured
                    ? workflowMcpDraft.server_slug
                    : slugifyMcpServerName(serverName) || workflowMcpDraft.server_slug;
                  setWorkflowMcpDraft({ ...workflowMcpDraft, server_name: serverName, server_slug: nextSlug });
                }}
              />
            </label>
            <label>
              Server Slug
              <input
                disabled={!canManageWorkflowMcp}
                value={workflowMcpDraft.server_slug}
                onChange={(event) => setWorkflowMcpDraft({ ...workflowMcpDraft, server_slug: slugifyMcpServerName(event.target.value) })}
              />
            </label>
          </div>
          <label>
            描述
            <textarea
              disabled={!canManageWorkflowMcp}
              rows={2}
              value={workflowMcpDraft.description}
              onChange={(event) => setWorkflowMcpDraft({ ...workflowMcpDraft, description: event.target.value })}
            />
          </label>
          <div className="readonly-card">
            <small>Endpoint</small>
            <strong>{endpoint}</strong>
            <small>认证方式：Bearer token。`tools/call` 会运行 `Workflow.published_version_id` 指向的版本。</small>
          </div>
          {!canManageWorkflowMcp ? <div className="inline-alert">只有 Workflow owner 可以管理 MCP Server 配置。</div> : null}
          {workflowMcpToken ? (
            <label>
              新 token
              <textarea className="token-block" rows={3} readOnly value={workflowMcpToken} />
            </label>
          ) : null}
          <div className="heading-actions">
            <button className="secondary-button" disabled={!canManageWorkflowMcp} onClick={saveWorkflowMcpServerConfig}>
              <Save size={15} /> {workflowMcpServer ? "保存配置" : "创建 Server"}
            </button>
            <button className="secondary-button" disabled={!workflowMcpServer || !canManageWorkflowMcp} onClick={rotateWorkflowMcpServerAccessToken}>
              <RefreshCw size={15} /> 轮换 token
            </button>
          </div>
        </div>
      </section>
    );
  }

  function renderExternalMcpPanel() {
    const selectedServerTools = getExternalServerTools(selectedExternalMcpServer);
    const creatingNewServer = selectedExternalMcpServerId === NEW_EXTERNAL_MCP_SERVER_ID;
    const syncingSelectedExternalMcpServer = Boolean(
      selectedExternalMcpServer && syncingExternalMcpServerId === selectedExternalMcpServer.id,
    );
    return (
      <section className="work-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">MCP Client</span>
            <h2>外部 MCP Servers</h2>
          </div>
          <button className="secondary-button" onClick={newExternalMcpServerDraft}>
            <Plus size={15} /> 新建连接
          </button>
        </div>
        {hiddenSelfExternalMcpServerCount > 0 ? (
          <div className="inline-alert">
            已隐藏 {hiddenSelfExternalMcpServerCount} 个指向当前 App 自己发布 endpoint 的连接。发布方请在上方 MCP Server 区域修改，不作为外部 MCP Server 管理。
          </div>
        ) : null}
        <div className="mcp-split">
          <div className="stack-list">
            {visibleExternalMcpServers.map((server) => {
              const serverTools = getExternalServerTools(server);
              return (
                <button
                  className={selectedExternalMcpServerId === server.id ? "list-card active" : "list-card"}
                  key={server.id}
                  onClick={() => setSelectedExternalMcpServerId(server.id)}
                >
                  <span className="list-card-main">
                    <strong>{server.name}</strong>
                    <small>{server.server_url}</small>
                  </span>
                  <span className={server.status === "active" ? "badge success" : "badge muted"}>{serverTools.length} tools</span>
                </button>
              );
            })}
            {creatingNewServer ? (
              <div className="list-card active">
                <span className="list-card-main">
                  <strong>新建 Server</strong>
                  <small>填写 Streamable HTTP MCP Server 地址。</small>
                </span>
                <span className="badge muted">草稿</span>
              </div>
            ) : null}
            {!visibleExternalMcpServers.length && !creatingNewServer ? <p className="muted-copy">还没有可用于当前 App 的外部 MCP Server。</p> : null}
          </div>
          <div className="form-grid">
            <label>
              名称
              <input value={externalMcpDraft.name} onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, name: event.target.value })} />
            </label>
            <label>
              描述
              <textarea
                rows={2}
                value={externalMcpDraft.description}
                onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, description: event.target.value })}
              />
            </label>
            <div className="two-fields">
              <label>
                Transport
                <select
                  value={externalMcpDraft.transport_type}
                  onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, transport_type: event.target.value })}
                >
                  {MCP_TRANSPORT_TYPES.map((transport) => (
                    <option key={transport} value={transport}>
                      {transport}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Auth
                <select value={externalMcpDraft.auth_type} onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, auth_type: event.target.value })}>
                  {MCP_AUTH_TYPES.map((authType) => (
                    <option key={authType} value={authType}>
                      {authType}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Server URL
              <input
                placeholder="https://example.com/mcp"
                value={externalMcpDraft.server_url}
                onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, server_url: event.target.value })}
              />
            </label>
            {externalMcpDraft.auth_type === "bearer" ? (
              <label>
                Bearer Token
                <input
                  type="password"
                  placeholder={selectedExternalMcpServer?.has_auth_secret ? "留空表示沿用当前 token" : ""}
                  value={externalMcpDraft.auth_secret}
                  onChange={(event) => setExternalMcpDraft({ ...externalMcpDraft, auth_secret: event.target.value })}
                />
              </label>
            ) : null}
            <div className="header-editor">
              <div className="field-row-title">
                <span>Custom Headers</span>
                <div className="heading-actions compact">
                  <button className="secondary-button" type="button" onClick={addExternalHeader}>
                    <Plus size={14} /> Add
                  </button>
                  <button className="ghost-danger-button" type="button" disabled={!externalMcpDraft.custom_headers.length} onClick={clearExternalHeaders}>
                    Clear
                  </button>
                </div>
              </div>
              {externalMcpDraft.custom_headers.map((header, index) => (
                <div className="header-row" key={`${header.name}-${index}`}>
                  <input
                    placeholder="x-api-key"
                    value={header.name}
                    onChange={(event) => updateExternalHeader(index, { name: event.target.value })}
                  />
                  <input
                    type="password"
                    placeholder={header.saved ? "Saved value hidden; enter a new value to replace" : "Header value"}
                    value={header.value}
                    onChange={(event) => updateExternalHeader(index, { value: event.target.value })}
                  />
                  <button className="icon-danger-button" type="button" title="Remove header" onClick={() => removeExternalHeader(index)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              {!externalMcpDraft.custom_headers.length ? <p className="muted-copy">No custom headers configured.</p> : null}
              {externalMcpDraft.custom_headers.some((header) => header.saved) ? (
                <p className="muted-copy">Saved header values are hidden. Editing this section replaces the saved header set.</p>
              ) : null}
            </div>
            {selectedExternalMcpServer ? (
              <div className="readonly-card">
                <small>同步状态</small>
                <strong>{selectedExternalMcpServer.status}</strong>
                <small>Headers: {selectedExternalMcpServer.custom_header_names?.length ? selectedExternalMcpServer.custom_header_names.join(", ") : "none"}</small>
                <small>Session: {selectedExternalMcpServer.has_mcp_session ? "active" : "none"}</small>
                <small>Last sync：{formatTimestamp(selectedExternalMcpServer.last_sync_at)}</small>
              </div>
            ) : null}
            {selectedExternalMcpServer?.last_sync_error ? <div className="inline-alert error">{selectedExternalMcpServer.last_sync_error}</div> : null}
            <div className="heading-actions">
              <button className="secondary-button" onClick={saveExternalMcpServer}>
                <Save size={15} /> {selectedExternalMcpServer ? "保存 Server" : "创建 Server"}
              </button>
              <button
                className="secondary-button"
                disabled={!selectedExternalMcpServer || syncingSelectedExternalMcpServer}
                onClick={syncSelectedExternalMcpServer}
              >
                {syncingSelectedExternalMcpServer ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
                {syncingSelectedExternalMcpServer ? "同步中..." : "同步 tools"}
              </button>
              <button className="ghost-danger-button" disabled={!selectedExternalMcpServer} onClick={deleteSelectedExternalMcpServer}>
                <Trash2 size={15} /> 删除
              </button>
            </div>
          </div>
        </div>
        {selectedExternalMcpServer ? (
          <div className="tool-grid">
            {selectedServerTools.map((tool) => (
              <div className="tool-card" key={`${selectedExternalMcpServer.id}-${tool.name}`}>
                <strong>{tool.name}</strong>
                <small>{tool.description || "No description"}</small>
              </div>
            ))}
            {!selectedServerTools.length ? <p className="muted-copy">同步后会在这里显示远端 tools manifest。</p> : null}
          </div>
        ) : null}
      </section>
    );
  }

  function renderMcpView() {
    return (
      <div className="mcp-layout">
        {renderWorkflowMcpPanel()}
        {renderExternalMcpPanel()}
      </div>
    );
  }

  function renderLogsView() {
    return (
      <div className="logs-layout">
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Runs</span>
              <h2>运行记录</h2>
            </div>
            <span className="badge muted">{runs.length}</span>
          </div>
          <div className="stack-list roomy">
            {runs.map((run) => (
              <button className={selectedRunId === run.id ? "list-card active" : "list-card"} key={run.id} onClick={() => selectRun(run)}>
                <span className="list-card-main">
                  <strong>{run.status}</strong>
                  <small>
                    v {shortId(run.workflow_version_id)} · {run.latency_ms} ms · {formatTimestamp(run.created_at)}
                  </small>
                </span>
                <span className={run.error ? "badge danger" : "badge success"}>{run.error ? "error" : "ok"}</span>
              </button>
            ))}
            {!runs.length ? <p className="muted-copy">当前 Workflow 暂无运行记录。</p> : null}
          </div>
        </section>
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Run Steps</span>
              <h2>步骤详情</h2>
            </div>
          </div>
          <div className="table-list">
            {runSteps.map((step) => (
              <div className="step-card" key={step.id}>
                <div className="step-heading">
                  <strong>{step.name || step.type}</strong>
                  <span className={step.error ? "badge danger" : "badge muted"}>{step.latency_ms} ms</span>
                </div>
                <small>
                  {step.type} · {formatTimestamp(step.started_at)}
                  {step.error ? ` · ${step.error}` : ""}
                </small>
                <details>
                  <summary>查看输入输出</summary>
                  <pre>{JSON.stringify({ input: step.input_json, output: step.output_json }, null, 2)}</pre>
                </details>
              </div>
            ))}
            {!runSteps.length ? <p className="muted-copy">选择一条 run 查看步骤。</p> : null}
          </div>
        </section>
        <section className="work-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Current Trace</span>
              <h2>本次调试事件</h2>
            </div>
          </div>
          <pre className="trace-json">{trace.length ? JSON.stringify(trace, null, 2) : "暂无 trace"}</pre>
        </section>
      </div>
    );
  }

  function renderActiveView() {
    if (activeView === "workspace") return selectedApp ? renderAppDetailView() : renderWorkspaceHome();
    if (activeView === "knowledge") return renderKnowledgeView();
    if (activeView === "credentials") return renderCredentialsView();
    return renderWorkspaceHome();
  }

  if (authLoading) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <div className="brand-mark">
            <Bot size={22} />
            <div>
              <h1>Dify-like</h1>
              <p>正在检查登录状态</p>
            </div>
          </div>
        </section>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <div className="brand-mark large">
            <Bot size={24} />
            <div>
              <h1>Dify-like</h1>
              <p>知识库、Workflow 与 MCP 编排工作台</p>
            </div>
          </div>
          <label>
            邮箱
            <input autoComplete="email" value={authForm.email} onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })} />
          </label>
          <label>
            密码
            <input
              autoComplete="current-password"
              type="password"
              value={authForm.password}
              onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitAuth("login");
              }}
            />
          </label>
          {authError ? <p className="auth-error">{authError}</p> : null}
          <div className="auth-actions">
            <button className="primary-button" disabled={authBusy} onClick={() => submitAuth("login")}>
              <LogIn size={15} /> 登录
            </button>
            <button className="secondary-button" disabled={authBusy} onClick={() => submitAuth("register")}>
              <Plus size={15} /> 注册
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="workspace-shell">
      <aside className="global-sidebar">
        <div className="brand-mark">
          <Bot size={22} />
          <div>
            <h1>Dify-like</h1>
            <p>LLM Workflow Console</p>
          </div>
        </div>
        <nav className="main-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={activeView === item.id ? "nav-item active" : "nav-item"}
                onClick={() => {
                  if (item.id === "workspace") {
                    returnToWorkspaceHome();
                    return;
                  }
                  setActiveView(item.id);
                }}
              >
                <Icon size={18} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="session-card">
            <UserRound size={16} />
            <span>{user.email}</span>
            <button className="icon-button" title="退出登录" aria-label="退出登录" onClick={logout}>
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>
      <section className="workspace-main">
        <header className="topbar">
          <div className="topbar-title">
            <span className="eyebrow">{NAV_ITEMS.find((item) => item.id === activeView)?.description}</span>
            <h1>{NAV_ITEMS.find((item) => item.id === activeView)?.label}</h1>
          </div>
          <div className="topbar-context">
            {activeView === "workspace" && selectedApp ? (
              <div className="context-pill">
                <Wrench size={14} />
                <span>{selectedApp.name}</span>
              </div>
            ) : null}
            {activeView === "workspace" && selectedWorkflow ? (
              <div className="context-pill">
                <GitBranch size={14} />
                <span>{selectedWorkflow.name}</span>
              </div>
            ) : null}
            {activeView === "workspace" && selectedWorkflow ? renderStatusBadge(selectedWorkflow, true) : null}
          </div>
        </header>
        {statusMessage ? <div className="toast-notice">{statusMessage}</div> : null}
        <div className="workspace-content">{renderActiveView()}</div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
