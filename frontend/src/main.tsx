import React, { useEffect, useMemo, useState } from "react";
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
  KnowledgeDocument,
  ModelCredential,
  RunItem,
  ToolItem,
  UserItem,
} from "./types";
import "./styles.css";

const MODEL_PROVIDERS = ["mock", "openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];
const CREDENTIAL_PROVIDERS = [
  "openai",
  "openai_compatible",
  "deepseek",
  "dashscope",
  "qwen",
  "vllm",
  "zhipu",
  "zhipuai",
  "jina",
  "cohere",
];
const EMBEDDING_PROVIDERS = ["openai", "openai_compatible", "zhipu", "zhipuai"];
const RERANK_PROVIDERS = ["passthrough", "jina", "cohere"];

type AgentNodeModel = {
  provider?: string;
  model_name?: string;
  credential_id?: string;
  base_url?: string;
  temperature?: string | number;
  top_p?: string | number;
  max_tokens?: string | number;
};

type RagNodeModel = {
  retrieval_top_k?: string | number;
  rerank_provider?: string;
  rerank_top_n?: string | number;
  rerank_credential_id?: string;
  rerank_model?: string;
  rerank_base_url?: string;
  embedding_provider?: string;
  embedding_model?: string;
  embedding_dimension?: string | number;
  embedding_credential_id?: string;
  embedding_base_url?: string;
  chunk_size?: string | number;
  chunk_overlap?: string | number;
  chunk_strategy?: string;
  enabled?: boolean;
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

function defaultCredentialProvider(provider: string) {
  return provider && provider !== "mock" ? provider : "openai_compatible";
}

function defaultEmbeddingConfig(provider: string) {
  if (provider === "zhipu" || provider === "zhipuai") {
    return {
      embedding_model: "embedding-3",
      embedding_dimension: 2048,
      embedding_base_url: "https://open.bigmodel.cn/api/paas/v4",
    };
  }
  return {
    embedding_model: "text-embedding-3-small",
    embedding_dimension: 1536,
    embedding_base_url: "https://api.openai.com/v1",
  };
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

function isRagNode(node: WorkflowNode) {
  const type = getWorkflowNodeType(node);
  return node.id === "rag" || type === "rag";
}

function getWorkflowNodeLabel(node: WorkflowNode, index: number) {
  const id = getWorkflowNodeId(node, index);
  const type = getWorkflowNodeType(node);
  if (id === "start" || type === "start") return "Start";
  if (id === "end" || type === "end") return "End";
  if (id === "agent" || type === "agent" || type === "react_agent") return "Agent";
  if (isRagNode(node)) return "RAG";
  return id;
}

function getAgentNodeModel(app: AppItem): AgentNodeModel {
  const node = getWorkflowNodes(app).find((item) => {
    const type = getWorkflowNodeType(item);
    return item.id === "agent" || type === "agent" || type === "react_agent";
  });
  return isRecord(node?.model) ? (node.model as AgentNodeModel) : {};
}

function getRagNode(app: AppItem): WorkflowNode {
  const node = getWorkflowNodes(app).find((item) => isRagNode(item));
  return node ?? {};
}

function updateAgentNodeModel(app: AppItem, key: keyof AgentNodeModel, value: string | number): AppItem {
  const spec = app.workflow_spec ?? {};
  const nodes = getWorkflowNodes(app);
  const nextNodes = nodes.map((item) => {
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

function updateRagNode(app: AppItem, key: keyof RagNodeModel, value: string | number | boolean | string[]): AppItem {
  const spec = app.workflow_spec ?? {};
  const nodes = getWorkflowNodes(app);
  const nextNodes = nodes.map((item) => {
    if (!isRagNode(item)) return item;
    return { ...item, id: "rag", type: "rag", [key]: value };
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
  const [selectedApp, setSelectedApp] = useState<AppItem | null>(null);
  const [credentials, setCredentials] = useState<ModelCredential[]>([]);
  const [runtimeKnowledgeDocuments, setRuntimeKnowledgeDocuments] = useState<KnowledgeDocument[]>([]);
  const [credentialDraft, setCredentialDraft] = useState({
    provider: "openai_compatible",
    name: "",
    api_key: "",
  });
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [appTools, setAppTools] = useState<AppTool[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selectedWorkflowNodeId, setSelectedWorkflowNodeId] = useState("");
  const [input, setInput] = useState("我的订单 10086 到哪了？");
  const [trace, setTrace] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);

  const enabledToolNames = useMemo(
    () => appTools.filter((item) => item.enabled).map((item) => item.tool_name),
    [appTools],
  );
  const agentNodeModel = useMemo(() => (selectedApp ? getAgentNodeModel(selectedApp) : {}), [selectedApp]);
  const ragNode = useMemo(() => (selectedApp ? getRagNode(selectedApp) : {}), [selectedApp]);
  const ragNodeModel = ragNode as RagNodeModel;
  const workflowNodes = useMemo(() => (selectedApp ? getWorkflowNodes(selectedApp) : []), [selectedApp]);
  const workflowEdges = useMemo(() => (selectedApp ? getWorkflowEdges(selectedApp) : []), [selectedApp]);
  const selectedWorkflowNode = useMemo(() => {
    const node = workflowNodes.find((item, index) => getWorkflowNodeId(item, index) === selectedWorkflowNodeId);
    return node ?? workflowNodes[0] ?? null;
  }, [selectedWorkflowNodeId, workflowNodes]);
  const selectedWorkflowNodeType = selectedWorkflowNode ? getWorkflowNodeType(selectedWorkflowNode) : "";
  const ragUploadEnabled = Boolean(ragNodeModel.enabled ?? true);

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
        return isRagNode(node) || id === "agent" || type === "react_agent";
      }) ?? workflowNodes[0];
    setSelectedWorkflowNodeId(getWorkflowNodeId(defaultNode, workflowNodes.indexOf(defaultNode)));
  }, [selectedWorkflowNodeId, workflowNodes]);

  useEffect(() => {
    if (!selectedApp || !conversationId) {
      setRuntimeKnowledgeDocuments([]);
      return;
    }
    api.listRuntimeRagDocuments(selectedApp.id, conversationId).then(setRuntimeKnowledgeDocuments).catch((error) => {
      console.error(error);
      setRuntimeKnowledgeDocuments([]);
    });
  }, [selectedApp?.id, conversationId]);

  function resetWorkspace() {
    setApps([]);
    setSelectedApp(null);
    setCredentials([]);
    setRuntimeKnowledgeDocuments([]);
    setTools([]);
    setAppTools([]);
    setRuns([]);
    setMessages([]);
    setTrace([]);
    setConversationId(null);
    setSelectedWorkflowNodeId("");
    setInput("我的订单 10086 到哪了？");
    setBusy(false);
  }

  async function selectApp(app: AppItem | null) {
    setSelectedApp(app);
    setMessages([]);
    setTrace([]);
    setConversationId(null);
    if (!app) {
      setAppTools([]);
      setRuns([]);
      setSelectedWorkflowNodeId("");
      setRuntimeKnowledgeDocuments([]);
      return;
    }
    const [appToolList, runList] = await Promise.all([
      api.listAppTools(app.id),
      api.listRuns(app.id),
    ]);
    setAppTools(appToolList);
    setRuns(runList);

    const latestConversationId = runList[0]?.conversation_id ?? null;
    setConversationId(latestConversationId);
    setRuntimeKnowledgeDocuments(latestConversationId ? await api.listRuntimeRagDocuments(app.id, latestConversationId) : []);
    if (!latestConversationId) return;

    const history = await api.listMessages(latestConversationId);
    setMessages(
      history
        .filter((message) => message.role === "user" || message.role === "assistant" || message.role === "system")
        .map((message) => ({
          role: message.role,
          content: message.content,
        })),
    );
  }

  async function refresh(preferredAppId?: string | null) {
    if (!user) return;
    const [appList, toolList, credentialList] = await Promise.all([
      api.listApps(),
      api.listTools(),
      api.listModelCredentials(),
    ]);
    setApps(appList);
    setTools(toolList);
    setCredentials(credentialList);
    const currentApp = selectedApp ? appList.find((item) => item.id === selectedApp.id) : null;
    const app = appList.find((item) => item.id === preferredAppId) ?? currentApp ?? appList[0] ?? null;
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
    if (!selectedApp) return;
    setCredentialDraft((draft) => ({
      ...draft,
      provider: defaultCredentialProvider(selectedApp.model_provider),
    }));
  }, [selectedApp?.id]);

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
    if (!selectedApp) return;
    const updated = await api.updateApp(selectedApp.id, selectedApp);
    setSelectedApp(updated);
    await api.updateAppTools(selectedApp.id, enabledToolNames);
    await refresh(updated.id);
  }

  async function createCredential() {
    const provider = credentialDraft.provider.trim();
    const apiKey = credentialDraft.api_key.trim();
    if (!provider || !apiKey) return;
    const name = credentialDraft.name.trim() || `${provider} credential`;
    const credential = await api.createModelCredential({
      provider,
      name,
      api_key: apiKey,
    });
    setCredentials(await api.listModelCredentials());
    setCredentialDraft({
      provider: defaultCredentialProvider(selectedApp?.model_provider ?? "openai_compatible"),
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
    if (!selectedApp) return;
    const needAppUpdate = selectedApp.model_credential_id === credentialId;
    const needNodeUpdate = String(agentNodeModel.credential_id ?? "") === credentialId;
    if (!needAppUpdate && !needNodeUpdate) return;

    let nextApp = selectedApp;
    if (needAppUpdate) nextApp = { ...nextApp, model_credential_id: "" };
    if (needNodeUpdate) nextApp = updateAgentNodeModel(nextApp, "credential_id", "");
    const saved = await api.updateApp(nextApp.id, nextApp);
    setSelectedApp(saved);
  }

  async function toggleTool(toolName: string) {
    if (!selectedApp) return;
    const next = appTools.some((item) => item.tool_name === toolName && item.enabled)
      ? enabledToolNames.filter((name) => name !== toolName)
      : [...enabledToolNames, toolName];
    const updated = await api.updateAppTools(selectedApp.id, next);
    setAppTools(updated);
  }

  async function uploadRuntimeRagFile(file: File | null) {
    if (!selectedApp || !file) return;
    const savedApp = await api.updateApp(selectedApp.id, selectedApp);
    setSelectedApp(savedApp);
    const result = await api.uploadRuntimeRagDocument(savedApp.id, conversationId, file);
    setConversationId(result.conversation_id);
    setRuntimeKnowledgeDocuments(await api.listRuntimeRagDocuments(savedApp.id, result.conversation_id));
    setTrace((items) => [
      ...items,
      { event: "runtime_rag_document_uploaded", filename: file.name, conversation_id: result.conversation_id },
    ]);
  }

  function updateRagConfig(key: keyof RagNodeModel, value: string | number | boolean | string[]) {
    if (!selectedApp) return;
    setSelectedApp(updateRagNode(selectedApp, key, value));
  }

  function updateRagEmbeddingProvider(provider: string) {
    if (!selectedApp) return;
    const defaults = defaultEmbeddingConfig(provider);
    let nextApp = updateRagNode(selectedApp, "embedding_provider", provider);
    nextApp = updateRagNode(nextApp, "embedding_model", defaults.embedding_model);
    nextApp = updateRagNode(nextApp, "embedding_dimension", defaults.embedding_dimension);
    nextApp = updateRagNode(nextApp, "embedding_base_url", defaults.embedding_base_url);
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
      await streamChat(selectedApp.id, query, conversationId, (event, data) => {
        if (event === "run_started") {
          setConversationId(String(data.conversation_id));
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
        if (event === "rag" || event === "tool_call" || event === "final" || event === "workflow_warning") {
          setTrace((items) => [...items, { event, ...data }]);
        }
      });
      setRuns(await api.listRuns(selectedApp.id));
    } finally {
      setBusy(false);
    }
  }

  function renderWorkflowNodeIcon(type: string) {
    if (type === "rag") return <Database size={16} />;
    if (type === "react_agent" || type === "agent") return <Bot size={16} />;
    return <Play size={16} />;
  }

  function renderWorkflowNodeSummary(node: WorkflowNode) {
    const type = getWorkflowNodeType(node);
    if (isRagNode(node)) {
      if (!Boolean(node.enabled ?? true)) return "已停用";
      return "Playground 上传文件";
    }
    if (type === "react_agent" || type === "agent") {
      const model = isRecord(node.model) ? node.model : {};
      return String(model.model_name ?? selectedApp?.model_name ?? "继承 App 模型");
    }
    if (type === "start") return "接收用户输入";
    if (type === "end") return "结束并返回结果";
    return type;
  }

  function renderWorkflowCanvas() {
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
              <React.Fragment key={`${id}-${index}`}>
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
              </React.Fragment>
            );
          })}
        </div>
      </section>
    );
  }

  function renderAppSettings() {
    if (!selectedApp) return null;
    return (
      <section className="panel">
        <div className="panel-title">
          <Wrench size={16} /> 应用设置
        </div>
        <label>
          名称
          <input value={selectedApp.name} onChange={(event) => setSelectedApp({ ...selectedApp, name: event.target.value })} />
        </label>
        <label>
          描述
          <textarea
            rows={2}
            value={selectedApp.description}
            onChange={(event) => setSelectedApp({ ...selectedApp, description: event.target.value })}
          />
        </label>
        <label>
          System Prompt
          <textarea
            rows={4}
            value={selectedApp.system_prompt}
            onChange={(event) => setSelectedApp({ ...selectedApp, system_prompt: event.target.value })}
          />
        </label>
      </section>
    );
  }

  function renderRagNodeSettings() {
    return (
      <>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(ragNodeModel.enabled ?? true)}
            onChange={(event) => updateRagConfig("enabled", event.target.checked)}
          />
          <span>
            <strong>启用 RAG</strong>
            <small>关闭后 workflow 会跳过该节点</small>
          </span>
        </label>
        <div className="grid-two">
          <label>
            召回 Top-K
            <input
              type="number"
              value={Number(ragNodeModel.retrieval_top_k ?? 20)}
              onChange={(event) => updateRagConfig("retrieval_top_k", Number(event.target.value))}
            />
          </label>
          <label>
            返回 Top-N
            <input
              type="number"
              value={Number(ragNodeModel.rerank_top_n ?? 5)}
              onChange={(event) => updateRagConfig("rerank_top_n", Number(event.target.value))}
            />
          </label>
        </div>
        <label>
          Rerank Provider
          <select
            value={String(ragNodeModel.rerank_provider ?? "passthrough")}
            onChange={(event) => updateRagConfig("rerank_provider", event.target.value)}
          >
            {RERANK_PROVIDERS.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          Rerank 凭据
          <select
            value={String(ragNodeModel.rerank_credential_id ?? "")}
            onChange={(event) => updateRagConfig("rerank_credential_id", event.target.value)}
          >
            <option value="">未选择</option>
            {credentials.map((credential) => (
              <option key={credential.id} value={credential.id}>
                {credential.name} · {credential.provider} · {credential.masked_api_key}
              </option>
            ))}
          </select>
        </label>
        <label>
          Rerank 模型
          <input
            placeholder="jina-reranker-v2 / rerank-multilingual-v3.0"
            value={String(ragNodeModel.rerank_model ?? "")}
            onChange={(event) => updateRagConfig("rerank_model", event.target.value)}
          />
        </label>
        <label>
          Rerank Base URL
          <input
            placeholder="默认使用 Jina / Cohere 官方地址"
            value={String(ragNodeModel.rerank_base_url ?? "")}
            onChange={(event) => updateRagConfig("rerank_base_url", event.target.value)}
          />
        </label>
        <div className="sub-panel-title">Embedding</div>
        <div className="grid-two">
          <label>
            提供方
            <select
              value={String(ragNodeModel.embedding_provider ?? "")}
              onChange={(event) => updateRagEmbeddingProvider(event.target.value)}
            >
              {EMBEDDING_PROVIDERS.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
          </label>
          <label>
            维度
            <input
              type="number"
              value={Number(ragNodeModel.embedding_dimension ?? 0)}
              onChange={(event) => updateRagConfig("embedding_dimension", Number(event.target.value))}
            />
          </label>
        </div>
        <label>
          Embedding 模型
          <input
            value={String(ragNodeModel.embedding_model ?? "")}
            onChange={(event) => updateRagConfig("embedding_model", event.target.value)}
          />
        </label>
        <label>
          Embedding 凭据
          <select
            value={String(ragNodeModel.embedding_credential_id ?? "")}
            onChange={(event) => updateRagConfig("embedding_credential_id", event.target.value)}
          >
            <option value="">未选择</option>
            {credentials.map((credential) => (
              <option key={credential.id} value={credential.id}>
                {credential.name} · {credential.provider} · {credential.masked_api_key}
              </option>
            ))}
          </select>
        </label>
        <label>
          Embedding Base URL
          <input
            value={String(ragNodeModel.embedding_base_url ?? "")}
            onChange={(event) => updateRagConfig("embedding_base_url", event.target.value)}
          />
        </label>
        <div className="grid-two">
          <label>
            Chunk Size
            <input
              type="number"
              value={Number(ragNodeModel.chunk_size ?? 512)}
              onChange={(event) => updateRagConfig("chunk_size", Number(event.target.value))}
            />
          </label>
          <label>
            Overlap
            <input
              type="number"
              value={Number(ragNodeModel.chunk_overlap ?? 64)}
              onChange={(event) => updateRagConfig("chunk_overlap", Number(event.target.value))}
            />
          </label>
        </div>
      </>
    );
  }

  function renderAgentNodeSettings() {
    if (!selectedApp) return null;
    return (
      <>
        <label>
          模型提供方
          <select
            value={String(agentNodeModel.provider ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "provider", event.target.value))}
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
            placeholder={selectedApp.model_name}
            value={String(agentNodeModel.model_name ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "model_name", event.target.value))}
          />
        </label>
        <label>
          模型凭据
          <select
            value={String(agentNodeModel.credential_id ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "credential_id", event.target.value))}
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
            placeholder={selectedApp.model_base_url || "https://api.openai.com/v1"}
            value={String(agentNodeModel.base_url ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "base_url", event.target.value))}
          />
        </label>
        <div className="grid-two">
          <label>
            温度
            <input
              type="number"
              placeholder={String(selectedApp.temperature)}
              value={String(agentNodeModel.temperature ?? "")}
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "temperature", event.target.value))}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              placeholder={String(selectedApp.top_p)}
              value={String(agentNodeModel.top_p ?? "")}
              onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "top_p", event.target.value))}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            placeholder={String(selectedApp.max_tokens)}
            value={String(agentNodeModel.max_tokens ?? "")}
            onChange={(event) => setSelectedApp(updateAgentNodeModel(selectedApp, "max_tokens", event.target.value))}
          />
        </label>
        <div className="sub-panel-title">App 默认模型</div>
        <label>
          模型提供方
          <select
            value={selectedApp.model_provider}
            onChange={(event) => {
              const provider = event.target.value;
              setSelectedApp({ ...selectedApp, model_provider: provider });
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
          <input value={selectedApp.model_name} onChange={(event) => setSelectedApp({ ...selectedApp, model_name: event.target.value })} />
        </label>
        <label>
          模型凭据
          <select
            value={selectedApp.model_credential_id}
            onChange={(event) => setSelectedApp({ ...selectedApp, model_credential_id: event.target.value })}
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
            value={selectedApp.model_base_url}
            onChange={(event) => setSelectedApp({ ...selectedApp, model_base_url: event.target.value })}
          />
        </label>
        <div className="grid-two">
          <label>
            温度
            <input
              type="number"
              value={selectedApp.temperature}
              onChange={(event) => setSelectedApp({ ...selectedApp, temperature: Number(event.target.value) })}
            />
          </label>
          <label>
            Top P
            <input
              type="number"
              value={selectedApp.top_p}
              onChange={(event) => setSelectedApp({ ...selectedApp, top_p: Number(event.target.value) })}
            />
          </label>
        </div>
        <label>
          Max Tokens
          <input
            type="number"
            value={selectedApp.max_tokens}
            onChange={(event) => setSelectedApp({ ...selectedApp, max_tokens: Number(event.target.value) })}
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
    if (!selectedApp || !selectedWorkflowNode) return null;
    const nodeIndex = workflowNodes.indexOf(selectedWorkflowNode);
    const title = getWorkflowNodeLabel(selectedWorkflowNode, Math.max(nodeIndex, 0));
    const id = getWorkflowNodeId(selectedWorkflowNode, Math.max(nodeIndex, 0));
    const selectedIsRagNode = isRagNode(selectedWorkflowNode);
    const isAgentNode = id === "agent" || selectedWorkflowNodeType === "agent" || selectedWorkflowNodeType === "react_agent";
    return (
      <section className="panel node-inspector">
        <div className="panel-title">
          {renderWorkflowNodeIcon(selectedWorkflowNodeType)} {title} 节点
        </div>
        <div className="node-meta">
          <span>{id}</span>
          <span>{selectedWorkflowNodeType}</span>
        </div>
        {selectedIsRagNode ? renderRagNodeSettings() : null}
        {isAgentNode ? renderAgentNodeSettings() : null}
        {!selectedIsRagNode && !isAgentNode ? (
          <div className="readonly-node">
            <strong>{renderWorkflowNodeSummary(selectedWorkflowNode)}</strong>
            <small>当前节点只从后端 workflow 定义展示，暂时没有可编辑配置。</small>
          </div>
        ) : null}
      </section>
    );
  }

  function renderPlaygroundRuntimeFiles() {
    return (
      <div className="playground-rag-files">
        <label className="upload upload-inline">
          <FileUp size={16} />
          上传会话文件
          <input
            type="file"
            accept=".txt,.md,.py,.js,.jsx,.ts,.tsx,.java,.go,.json,.yaml,.yml,.csv,.html,.css,.pdf,.docx"
            disabled={!selectedApp || !ragUploadEnabled || busy}
            onChange={(event) => uploadRuntimeRagFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {runtimeKnowledgeDocuments.length ? (
          <div className="runtime-doc-list">
            {runtimeKnowledgeDocuments.map((document) => (
              <div className="runtime-doc-item" key={document.id}>
                <strong>{document.filename}</strong>
                <span>
                  {document.status}
                  {document.error ? ` · ${document.error}` : ""}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
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
              <p>AgentScope MVP Demo</p>
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
            <p>AgentScope MVP Demo</p>
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
          <Play size={16} /> 创建电商客服 Agent
        </button>
        <div className="app-list">
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
        </div>
      </aside>

      <section className="config">
        <header>
          <GitBranch size={18} />
          <h2>Workflow 配置</h2>
        </header>

        <section className="panel">
          <div className="panel-title">
            <KeyRound size={16} /> 模型凭据
          </div>
          <div className="grid-two">
            <label>
              提供方
              <select
                value={credentialDraft.provider}
                onChange={(event) => setCredentialDraft({ ...credentialDraft, provider: event.target.value })}
              >
                {CREDENTIAL_PROVIDERS.map((provider) => (
                  <option key={provider} value={provider}>
                    {provider}
                  </option>
                ))}
              </select>
            </label>
            <label>
              名称
              <input
                value={credentialDraft.name}
                onChange={(event) => setCredentialDraft({ ...credentialDraft, name: event.target.value })}
              />
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
                  credential.id === selectedApp?.model_credential_id ||
                  credential.id === String(agentNodeModel.credential_id ?? "")
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

        {selectedApp ? (
          <>
            {renderAppSettings()}
            {renderWorkflowCanvas()}
            {renderSelectedNodeSettings()}
            <button className="secondary" onClick={saveConfig}>
              <Save size={16} /> 保存配置
            </button>
          </>
        ) : (
          <p className="empty">先创建一个 Agent 应用。</p>
        )}
      </section>

      <section className="chat">
        <header>
          <MessageSquare size={18} />
          <h2>Playground</h2>
        </header>
        <div className="messages">
          {messages.length === 0 ? (
            <div className="empty">试试知识库问答、订单查询或工具调用。</div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))
          )}
        </div>
        {renderPlaygroundRuntimeFiles()}
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
        <section className="panel">
          <div className="panel-title">最近 Runs</div>
          {runs.map((run) => (
            <div className="run" key={run.id}>
              <strong>{run.status}</strong>
              <span>{run.latency_ms} ms</span>
            </div>
          ))}
        </section>
      </aside>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
