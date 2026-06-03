import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Database,
  FileUp,
  GitBranch,
  History,
  KeyRound,
  LogIn,
  LogOut,
  MessageSquare,
  Play,
  Plus,
  Save,
  Trash2,
  UserRound,
  Wrench,
} from "lucide-react";
import { api, streamChat } from "./api";
import type {
  AppItem,
  ChatMessage,
  KnowledgeBase,
  KnowledgeDocument,
  ModelCredential,
  RunItem,
  RunStepItem,
  ToolItem,
  UserItem,
  WorkflowItem,
  WorkflowVersionItem,
} from "./types";
import "./styles.css";

const MODEL_PROVIDERS = ["mock", "openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];
const QUERY_LLM_PROVIDERS = MODEL_PROVIDERS.filter((provider) => provider !== "mock");
const CREDENTIAL_PROVIDERS = ["openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm", "zhipu", "zhipuai"];
const QUERY_ENHANCEMENT_STRATEGIES = ["rewrite", "hyde", "multi_query"];
const PROCESSING_DOCUMENT_STATUSES = new Set(["queued", "parsing", "chunking", "embedding"]);

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

type WorkflowNode = Record<string, unknown>;

type WorkflowEdge = {
  source: string;
  target: string;
};

type AuthAction = "login" | "register";

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

function getWorkflowNodeLabel(node: WorkflowNode, index: number) {
  const id = getWorkflowNodeId(node, index);
  const type = getWorkflowNodeType(node);
  if (id === "start" || type === "start") return "Start";
  if (id === "end" || type === "end") return "End";
  if (id === "agent" || type === "agent" || type === "react_agent") return "Agent";
  if (isRetrievalNode(node)) return "检索节点";
  return id;
}

function getAgentNodeModel(workflow: WorkflowItem): AgentNodeModel {
  const node = getWorkflowNodes(workflow).find((item) => {
    const type = getWorkflowNodeType(item);
    return item.id === "agent" || type === "agent" || type === "react_agent";
  });
  return isRecord(node?.model) ? (node.model as AgentNodeModel) : {};
}

function getRetrievalNode(workflow: WorkflowItem): WorkflowNode {
  return getWorkflowNodes(workflow).find((item) => isRetrievalNode(item)) ?? {};
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

function isAgentNode(node: WorkflowNode) {
  const type = getWorkflowNodeType(node);
  return node.id === "agent" || type === "agent" || type === "react_agent";
}

function updateAgentNodeTools(workflow: WorkflowItem, agentNodeId: string, toolNames: string[]): WorkflowItem {
  const nodes = getWorkflowNodes(workflow);
  const targetNode = nodes.find((node, index) => getWorkflowNodeId(node, index) === agentNodeId) ?? null;
  const existingTools = getAgentNodeTools(targetNode);
  const uniqueToolNames = Array.from(new Set(toolNames));
  const nextTools = uniqueToolNames.map((name) => {
    const existingTool = existingTools.find((tool) => tool.type === "builtin" && tool.name === name);
    return {
      type: "builtin",
      name,
      enabled: true,
      config: existingTool?.config ?? {},
    };
  });
  const nextNodes = nodes.map((node, index) => {
    const nodeId = getWorkflowNodeId(node, index);
    if (nodeId !== agentNodeId || !isAgentNode(node)) return node;
    return { ...node, tools: nextTools };
  });
  return {
    ...workflow,
    draft_spec: {
      ...(workflow.draft_spec ?? {}),
      nodes: nextNodes,
    },
  };
}

function updateAgentNodeModel(workflow: WorkflowItem, key: keyof AgentNodeModel, value: string | number): WorkflowItem {
  const spec = workflow.draft_spec ?? {};
  const nextNodes = getWorkflowNodes(workflow).map((item) => {
    const type = getWorkflowNodeType(item);
    const isAgentNode = item.id === "agent" || type === "agent" || type === "react_agent";
    if (!isAgentNode) return item;
    const model = isRecord(item.model) ? item.model : {};
    return {
      ...item,
      model: {
        ...model,
        [key]: value,
      },
    };
  });
  return {
    ...workflow,
    draft_spec: {
      ...spec,
      nodes: nextNodes,
    },
  };
}

function updateRetrievalNode(
  workflow: WorkflowItem,
  key: keyof RetrievalNodeModel,
  value: string | number | boolean | string[],
): WorkflowItem {
  const spec = workflow.draft_spec ?? {};
  const nextNodes = getWorkflowNodes(workflow).map((item) => {
    if (!isRetrievalNode(item)) return item;
    return { ...item, id: "retrieval", type: "retrieval", [key]: value };
  });
  return {
    ...workflow,
    draft_spec: {
      ...spec,
      nodes: nextNodes,
    },
  };
}

function pruneRetrievalKnowledgeBaseIds(workflow: WorkflowItem, knowledgeBases: KnowledgeBase[]): WorkflowItem {
  const validIds = new Set(knowledgeBases.map((item) => item.id));
  const spec = workflow.draft_spec ?? {};
  const nextNodes = getWorkflowNodes(workflow).map((item) => {
    if (!isRetrievalNode(item)) return item;
    const ids = Array.isArray(item.knowledge_base_ids) ? item.knowledge_base_ids : [];
    return {
      ...item,
      id: "retrieval",
      type: "retrieval",
      knowledge_base_ids: ids.map((id) => String(id)).filter((id) => validIds.has(id)),
    };
  });
  return {
    ...workflow,
    draft_spec: {
      ...spec,
      nodes: nextNodes,
    },
  };
}

function App() {
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
  const [draftSpecText, setDraftSpecText] = useState("");
  const [draftSpecError, setDraftSpecError] = useState("");
  const [credentials, setCredentials] = useState<ModelCredential[]>([]);
  const [credentialDraft, setCredentialDraft] = useState({
    provider: "openai_compatible",
    name: "",
    api_key: "",
  });
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState("");
  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocument[]>([]);
  const [knowledgeDraft, setKnowledgeDraft] = useState({ name: "", description: "" });
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [runSteps, setRunSteps] = useState<RunStepItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const [selectedWorkflowNodeId, setSelectedWorkflowNodeId] = useState("");
  const [input, setInput] = useState("这份知识库里讲了什么？");
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
  const canEditSelectedApp = Boolean(selectedOwnedApp);
  const canEditSelectedWorkflow = Boolean(selectedOwnedApp && selectedWorkflow);
  const agentNodeModel = useMemo(() => (selectedWorkflow ? getAgentNodeModel(selectedWorkflow) : {}), [selectedWorkflow]);
  const retrievalNode = useMemo(() => (selectedWorkflow ? getRetrievalNode(selectedWorkflow) : {}), [selectedWorkflow]);
  const retrievalNodeModel = retrievalNode as RetrievalNodeModel;
  const workflowNodes = useMemo(() => (selectedWorkflow ? getWorkflowNodes(selectedWorkflow) : []), [selectedWorkflow]);
  const workflowEdges = useMemo(() => (selectedWorkflow ? getWorkflowEdges(selectedWorkflow) : []), [selectedWorkflow]);
  const selectedWorkflowNode = useMemo(() => {
    const node = workflowNodes.find((item, index) => getWorkflowNodeId(item, index) === selectedWorkflowNodeId);
    return node ?? workflowNodes[0] ?? null;
  }, [selectedWorkflowNodeId, workflowNodes]);
  const selectedWorkflowNodeType = selectedWorkflowNode ? getWorkflowNodeType(selectedWorkflowNode) : "";
  const selectedWorkflowNodeIsAgent = Boolean(selectedWorkflowNode && isAgentNode(selectedWorkflowNode));
  const enabledAgentToolNames = useMemo(() => getEnabledAgentToolNames(selectedWorkflowNode), [selectedWorkflowNode]);

  function setActiveConversationId(nextConversationId: string | null) {
    conversationIdRef.current = nextConversationId;
    setConversationId(nextConversationId);
  }

  function resetWorkspace() {
    setApps([]);
    setWorkflows([]);
    setSelectedApp(null);
    setSelectedWorkflow(null);
    setWorkflowVersions([]);
    setDraftSpecText("");
    setDraftSpecError("");
    setCredentials([]);
    setKnowledgeBases([]);
    setSelectedKnowledgeBaseId("");
    setKnowledgeDocuments([]);
    setTools([]);
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

  async function selectWorkflow(workflow: WorkflowItem | null) {
    setSelectedWorkflow(workflow);
    setWorkflowVersions([]);
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

    const [versionList, runList] = await Promise.all([api.listWorkflowVersions(workflow.id), api.listWorkflowRuns(workflow.id)]);
    setWorkflowVersions(versionList);
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
          .map((message) => ({
            role: message.role,
            content: message.content,
          })),
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
    const currentWorkflowId = selectedWorkflow?.app_id === app.id ? selectedWorkflow.id : null;
    const workflow =
      workflowList.find((item) => item.id === preferredWorkflowId) ??
      workflowList.find((item) => item.id === currentWorkflowId) ??
      workflowList[0] ??
      null;
    await selectWorkflow(workflow);
  }

  async function refresh(preferredAppId?: string | null, preferredWorkflowId?: string | null) {
    if (!user) return;
    const [appList, toolList, credentialList, kbList] = await Promise.all([
      api.listApps(),
      api.listTools(),
      api.listModelCredentials(),
      api.listKnowledgeBases(),
    ]);
    setApps(appList);
    setTools(toolList);
    setCredentials(credentialList);
    setKnowledgeBases(kbList);
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
      workflowNodes.find((node, index) => {
        const id = getWorkflowNodeId(node, index);
        const type = getWorkflowNodeType(node);
        return isRetrievalNode(node) || id === "agent" || type === "react_agent";
      }) ?? workflowNodes[0];
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
        .catch((error) => {
          console.error(error);
        });
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [canEditSelectedApp, knowledgeDocuments, selectedKnowledgeBaseId]);

  useEffect(() => {
    if (!selectedOwnedApp) return;
    setCredentialDraft((draft) => ({
      ...draft,
      provider: defaultCredentialProvider(selectedOwnedApp.model_provider),
    }));
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
    const app = await api.createApp();
    await refresh(app.id);
  }

  async function createWorkflow() {
    if (!selectedOwnedApp) return;
    const workflow = await api.createWorkflow(selectedOwnedApp.id, {
      name: `Workflow ${workflows.length + 1}`,
      description: "",
    });
    await refresh(selectedOwnedApp.id, workflow.id);
    setStatusMessage("Workflow created.");
  }

  async function persistCurrentConfig() {
    if (!selectedOwnedApp) return null;
    if (draftSpecError) {
      throw new Error("Draft spec JSON is invalid.");
    }
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
      const saved = await persistCurrentConfig();
      if (!saved) return;
      await refresh(saved.app.id, saved.workflow?.id ?? selectedWorkflow?.id);
      setStatusMessage("配置已保存。");
    } catch (error) {
      setStatusMessage(`保存配置失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function publishSelectedWorkflow() {
    if (!selectedOwnedApp || !selectedWorkflow) return;
    const saved = await persistCurrentConfig();
    const workflowId = saved?.workflow?.id ?? selectedWorkflow.id;
    const version = await api.publishWorkflow(workflowId);
    await refresh(selectedOwnedApp.id, workflowId);
    setStatusMessage("应用已发布。");
  }

  async function deleteSelectedWorkflow() {
    if (!selectedOwnedApp || !selectedWorkflow) return;
    await api.deleteWorkflow(selectedWorkflow.id);
    await refresh(selectedOwnedApp.id);
    setStatusMessage("应用已取消发布。");
  }

  async function createKnowledgeBase() {
    const name = knowledgeDraft.name.trim();
    if (!name) return;
    setBusy(true);
    setStatusMessage("正在创建知识库...");
    try {
      const kb = await api.createKnowledgeBase({ name, description: knowledgeDraft.description.trim() });
      setKnowledgeDraft({ name: "", description: "" });
      setSelectedKnowledgeBaseId(kb.id);
      setKnowledgeBases(await api.listKnowledgeBases());
      setStatusMessage("知识库已创建。");
    } catch (error) {
      setStatusMessage(`创建知识库失败：${error instanceof Error ? error.message : String(error)}`);
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
    } finally {
      setBusy(false);
    }
  }

  async function deleteKnowledgeDocument(documentId: string) {
    if (!selectedKnowledgeBaseId) return;
    await api.deleteKnowledgeDocument(selectedKnowledgeBaseId, documentId);
    setKnowledgeDocuments(await api.listKnowledgeDocuments(selectedKnowledgeBaseId));
  }

  async function rebuildSelectedKnowledgeBase() {
    if (!selectedKnowledgeBaseId) return;
    await api.rebuildKnowledgeBase(selectedKnowledgeBaseId);
    setKnowledgeDocuments(await api.listKnowledgeDocuments(selectedKnowledgeBaseId));
  }

  async function createCredential() {
    const provider = credentialDraft.provider.trim();
    const apiKey = credentialDraft.api_key.trim();
    if (!provider || !apiKey) return;
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
  }

  async function deleteCredential(credentialId: string) {
    await api.deleteModelCredential(credentialId);
    setCredentials(await api.listModelCredentials());
    if (!selectedOwnedApp) return;
    let nextApp = selectedOwnedApp;
    if (selectedOwnedApp.model_credential_id === credentialId) {
      nextApp = { ...nextApp, model_credential_id: "" };
    }
    let nextWorkflow = selectedWorkflow;
    if (String(agentNodeModel.credential_id ?? "") === credentialId) {
      nextWorkflow = nextWorkflow ? updateAgentNodeModel(nextWorkflow, "credential_id", "") : nextWorkflow;
    }
    if (String(retrievalNodeModel.query_llm_credential_id ?? "") === credentialId) {
      nextWorkflow = nextWorkflow ? updateRetrievalNode(nextWorkflow, "query_llm_credential_id", "") : nextWorkflow;
    }
    setSelectedApp(nextApp);
    setSelectedWorkflow(nextWorkflow);
  }

  function toggleTool(toolName: string) {
    if (!canEditSelectedWorkflow || !selectedWorkflow || !selectedWorkflowNodeIsAgent) return;
    const agentNodeId = selectedWorkflowNodeId || (selectedWorkflowNode ? getWorkflowNodeId(selectedWorkflowNode, 0) : "");
    if (!agentNodeId) return;
    const next = enabledAgentToolNames.includes(toolName)
      ? enabledAgentToolNames.filter((name) => name !== toolName)
      : [...enabledAgentToolNames, toolName];
    setSelectedWorkflow(updateAgentNodeTools(selectedWorkflow, agentNodeId, next));
  }

  function updateRetrievalConfig(key: keyof RetrievalNodeModel, value: string | number | boolean | string[]) {
    if (!selectedWorkflow) return;
    setSelectedWorkflow(updateRetrievalNode(selectedWorkflow, key, value));
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
    if (!selectedWorkflow) return;
    let nextWorkflow = updateRetrievalNode(selectedWorkflow, "query_llm_provider", provider);
    nextWorkflow = updateRetrievalNode(nextWorkflow, "query_llm_base_url", defaultQueryLlmBaseUrl(provider));
    setSelectedWorkflow(nextWorkflow);
  }

  function updateDraftSpecText(value: string) {
    setDraftSpecText(value);
    if (!selectedWorkflow) return;
    try {
      const parsed = JSON.parse(value);
      if (!isRecord(parsed)) {
        throw new Error("draft_spec must be a JSON object");
      }
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
              next[next.length - 1] = {
                ...last,
                content: last.content ? `${last.content}\n\n${message}` : message,
              };
            }
            return next;
          });
        }
        if (event === "message_delta") {
          setMessages((items) => {
            const next = [...items];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, content: last.content + String(data.content ?? "") };
            return next;
          });
        }
        if (event === "retrieval" || event === "tool_call" || event === "final" || event === "workflow_warning") {
          setTrace((items) => [...items, { event, ...data }]);
        }
      });
      if (selectedWorkflow) {
        const runList = await api.listWorkflowRuns(selectedWorkflow.id);
        setRuns(runList);
        await selectRun(runList[0] ?? null);
      }
    } finally {
      setBusy(false);
    }
  }

  function renderWorkflowNodeIcon(type: string) {
    if (type === "retrieval") return <Database size={16} />;
    if (type === "react_agent" || type === "agent") return <Bot size={16} />;
    return <Play size={16} />;
  }

  function renderWorkflowNodeSummary(node: WorkflowNode) {
    const type = getWorkflowNodeType(node);
    if (isRetrievalNode(node)) {
      const ids = Array.isArray(node.knowledge_base_ids) ? node.knowledge_base_ids : [];
      return ids.length ? `${ids.length} 个知识库` : "未选择知识库";
    }
    if (type === "react_agent" || type === "agent") {
      const model = isRecord(node.model) ? node.model : {};
      return String(model.model_name ?? selectedOwnedApp?.model_name ?? "继承 App 模型");
    }
    if (type === "start") return "接收用户输入";
    if (type === "end") return "结束并返回结果";
    return type;
  }

  function renderWorkflowCanvas() {
    if (!selectedWorkflow) {
      return (
        <section className="panel">
          <div className="panel-title">
            <GitBranch size={16} /> Workflow
          </div>
          <p className="empty">Select or create a Workflow.</p>
        </section>
      );
    }
    return (
      <section className="panel">
        <div className="panel-title">
          <GitBranch size={16} /> Workflow 编排
        </div>
        <div className="workflow-canvas">
          {workflowNodes.map((node, index) => {
            const id = getWorkflowNodeId(node, index);
            const type = getWorkflowNodeType(node);
            const isSelected = selectedWorkflowNodeId === id || (!selectedWorkflowNodeId && index === 0);
            const outgoing = workflowEdges.filter((edge) => edge.source === id).map((edge) => edge.target);
            return (
              <div className="workflow-chain-item" key={`${id}-${index}`}>
                <button
                  className={isSelected ? "workflow-node active" : "workflow-node"}
                  onClick={() => setSelectedWorkflowNodeId(id)}
                >
                  <span className="workflow-node-icon">{renderWorkflowNodeIcon(type)}</span>
                  <span className="workflow-node-main">
                    <strong>{getWorkflowNodeLabel(node, index)}</strong>
                    <small>{type}</small>
                  </span>
                  <span className="workflow-node-meta">{renderWorkflowNodeSummary(node)}</span>
                </button>
                {index < workflowNodes.length - 1 ? (
                  <div className="workflow-link">
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

  function renderAppSettings() {
    if (!selectedOwnedApp) return null;
    return (
      <section className="panel">
        <div className="panel-title">
          <Wrench size={16} /> 应用设置
        </div>
        <label>
          名称
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
      </section>
    );
  }

  function renderWorkflowListPanel() {
    if (!selectedOwnedApp) return null;
    return (
      <section className="panel">
        <div className="panel-title panel-title-between">
          <span>
            <GitBranch size={16} /> Workflows
          </span>
          <button className="icon-button" title="Create workflow" onClick={createWorkflow}>
            <Plus size={14} />
          </button>
        </div>
        <div className="workflow-list">
          {workflows.map((workflow) => {
            const isSelected = selectedWorkflowId === workflow.id;
            const hasUnpublishedChanges = isSelected && selectedWorkflowHasUnpublishedChanges;
            const statusLabel = hasUnpublishedChanges
              ? "Unpublished changes"
              : workflow.published_version_id
                ? "Published"
                : "Draft only";
            const statusClass = hasUnpublishedChanges
              ? "status-pill dirty"
              : workflow.published_version_id
                ? "status-pill published"
                : "status-pill";
            return (
              <button
                className={isSelected ? "workflow-list-item active" : "workflow-list-item"}
                key={workflow.id}
                onClick={() => selectWorkflow(workflow)}
              >
                <span>
                  <strong>{workflow.name}</strong>
                  <small>{workflow.description || workflow.id}</small>
                </span>
                <small className={statusClass}>{statusLabel}</small>
              </button>
            );
          })}
          {workflows.length === 0 ? <p className="empty">No workflows found for this App.</p> : null}
        </div>
      </section>
    );
  }

  function renderWorkflowSettings() {
    if (!selectedWorkflow) return null;
    return (
      <section className="panel">
        <div className="panel-title">
          <GitBranch size={16} /> Workflow Detail
        </div>
        <label>
          Name
          <input
            value={selectedWorkflow.name}
            onChange={(event) => setSelectedWorkflow({ ...selectedWorkflow, name: event.target.value })}
          />
        </label>
        <label>
          Description
          <textarea
            rows={2}
            value={selectedWorkflow.description}
            onChange={(event) => setSelectedWorkflow({ ...selectedWorkflow, description: event.target.value })}
          />
        </label>
        <div className="readonly-node">
          <strong>{selectedWorkflow.published_version_id || "Workflow is not published"}</strong>
          <small>
            {selectedWorkflowHasUnpublishedChanges
              ? "current draft has unpublished changes"
              : selectedWorkflow.published_version_id
                ? "published_version_id"
                : "publish before chat"}
          </small>
        </div>
        {selectedWorkflowHasUnpublishedChanges ? (
          <div className="notice">Current draft has unpublished changes. Chat still runs the published version.</div>
        ) : null}
        {workflowVersions.length ? (
          <div className="version-list">
            {workflowVersions.map((version) => (
              <div className="version-item" key={version.id}>
                <strong>v{version.version_number}</strong>
                <span>{version.id}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty">No published versions yet.</p>
        )}
        <label>
          draft_spec JSON
          <textarea
            className="json-editor"
            rows={12}
            spellCheck={false}
            value={draftSpecText}
            onChange={(event) => updateDraftSpecText(event.target.value)}
          />
        </label>
        {draftSpecError ? <div className="error-notice">{draftSpecError}</div> : null}
        <button className="secondary danger" disabled={workflows.length <= 1} onClick={deleteSelectedWorkflow}>
          <Trash2 size={16} /> Delete Workflow
        </button>
      </section>
    );
  }

  function renderKnowledgeDatabasePanel() {
    return (
      <section className="panel">
        <div className="panel-title">
          <Database size={16} /> 知识库
        </div>
        <div className="grid-two">
          <label>
            名称
            <input value={knowledgeDraft.name} onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, name: event.target.value })} />
          </label>
          <label>
            操作
            <button className="secondary" disabled={!knowledgeDraft.name.trim()} onClick={createKnowledgeBase}>
              <Plus size={16} /> 新建
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
        <div className="kb-list">
          {knowledgeBases.map((kb) => (
            <button
              className={selectedKnowledgeBaseId === kb.id ? "kb-item active" : "kb-item"}
              key={kb.id}
              onClick={() => setSelectedKnowledgeBaseId(kb.id)}
            >
              <strong>{kb.name}</strong>
              <span>
                {kb.locked ? "已锁定" : "未锁定"} · {kb.embedding_provider}/{kb.embedding_model} · {kb.embedding_dimension}
              </span>
            </button>
          ))}
        </div>
        {selectedKnowledgeBase ? (
          <>
            <div className="actions-row">
              <label className="upload upload-inline">
                <FileUp size={16} />
                上传文档
                <input
                  type="file"
                  accept=".txt,.md,.py,.js,.jsx,.ts,.tsx,.java,.go,.json,.yaml,.yml,.csv,.html,.css,.pdf,.docx,.pptx,.xlsx,.xls"
                  disabled={busy}
                  onChange={(event) => uploadKnowledgeFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <button className="secondary inline-button" onClick={rebuildSelectedKnowledgeBase}>
                重建
              </button>
            </div>
            <div className="doc-list">
              {knowledgeDocuments.map((document) => (
                <div className="doc-item" key={document.id}>
                  <strong>{document.filename}</strong>
                  <span>
                    {document.status}
                    {document.error ? ` · ${document.error}` : ""}
                  </span>
                  <button className="icon-button" title="删除文档" onClick={() => deleteKnowledgeDocument(document.id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="empty">先创建一个知识库。</p>
        )}
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
      <>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(retrievalNodeModel.enabled ?? true)}
            onChange={(event) => updateRetrievalConfig("enabled", event.target.checked)}
          />
          <span>
            <strong>启用检索</strong>
            <small>关闭后 workflow 会跳过该节点</small>
          </span>
        </label>
        <div className="sub-panel-title">知识库选择</div>
        {knowledgeBases.map((kb) => (
          <label className="check" key={kb.id}>
            <input type="checkbox" checked={selectedIds.includes(kb.id)} onChange={() => toggleKnowledgeBaseInNode(kb.id)} />
            <span>
              <strong>{kb.name}</strong>
              <small>{kb.description || kb.embedding_model}</small>
            </span>
          </label>
        ))}
        <div className="grid-two">
          <label>
            召回 Top-K
            <input
              type="number"
              value={Number(retrievalNodeModel.retrieval_top_k ?? 20)}
              onChange={(event) => updateRetrievalConfig("retrieval_top_k", Number(event.target.value))}
            />
          </label>
          <label>
            Jina Rerank
            <select
              value={Boolean(retrievalNodeModel.rerank_enabled ?? false) ? "on" : "off"}
              onChange={(event) => updateRetrievalConfig("rerank_enabled", event.target.value === "on")}
            >
              <option value="off">关闭</option>
              <option value="on">开启</option>
            </select>
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={queryEnhancementEnabled}
            onChange={(event) => updateRetrievalConfig("query_enhancement_enabled", event.target.checked)}
          />
          <span>
            <strong>Query Enhancement</strong>
            <small>开启后必须配置独立的 Query LLM 凭据</small>
          </span>
        </label>
        {queryEnhancementEnabled ? (
          <>
            <div className="notice">
              Query Enhancement 使用创建者单独选择的 LLM API 和 key，不复用 Agent 节点模型配置。
            </div>
            <label>
              策略
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
            <div className="grid-two">
              <label>
                Query LLM Provider
                <select
                  value={String(retrievalNodeModel.query_llm_provider ?? "")}
                  onChange={(event) => updateRetrievalQueryLlmProvider(event.target.value)}
                >
                  <option value="">选择</option>
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
                <option value="">选择 API 凭据</option>
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
      </>
    );
  }

  function renderAgentNodeSettings() {
    if (!selectedOwnedApp || !selectedWorkflow) return null;
    return (
      <>
        <label>
          模型提供方
          <select
            value={String(agentNodeModel.provider ?? "")}
            onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "provider", event.target.value))}
          >
            <option value="">继承 App</option>
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
            onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "model_name", event.target.value))}
          />
        </label>
        <label>
          模型凭据
          <select
            value={String(agentNodeModel.credential_id ?? "")}
            onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "credential_id", event.target.value))}
          >
            <option value="">继承 App</option>
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
            onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "base_url", event.target.value))}
          />
        </label>
        <div className="grid-two">
          <label>
            温度
            <input
              type="number"
              placeholder={String(selectedOwnedApp.temperature)}
              value={String(agentNodeModel.temperature ?? "")}
              onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "temperature", event.target.value))}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              placeholder={String(selectedOwnedApp.top_p)}
              value={String(agentNodeModel.top_p ?? "")}
              onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "top_p", event.target.value))}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            placeholder={String(selectedOwnedApp.max_tokens)}
            value={String(agentNodeModel.max_tokens ?? "")}
            onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "max_tokens", event.target.value))}
          />
        </label>
        <div className="sub-panel-title">Agent Context</div>
        <div className="grid-two">
          <label>
            Context Window
            <input
              type="number"
              placeholder="8192"
              value={String(agentNodeModel.model_context_window ?? "")}
              onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "model_context_window", event.target.value))}
            />
          </label>
          <label>
            Safety
            <input
              type="number"
              placeholder="400"
              value={String(agentNodeModel.context_safety_margin ?? "")}
              onChange={(event) => setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "context_safety_margin", event.target.value))}
            />
          </label>
        </div>
        <label>
          Reserved Output Tokens
          <input
            type="number"
            placeholder="1024"
            value={String(agentNodeModel.context_reserved_output_tokens ?? "")}
            onChange={(event) =>
              setSelectedWorkflow(updateAgentNodeModel(selectedWorkflow, "context_reserved_output_tokens", event.target.value))
            }
          />
        </label>
        <div className="sub-panel-title">App 默认模型</div>
        <label>
          模型提供方
          <select
            value={selectedOwnedApp.model_provider}
            onChange={(event) => {
              const provider = event.target.value;
              setSelectedApp({ ...selectedOwnedApp, model_provider: provider });
              setCredentialDraft({ ...credentialDraft, provider: defaultCredentialProvider(provider) });
            }}
          >
            {MODEL_PROVIDERS.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          模型名称
          <input value={selectedOwnedApp.model_name} onChange={(event) => setSelectedApp({ ...selectedOwnedApp, model_name: event.target.value })} />
        </label>
        <label>
          模型凭据
          <select
            value={selectedOwnedApp.model_credential_id}
            onChange={(event) => setSelectedApp({ ...selectedOwnedApp, model_credential_id: event.target.value })}
          >
            <option value="">未选择</option>
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
            placeholder="https://api.openai.com/v1"
            value={selectedOwnedApp.model_base_url}
            onChange={(event) => setSelectedApp({ ...selectedOwnedApp, model_base_url: event.target.value })}
          />
        </label>
        <div className="grid-two">
          <label>
            温度
            <input
              type="number"
              value={selectedOwnedApp.temperature}
              onChange={(event) => setSelectedApp({ ...selectedOwnedApp, temperature: Number(event.target.value) })}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              value={selectedOwnedApp.top_p}
              onChange={(event) => setSelectedApp({ ...selectedOwnedApp, top_p: Number(event.target.value) })}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            value={selectedOwnedApp.max_tokens}
            onChange={(event) => setSelectedApp({ ...selectedOwnedApp, max_tokens: Number(event.target.value) })}
          />
        </label>
        <div className="sub-panel-title">工具</div>
        {tools.map((tool) => (
          <label className="check" key={tool.name}>
            <input type="checkbox" checked={enabledAgentToolNames.includes(tool.name)} onChange={() => toggleTool(tool.name)} />
            <span>
              <strong>{tool.label}</strong>
              <small>{tool.description}</small>
            </span>
          </label>
        ))}
      </>
    );
  }

  function renderSelectedNodeSettings() {
    if (!selectedOwnedApp || !selectedWorkflow || !selectedWorkflowNode) return null;
    const nodeIndex = workflowNodes.indexOf(selectedWorkflowNode);
    const title = getWorkflowNodeLabel(selectedWorkflowNode, Math.max(nodeIndex, 0));
    const id = getWorkflowNodeId(selectedWorkflowNode, Math.max(nodeIndex, 0));
    const selectedIsRetrievalNode = isRetrievalNode(selectedWorkflowNode);
    const isAgentNode = id === "agent" || selectedWorkflowNodeType === "agent" || selectedWorkflowNodeType === "react_agent";
    return (
      <section className="panel node-inspector">
        <div className="panel-title">
          {renderWorkflowNodeIcon(selectedWorkflowNodeType)} {title}
        </div>
        <div className="node-meta">
          <span>{id}</span>
          <span>{selectedWorkflowNodeType}</span>
        </div>
        {selectedIsRetrievalNode ? renderRetrievalNodeSettings() : null}
        {isAgentNode ? renderAgentNodeSettings() : null}
        {!selectedIsRetrievalNode && !isAgentNode ? (
          <div className="readonly-node">
            <strong>{renderWorkflowNodeSummary(selectedWorkflowNode)}</strong>
            <small>当前节点只展示运行结构。</small>
          </div>
        ) : null}
      </section>
    );
  }

  function renderCredentialPanel() {
    if (!canEditSelectedApp) return null;
    return (
      <section className="panel">
        <div className="panel-title">
          <KeyRound size={16} /> 模型凭据
        </div>
        <div className="grid-two">
          <label>
            提供方
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
        <button className="secondary" disabled={!credentialDraft.api_key.trim()} onClick={createCredential}>
          <Plus size={16} /> 新建凭据
        </button>
        <div className="credential-list">
          {credentials.map((credential) => (
            <div
              className={
                credential.id === selectedOwnedApp?.model_credential_id ||
                credential.id === String(agentNodeModel.credential_id ?? "") ||
                credential.id === String(retrievalNodeModel.query_llm_credential_id ?? "")
                  ? "credential-item active"
                  : "credential-item"
              }
              key={credential.id}
            >
              <span>
                <strong>{credential.name}</strong>
                <small>
                  {credential.provider} · {credential.masked_api_key}
                </small>
              </span>
              <button className="icon-button" title="删除凭据" onClick={() => deleteCredential(credential.id)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (authLoading) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <div className="brand">
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
          <div className="brand">
            <Bot size={22} />
            <div>
              <h1>Dify-like</h1>
              <p>知识库与检索节点工作台</p>
            </div>
          </div>
          <label>
            邮箱
            <input
              autoComplete="email"
              value={authForm.email}
              onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
            />
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
            <button className="primary" disabled={authBusy} onClick={() => submitAuth("login")}>
              <LogIn size={16} /> 登录
            </button>
            <button className="secondary" disabled={authBusy} onClick={() => submitAuth("register")}>
              <Plus size={16} /> 注册
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Bot size={22} />
          <div>
            <h1>Dify-like</h1>
            <p>知识库与检索节点</p>
          </div>
        </div>
        <div className="session">
          <div className="session-user">
            <UserRound size={16} />
            <span>{user.email}</span>
          </div>
          <button className="icon-button" title="退出登录" onClick={logout}>
            <LogOut size={14} />
          </button>
        </div>
        <button className="primary" onClick={createDemoApp}>
          <Play size={16} /> 创建应用
        </button>
        <div className="app-list">
          <div className="app-section-title">我的应用</div>
          {apps.map((app) => (
            <button
              key={app.id}
              className={selectedApp?.id === app.id ? "app-item active" : "app-item"}
              onClick={() => selectApp(app)}
            >
              <strong>{app.name}</strong>
              <span>{app.description || app.id}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="config">
        <header>
          <GitBranch size={18} />
          <h2>{canEditSelectedApp ? "创建者配置" : "使用模式"}</h2>
        </header>
        {statusMessage ? <div className="notice">{statusMessage}</div> : null}
        {canEditSelectedApp ? (
          <>
            {renderKnowledgeDatabasePanel()}
            {renderCredentialPanel()}
            {renderAppSettings()}
            {renderWorkflowListPanel()}
            {renderWorkflowSettings()}
            {renderWorkflowCanvas()}
            {renderSelectedNodeSettings()}
            <div className="actions-row">
              <button className="secondary inline-button" disabled={Boolean(draftSpecError)} onClick={saveConfig}>
                <Save size={16} /> 保存
              </button>
              <button
                className="secondary inline-button"
                disabled={!canEditSelectedWorkflow || Boolean(draftSpecError)}
                onClick={publishSelectedWorkflow}
              >
                Publish Workflow
              </button>
            </div>
          </>
        ) : (
          <section className="panel">
            <div className="panel-title">
              <MessageSquare size={16} /> 只读使用
            </div>
            <p className="empty">你可以对已发布应用提问，不能修改节点、知识库、工具或凭据。</p>
          </section>
        )}
      </section>

      <section className="chat">
        <header>
          <MessageSquare size={18} />
          <h2>Chat</h2>
        </header>
        <div className="messages">
          {messages.length === 0 ? (
            <div className="empty">选择一个应用后开始提问。</div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))
          )}
        </div>
        {selectedWorkflow && !selectedWorkflowPublished ? <div className="notice">Workflow is not published.</div> : null}
        {selectedWorkflowHasUnpublishedChanges ? (
          <div className="notice">Draft has unpublished changes. This chat uses the last published version.</div>
        ) : null}
        <div className="composer">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") sendMessage();
            }}
          />
          <button className="primary" disabled={busy || !selectedWorkflowPublished} onClick={sendMessage}>
            <Play size={16} /> 发送
          </button>
        </div>
      </section>

      <aside className="trace">
        <header>
          <History size={18} />
          <h2>Logs</h2>
        </header>
        <section className="panel">
          <div className="panel-title">当前 Trace</div>
          <pre>{trace.length ? JSON.stringify(trace, null, 2) : "暂无 trace"}</pre>
        </section>
        {canEditSelectedApp ? (
          <section className="panel">
            <div className="panel-title">最近 Runs</div>
            {runs.map((run) => (
              <button className={selectedRunId === run.id ? "run active" : "run"} key={run.id} onClick={() => selectRun(run)}>
                <strong>{run.status}</strong>
                <span>{run.workflow_version_id.slice(0, 8)} · {run.latency_ms} ms</span>
              </button>
            ))}
          </section>
        ) : null}
        {runSteps.length ? (
          <section className="panel">
            <div className="panel-title">Run Steps</div>
            {runSteps.map((step) => (
              <div className="run-step" key={step.id}>
                <strong>{step.name || step.type}</strong>
                <span>
                  {step.type} · {step.latency_ms} ms{step.error ? ` · ${step.error}` : ""}
                </span>
              </div>
            ))}
          </section>
        ) : null}
      </aside>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
