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
  AppTool,
  ChatMessage,
  KnowledgeBase,
  KnowledgeDocument,
  ModelCredential,
  PublishedAppItem,
  RunItem,
  ToolItem,
  UserItem,
} from "./types";
import "./styles.css";

const MODEL_PROVIDERS = ["mock", "openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];
const QUERY_LLM_PROVIDERS = MODEL_PROVIDERS.filter((provider) => provider !== "mock");
const CREDENTIAL_PROVIDERS = ["openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm", "zhipu", "zhipuai"];
const QUERY_ENHANCEMENT_STRATEGIES = ["rewrite", "hyde", "multi_query"];

type SelectedApp = AppItem | PublishedAppItem;

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

type WorkflowNode = Record<string, unknown>;

type WorkflowEdge = {
  source: string;
  target: string;
};

type AuthAction = "login" | "register";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isFullApp(app: SelectedApp | null): app is AppItem {
  return Boolean(app && "workflow_spec" in app);
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

function getWorkflowNodes(app: AppItem): WorkflowNode[] {
  const nodes = app.workflow_spec.nodes;
  return Array.isArray(nodes) ? nodes.filter(isRecord) : [];
}

function getWorkflowEdges(app: AppItem): WorkflowEdge[] {
  const edges = app.workflow_spec.edges;
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

function getAgentNodeModel(app: AppItem): AgentNodeModel {
  const node = getWorkflowNodes(app).find((item) => {
    const type = getWorkflowNodeType(item);
    return item.id === "agent" || type === "agent" || type === "react_agent";
  });
  return isRecord(node?.model) ? (node.model as AgentNodeModel) : {};
}

function getRetrievalNode(app: AppItem): WorkflowNode {
  return getWorkflowNodes(app).find((item) => isRetrievalNode(item)) ?? {};
}

function updateAgentNodeModel(app: AppItem, key: keyof AgentNodeModel, value: string | number): AppItem {
  const spec = app.workflow_spec ?? {};
  const nextNodes = getWorkflowNodes(app).map((item) => {
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
    ...app,
    workflow_spec: {
      ...spec,
      nodes: nextNodes,
    },
  };
}

function updateRetrievalNode(app: AppItem, key: keyof RetrievalNodeModel, value: string | number | boolean | string[]): AppItem {
  const spec = app.workflow_spec ?? {};
  const nextNodes = getWorkflowNodes(app).map((item) => {
    if (!isRetrievalNode(item)) return item;
    return { ...item, id: "retrieval", type: "retrieval", [key]: value };
  });
  return {
    ...app,
    workflow_spec: {
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
  const [publishedApps, setPublishedApps] = useState<PublishedAppItem[]>([]);
  const [selectedApp, setSelectedApp] = useState<SelectedApp | null>(null);
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
  const [appTools, setAppTools] = useState<AppTool[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const [selectedWorkflowNodeId, setSelectedWorkflowNodeId] = useState("");
  const [input, setInput] = useState("这份知识库里讲了什么？");
  const [trace, setTrace] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const selectedOwnedApp = isFullApp(selectedApp) && selectedApp.owner_user_id === user?.id ? selectedApp : null;
  const selectedKnowledgeBase = knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId) ?? null;
  const canEditSelectedApp = Boolean(selectedOwnedApp);
  const enabledToolNames = useMemo(
    () => appTools.filter((item) => item.enabled).map((item) => item.tool_name),
    [appTools],
  );
  const agentNodeModel = useMemo(() => (selectedOwnedApp ? getAgentNodeModel(selectedOwnedApp) : {}), [selectedOwnedApp]);
  const retrievalNode = useMemo(() => (selectedOwnedApp ? getRetrievalNode(selectedOwnedApp) : {}), [selectedOwnedApp]);
  const retrievalNodeModel = retrievalNode as RetrievalNodeModel;
  const workflowNodes = useMemo(() => (selectedOwnedApp ? getWorkflowNodes(selectedOwnedApp) : []), [selectedOwnedApp]);
  const workflowEdges = useMemo(() => (selectedOwnedApp ? getWorkflowEdges(selectedOwnedApp) : []), [selectedOwnedApp]);
  const selectedWorkflowNode = useMemo(() => {
    const node = workflowNodes.find((item, index) => getWorkflowNodeId(item, index) === selectedWorkflowNodeId);
    return node ?? workflowNodes[0] ?? null;
  }, [selectedWorkflowNodeId, workflowNodes]);
  const selectedWorkflowNodeType = selectedWorkflowNode ? getWorkflowNodeType(selectedWorkflowNode) : "";

  function setActiveConversationId(nextConversationId: string | null) {
    conversationIdRef.current = nextConversationId;
    setConversationId(nextConversationId);
  }

  function resetWorkspace() {
    setApps([]);
    setPublishedApps([]);
    setSelectedApp(null);
    setCredentials([]);
    setKnowledgeBases([]);
    setSelectedKnowledgeBaseId("");
    setKnowledgeDocuments([]);
    setTools([]);
    setAppTools([]);
    setRuns([]);
    setMessages([]);
    setTrace([]);
    setActiveConversationId(null);
    setSelectedWorkflowNodeId("");
    setBusy(false);
  }

  async function selectApp(app: SelectedApp | null) {
    setSelectedApp(app);
    setMessages([]);
    setTrace([]);
    setActiveConversationId(null);
    setStatusMessage("");
    if (!app) {
      setAppTools([]);
      setRuns([]);
      setSelectedWorkflowNodeId("");
      return;
    }
    if (!isFullApp(app) || app.owner_user_id !== user?.id) {
      setAppTools([]);
      setRuns([]);
      setSelectedWorkflowNodeId("");
      return;
    }

    const [appToolList, runList] = await Promise.all([api.listAppTools(app.id), api.listRuns(app.id)]);
    setAppTools(appToolList);
    setRuns(runList);
    const latestConversationId = runList[0]?.conversation_id ?? null;
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

  async function refresh(preferredAppId?: string | null) {
    if (!user) return;
    const [appList, publishedList, toolList, credentialList, kbList] = await Promise.all([
      api.listApps(),
      api.listPublishedApps(),
      api.listTools(),
      api.listModelCredentials(),
      api.listKnowledgeBases(),
    ]);
    setApps(appList);
    setPublishedApps(publishedList);
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
      publishedList.find((item) => item.id === preferredAppId) ??
      publishedList.find((item) => item.id === currentId) ??
      appList[0] ??
      publishedList[0] ??
      null;
    await selectApp(app);
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

  async function saveConfig() {
    if (!selectedOwnedApp) return;
    const updated = await api.updateApp(selectedOwnedApp.id, selectedOwnedApp);
    await api.updateAppTools(updated.id, enabledToolNames);
    await refresh(updated.id);
    setStatusMessage("配置已保存。");
  }

  async function publishSelectedApp() {
    if (!selectedOwnedApp) return;
    const updated = await api.publishApp(selectedOwnedApp.id);
    await refresh(updated.id);
    setStatusMessage("应用已发布。");
  }

  async function unpublishSelectedApp() {
    if (!selectedOwnedApp) return;
    const updated = await api.unpublishApp(selectedOwnedApp.id);
    await refresh(updated.id);
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
      if (!isFullApp(current)) return current;
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
    if (String(agentNodeModel.credential_id ?? "") === credentialId) {
      nextApp = updateAgentNodeModel(nextApp, "credential_id", "");
    }
    if (String(retrievalNodeModel.query_llm_credential_id ?? "") === credentialId) {
      nextApp = updateRetrievalNode(nextApp, "query_llm_credential_id", "");
    }
    setSelectedApp(nextApp);
  }

  async function toggleTool(toolName: string) {
    if (!selectedOwnedApp) return;
    const next = appTools.some((item) => item.tool_name === toolName && item.enabled)
      ? enabledToolNames.filter((name) => name !== toolName)
      : [...enabledToolNames, toolName];
    const updated = await api.updateAppTools(selectedOwnedApp.id, next);
    setAppTools(updated);
  }

  function updateRetrievalConfig(key: keyof RetrievalNodeModel, value: string | number | boolean | string[]) {
    if (!selectedOwnedApp) return;
    setSelectedApp(updateRetrievalNode(selectedOwnedApp, key, value));
  }

  function toggleKnowledgeBaseInNode(kbId: string) {
    const currentIds = Array.isArray(retrievalNodeModel.knowledge_base_ids) ? retrievalNodeModel.knowledge_base_ids : [];
    const nextIds = currentIds.includes(kbId) ? currentIds.filter((item) => item !== kbId) : [...currentIds, kbId];
    updateRetrievalConfig("knowledge_base_ids", nextIds);
  }

  function updateRetrievalQueryLlmProvider(provider: string) {
    if (!selectedOwnedApp) return;
    let nextApp = updateRetrievalNode(selectedOwnedApp, "query_llm_provider", provider);
    nextApp = updateRetrievalNode(nextApp, "query_llm_base_url", defaultQueryLlmBaseUrl(provider));
    setSelectedApp(nextApp);
  }

  async function sendMessage() {
    if (!selectedApp || !input.trim()) return;
    const query = input.trim();
    setInput("");
    setBusy(true);
    setTrace([]);
    setMessages((items) => [...items, { role: "user", content: query }, { role: "assistant", content: "" }]);

    try {
      await streamChat(selectedApp.id, query, conversationIdRef.current, (event, data) => {
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
      if (selectedOwnedApp) {
        setRuns(await api.listRuns(selectedOwnedApp.id));
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
    if (!selectedOwnedApp) return null;
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
    const selectedIds = Array.isArray(retrievalNodeModel.knowledge_base_ids) ? retrievalNodeModel.knowledge_base_ids : [];
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
    if (!selectedOwnedApp) return null;
    return (
      <>
        <label>
          模型提供方
          <select
            value={String(agentNodeModel.provider ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "provider", event.target.value))}
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
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "model_name", event.target.value))}
          />
        </label>
        <label>
          模型凭据
          <select
            value={String(agentNodeModel.credential_id ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "credential_id", event.target.value))}
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
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "base_url", event.target.value))}
          />
        </label>
        <div className="grid-two">
          <label>
            温度
            <input
              type="number"
              placeholder={String(selectedOwnedApp.temperature)}
              value={String(agentNodeModel.temperature ?? "")}
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "temperature", event.target.value))}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              placeholder={String(selectedOwnedApp.top_p)}
              value={String(agentNodeModel.top_p ?? "")}
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "top_p", event.target.value))}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            placeholder={String(selectedOwnedApp.max_tokens)}
            value={String(agentNodeModel.max_tokens ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "max_tokens", event.target.value))}
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
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "model_context_window", event.target.value))}
            />
          </label>
          <label>
            Safety
            <input
              type="number"
              placeholder="400"
              value={String(agentNodeModel.context_safety_margin ?? "")}
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "context_safety_margin", event.target.value))}
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
              setSelectedApp(updateAgentNodeModel(selectedOwnedApp, "context_reserved_output_tokens", event.target.value))
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
            <input type="checkbox" checked={enabledToolNames.includes(tool.name)} onChange={() => toggleTool(tool.name)} />
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
    if (!selectedOwnedApp || !selectedWorkflowNode) return null;
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
              <span>{app.status}</span>
            </button>
          ))}
          <div className="app-section-title">已发布应用</div>
          {publishedApps.map((app) => {
            const ownedApp = apps.find((item) => item.id === app.id);
            return (
              <button
                key={app.id}
                className={selectedApp?.id === app.id ? "app-item active" : "app-item"}
                onClick={() => selectApp(ownedApp ?? app)}
              >
                <strong>{app.name}</strong>
                <span>{app.owned ? "我发布的" : "可使用"}</span>
              </button>
            );
          })}
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
            {renderWorkflowCanvas()}
            {renderSelectedNodeSettings()}
            <div className="actions-row">
              <button className="secondary inline-button" onClick={saveConfig}>
                <Save size={16} /> 保存
              </button>
              {selectedOwnedApp?.status === "published" ? (
                <button className="secondary inline-button" onClick={unpublishSelectedApp}>
                  取消发布
                </button>
              ) : (
                <button className="secondary inline-button" onClick={publishSelectedApp}>
                  发布
                </button>
              )}
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
        <div className="composer">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") sendMessage();
            }}
          />
          <button className="primary" disabled={busy || !selectedApp} onClick={sendMessage}>
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
              <div className="run" key={run.id}>
                <strong>{run.status}</strong>
                <span>{run.latency_ms} ms</span>
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
