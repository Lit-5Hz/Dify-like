import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Database,
  FileUp,
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
import type { AppItem, AppTool, ChatMessage, ModelCredential, RunItem, ToolItem, UserItem } from "./types";
import "./styles.css";

const MODEL_PROVIDERS = ["mock", "openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];
const CREDENTIAL_PROVIDERS = ["openai", "openai_compatible", "deepseek", "dashscope", "qwen", "vllm"];

type AgentNodeModel = {
  provider?: string;
  model_name?: string;
  credential_id?: string;
  base_url?: string;
  temperature?: string | number;
  top_p?: string | number;
  max_tokens?: string | number;
};

type AuthAction = "login" | "register";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function defaultCredentialProvider(provider: string) {
  return provider && provider !== "mock" ? provider : "openai_compatible";
}

function getAgentNodeModel(app: AppItem): AgentNodeModel {
  const spec = app.workflow_spec as Record<string, unknown>;
  const nodesRaw = spec.nodes;
  const nodes = Array.isArray(nodesRaw) ? nodesRaw : [];
  const node = nodes.find((item) => {
    if (!isRecord(item)) return false;
    const type = String(item.type ?? "");
    return item.id === "agent" || type === "agent" || type === "react_agent";
  });
  if (!isRecord(node) || !isRecord(node.model)) return {};
  return node.model as AgentNodeModel;
}

function updateAgentNodeModel(app: AppItem, key: keyof AgentNodeModel, value: string | number): AppItem {
  const spec = (app.workflow_spec ?? {}) as Record<string, unknown>;
  const nodesRaw = spec.nodes;
  const nodes = Array.isArray(nodesRaw) ? nodesRaw : [];
  const nextNodes = nodes.map((item) => {
    if (!isRecord(item)) return item;
    const type = String(item.type ?? "");
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

function App() {
  const [user, setUser] = useState<UserItem | null>(null);
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [apps, setApps] = useState<AppItem[]>([]);
  const [selectedApp, setSelectedApp] = useState<AppItem | null>(null);
  const [credentials, setCredentials] = useState<ModelCredential[]>([]);
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
  const [input, setInput] = useState("我的订单 10086 到哪了？");
  const [trace, setTrace] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);

  const enabledToolNames = useMemo(
    () => appTools.filter((item) => item.enabled).map((item) => item.tool_name),
    [appTools],
  );
  const agentNodeModel = useMemo(() => (selectedApp ? getAgentNodeModel(selectedApp) : {}), [selectedApp]);

  function resetWorkspace() {
    setApps([]);
    setSelectedApp(null);
    setCredentials([]);
    setCredentialDraft({
      provider: "openai_compatible",
      name: "",
      api_key: "",
    });
    setTools([]);
    setAppTools([]);
    setRuns([]);
    setMessages([]);
    setTrace([]);
    setConversationId(null);
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
      return;
    }
    const [appToolList, runList] = await Promise.all([api.listAppTools(app.id), api.listRuns(app.id)]);
    setAppTools(appToolList);
    setRuns(runList);
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
        if (!cancelled) {
          setUser(currentUser);
        }
      } catch {
        api.setAuthToken("");
      } finally {
        if (!cancelled) {
          setAuthLoading(false);
        }
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

  useEffect(() => {
    if (!selectedApp) return;
    setCredentialDraft((draft) => ({
      ...draft,
      provider: defaultCredentialProvider(selectedApp.model_provider),
    }));
  }, [selectedApp?.id]);

  async function createDemoApp() {
    const app = await api.createApp();
    await refresh(app.id);
  }

  async function saveConfig() {
    if (!selectedApp) return;
    const updated = await api.updateApp(selectedApp.id, selectedApp);
    setSelectedApp(updated);
    await api.updateAppTools(selectedApp.id, enabledToolNames);
    await refresh();
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
    if (needAppUpdate) {
      nextApp = { ...nextApp, model_credential_id: "" };
    }
    if (needNodeUpdate) {
      nextApp = updateAgentNodeModel(nextApp, "credential_id", "");
    }
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
        if (event === "retrieval" || event === "tool_call" || event === "final" || event === "workflow_warning") {
          setTrace((items) => [...items, { event, ...data }]);
        }
      });
      if (selectedApp) {
        setRuns(await api.listRuns(selectedApp.id));
      }
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File | null) {
    if (!selectedApp || !file) return;
    await api.uploadDocument(selectedApp.id, file);
    setTrace((items) => [...items, { event: "document_uploaded", filename: file.name }]);
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
              onClick={async () => {
                await selectApp(app);
              }}
            >
              <strong>{app.name}</strong>
              <span>{app.status}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="config">
        <header>
          <Wrench size={18} />
          <h2>Agent 配置</h2>
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
            <label>
              名称
              <input
                value={selectedApp.name}
                onChange={(event) => setSelectedApp({ ...selectedApp, name: event.target.value })}
              />
            </label>
            <label>
              描述
              <textarea
                rows={3}
                value={selectedApp.description}
                onChange={(event) => setSelectedApp({ ...selectedApp, description: event.target.value })}
              />
            </label>
            <label>
              System Prompt
              <textarea
                rows={8}
                value={selectedApp.system_prompt}
                onChange={(event) => setSelectedApp({ ...selectedApp, system_prompt: event.target.value })}
              />
            </label>

            <section className="panel">
              <div className="panel-title">
                <Bot size={16} /> App 默认模型
              </div>
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
                <input
                  value={selectedApp.model_name}
                  onChange={(event) => setSelectedApp({ ...selectedApp, model_name: event.target.value })}
                />
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
            </section>

            <section className="panel">
              <div className="panel-title">
                <Bot size={16} /> Agent 节点模型
              </div>
              <label>
                模型提供方
                <select
                  value={String(agentNodeModel.provider ?? "")}
                  onChange={(event) =>
                    setSelectedApp(updateAgentNodeModel(selectedApp, "provider", event.target.value))
                  }
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
                  onChange={(event) =>
                    setSelectedApp(updateAgentNodeModel(selectedApp, "model_name", event.target.value))
                  }
                />
              </label>
              <label>
                模型凭据
                <select
                  value={String(agentNodeModel.credential_id ?? "")}
                  onChange={(event) =>
                    setSelectedApp(updateAgentNodeModel(selectedApp, "credential_id", event.target.value))
                  }
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
                  onChange={(event) =>
                    setSelectedApp(updateAgentNodeModel(selectedApp, "base_url", event.target.value))
                  }
                />
              </label>
              <div className="grid-two">
                <label>
                  温度
                  <input
                    type="number"
                    placeholder={String(selectedApp.temperature)}
                    value={String(agentNodeModel.temperature ?? "")}
                    onChange={(event) =>
                      setSelectedApp(updateAgentNodeModel(selectedApp, "temperature", event.target.value))
                    }
                  />
                </label>
                <label>
                  Top P
                  <input
                    type="number"
                    placeholder={String(selectedApp.top_p)}
                    value={String(agentNodeModel.top_p ?? "")}
                    onChange={(event) =>
                      setSelectedApp(updateAgentNodeModel(selectedApp, "top_p", event.target.value))
                    }
                  />
                </label>
              </div>
              <label>
                Max Tokens
                <input
                  type="number"
                  placeholder={String(selectedApp.max_tokens)}
                  value={String(agentNodeModel.max_tokens ?? "")}
                  onChange={(event) =>
                    setSelectedApp(updateAgentNodeModel(selectedApp, "max_tokens", event.target.value))
                  }
                />
              </label>
            </section>
            <button className="secondary" onClick={saveConfig}>
              <Save size={16} /> 保存配置
            </button>

            <section className="panel">
              <div className="panel-title">
                <Database size={16} /> 知识库
              </div>
              <label className="upload">
                <FileUp size={16} />
                上传 .txt / .md
                <input type="file" accept=".txt,.md" onChange={(event) => upload(event.target.files?.[0] ?? null)} />
              </label>
            </section>

            <section className="panel">
              <div className="panel-title">
                <Wrench size={16} /> 工具
              </div>
              {tools.map((tool) => (
                <label className="check" key={tool.name}>
                  <input
                    type="checkbox"
                    checked={enabledToolNames.includes(tool.name)}
                    onChange={() => toggleTool(tool.name)}
                  />
                  <span>
                    <strong>{tool.label}</strong>
                    <small>{tool.description}</small>
                  </span>
                </label>
              ))}
            </section>
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
            <div className="empty">试试订单查询和退货政策问答。</div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                {message.content}
              </div>
            ))
          )}
        </div>
        <div className="composer">
          <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter") sendMessage();
          }} />
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
