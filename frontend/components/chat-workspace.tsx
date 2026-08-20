"use client";

import Link from "next/link";
import {useRouter} from "next/navigation";
import {FormEvent, KeyboardEvent, useEffect, useRef, useState} from "react";

import {AnalyticsResult} from "@/components/analytics-result";
import {
  createConversation, deleteConversation, getConversation, listConnectionProfiles,
  listConversations, submitConversationMessage, updateConversation,
  type ConnectionProfile, type Conversation, type ConversationMessage,
  type ConversationSummary, type ResponseLanguage,
} from "@/lib/api";

const CONNECTION_KEY = "prompt2insight.selected-connection";

export function ChatWorkspace({conversationId}: {conversationId?: string}) {
  const router = useRouter();
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [connectionId, setConnectionId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [language, setLanguage] = useState<ResponseLanguage>("auto");
  const [loading, setLoading] = useState(Boolean(conversationId));
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drafts = useRef(new Map<string, string>());
  const inFlight = useRef(false);
  const retry = useRef<{content: string; id: string} | null>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const loadVersion = useRef(0);

  const refreshHistory = async () => setHistory((await listConversations()).items);
  useEffect(() => { void refreshHistory().catch((caught: unknown) => setError(message(caught))); }, []);
  useEffect(() => {
    void listConnectionProfiles().then((items) => {
      setProfiles(items);
      const stored = window.localStorage.getItem(CONNECTION_KEY);
      const selected = items.find((item) => item.id === stored && item.state === "ready") ?? items.find((item) => item.state === "ready");
      if (selected) setConnectionId(selected.id);
    }).catch((caught: unknown) => setError(message(caught)));
  }, []);
  useEffect(() => {
    const version = ++loadVersion.current;
    if (!conversationId) { setConversation(null); setLoading(false); setError(null); setDraft(drafts.current.get("new") ?? ""); return; }
    setLoading(true); setError(null); setDraft(drafts.current.get(conversationId) ?? "");
    void getConversation(conversationId).then((item) => { if (loadVersion.current === version) { setConversation(item); setLanguage(item.language); } }).catch((caught: unknown) => { if (loadVersion.current === version) setError(message(caught)); }).finally(() => { if (loadVersion.current === version) setLoading(false); });
  }, [conversationId]);
  useEffect(() => { if (conversation && nearBottom.current) threadEnd.current?.scrollIntoView({block: "end"}); }, [conversation?.messages.length]);

  const activeProfile = profiles.find((item) => item.id === (conversation?.connection_id ?? connectionId));
  const filtered = history.filter((item) => item.title.toLowerCase().includes(query.toLowerCase()));
  const activeKey = conversationId ?? "new";
  const saveDraft = (value: string) => { drafts.current.set(activeKey, value); setDraft(value); };
  const resize = (element: HTMLTextAreaElement) => { element.style.height = "auto"; element.style.height = `${Math.min(element.scrollHeight, 220)}px`; };

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const content = retry.current?.content ?? draft.trim();
    if (!content || inFlight.current) return;
    inFlight.current = true; setSending(true); setError(null);
    const clientMessageId = retry.current?.id ?? crypto.randomUUID();
    retry.current = {content, id: clientMessageId};
    try {
      let id = conversationId;
      if (!id) {
        if (!connectionId) throw new Error("Choose a ready connection before starting a chat.");
        const created = await createConversation({connectionId, language});
        id = created.id;
        drafts.current.set(id, content); drafts.current.delete("new");
        router.replace(`/chat/${id}`);
      }
      const result = await submitConversationMessage(id, {content, clientMessageId});
      const messages = [result.user_message, ...(result.assistant_message ? [result.assistant_message] : [])];
      setConversation((current) => current?.id === id ? {...current, messages: mergeMessages(current.messages, messages)} : current);
      saveDraft(""); retry.current = null;
      await refreshHistory();
    } catch (caught) { setError(message(caught)); }
    finally { inFlight.current = false; setSending(false); }
  }
  async function rename(item: ConversationSummary) {
    const title = window.prompt("Rename conversation", item.title)?.trim();
    if (!title || title === item.title) return;
    try { await updateConversation(item.id, {title}); await refreshHistory(); if (conversation?.id === item.id) setConversation({...conversation, title}); } catch (caught) { setError(message(caught)); }
  }
  async function archive(item: ConversationSummary) {
    if (!window.confirm(`Archive “${item.title}”?`)) return;
    try { await updateConversation(item.id, {archived: true}); await refreshHistory(); if (item.id === conversationId) router.replace("/chat/new"); } catch (caught) { setError(message(caught)); }
  }
  async function remove(item: ConversationSummary) {
    if (!window.confirm(`Delete “${item.title}”? This cannot be undone.`)) return;
    try { await deleteConversation(item.id); await refreshHistory(); if (item.id === conversationId) router.replace("/chat/new"); } catch (caught) { setError(message(caught)); }
  }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } };

  return <div className={`chat-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
    <aside className={`chat-sidebar ${drawerOpen ? "drawer-open" : ""}`} aria-label="Conversation history">
      <div className="sidebar-top"><button className="secondary collapse-button" type="button" aria-label="Collapse sidebar" onClick={() => setCollapsed((value) => !value)}>☰</button><Link className="new-chat" href="/chat/new" onClick={() => setDrawerOpen(false)}>+ New chat</Link></div>
      <input aria-label="Search conversations" placeholder="Search conversations" value={query} onChange={(event) => setQuery(event.target.value)} />
      <nav className="conversation-history">{filtered.map((item) => <div className={`history-item ${item.id === conversationId ? "active" : ""}`} key={item.id}><Link href={`/chat/${item.id}`} onClick={() => setDrawerOpen(false)}>{item.title}</Link><details><summary aria-label={`Actions for ${item.title}`}>•••</summary><div className="history-actions"><button type="button" onClick={() => void rename(item)}>Rename</button><button type="button" onClick={() => void archive(item)}>Archive</button><button type="button" className="danger-button" onClick={() => void remove(item)}>Delete</button></div></details></div>)}</nav>
      <div className="sidebar-bottom"><Link href="/">Settings & connections</Link><label>Language<select value={language} onChange={(event) => setLanguage(event.target.value as ResponseLanguage)}><option value="auto">Auto</option><option value="en">English</option><option value="ar">العربية</option></select></label></div>
    </aside>
    {drawerOpen ? <button className="drawer-backdrop" aria-label="Close sidebar" onClick={() => setDrawerOpen(false)} /> : null}
    <main className="chat-main"><header className="chat-header"><button className="secondary mobile-menu" type="button" aria-label="Open sidebar" onClick={() => setDrawerOpen(true)}>☰</button><div><strong>{conversation?.title ?? "New chat"}</strong><small>{activeProfile ? `${activeProfile.name} · ${activeProfile.state}` : "No active connection"}</small></div><select aria-label="Active connection" value={connectionId} onChange={(event) => { setConnectionId(event.target.value); window.localStorage.setItem(CONNECTION_KEY, event.target.value); }} disabled={Boolean(conversation)}>{profiles.filter((item) => item.state === "ready").map((item) => <option key={item.id} value={item.id}>{item.name} · {item.state}</option>)}</select></header>
      <section className="message-thread" aria-live="polite" onScroll={(event) => { const element = event.currentTarget; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96; }}>{loading ? <p>Loading conversation…</p> : null}{error && !conversation ? <div className="chat-error"><p>Conversation unavailable: {error}</p><Link href="/chat/new">Start a new chat</Link></div> : null}{!loading && !error && !conversation ? <div className="chat-empty"><h1>What would you like to know?</h1><p>Start a conversation with your connected data.</p></div> : null}{conversation?.messages.map((item) => <MessageBubble key={item.id} item={item} onRetry={() => void send()} />)}{error && conversation ? <div className="chat-error" role="alert">{error}<button type="button" className="secondary" onClick={() => void send()}>Retry</button></div> : null}<div ref={threadEnd} /></section>
      <form className="chat-composer" onSubmit={send}><textarea aria-label="Message" placeholder="Ask a data question" value={draft} disabled={sending} onChange={(event) => { saveDraft(event.target.value); resize(event.currentTarget); }} onKeyDown={onKeyDown} /><button disabled={sending || !draft.trim()}>{sending ? "Working…" : "Send"}</button></form>
    </main>
  </div>;
}

function MessageBubble({item, onRetry}: {item: ConversationMessage; onRetry: () => void}) {
  return <article className={`message message-${item.role}`}><div className="message-content">{item.content}</div>{item.role === "assistant" && item.metadata.analytics ? <AnalyticsResult result={item.metadata.analytics} onRetry={onRetry} /> : null}{item.metadata.status === "failed" ? <small className="error">Analysis failed. <button type="button" className="secondary" onClick={onRetry}>Retry</button></small> : null}</article>;
}

function mergeMessages(current: ConversationMessage[], added: ConversationMessage[]) { return [...current, ...added.filter((item) => !current.some((existing) => existing.id === item.id))].sort((left, right) => left.sequence_number - right.sequence_number); }
function message(error: unknown) { return error instanceof Error ? error.message : "The request could not be completed."; }
