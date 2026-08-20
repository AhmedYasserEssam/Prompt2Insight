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
import {useDocumentLanguage} from "@/lib/document-language";

const CONNECTION_KEY = "prompt2insight.selected-connection";
const chatCopy = {
  en: {
    newChat: "New chat", conversations: "Conversations", search: "Search conversations", language: "Language", settings: "Settings & connections", message: "Message", placeholder: "Ask anything about your data…", send: "Send message", working: "Analyzing your data…", openSidebar: "Open sidebar", closeSidebar: "Close sidebar", collapseSidebar: "Collapse sidebar", loading: "Loading conversation…", unavailable: "Conversation unavailable", startNew: "Start a new chat", emptyTitle: "What would you like to explore?", emptyText: "Ask a question in plain language and get an answer, chart, and the data behind it.", noConnection: "No active connection", connected: "Connected", connectFirst: "Connect a data source to start asking questions", composerHint: "Enter to send · Shift + Enter for a new line", retry: "Retry", failed: "Analysis failed.", assistant: "Prompt2Insight", you: "You", suggestions: ["What are my top-selling products?", "Show me the latest sales trend", "Compare this month with last month"],
  },
  ar: {
    newChat: "محادثة جديدة", conversations: "المحادثات", search: "البحث في المحادثات", language: "اللغة", settings: "الإعدادات والاتصالات", message: "الرسالة", placeholder: "اسأل أي شيء عن بياناتك…", send: "إرسال الرسالة", working: "جارٍ تحليل بياناتك…", openSidebar: "فتح الشريط الجانبي", closeSidebar: "إغلاق الشريط الجانبي", collapseSidebar: "طي الشريط الجانبي", loading: "جارٍ تحميل المحادثة…", unavailable: "المحادثة غير متاحة", startNew: "بدء محادثة جديدة", emptyTitle: "ما الذي تريد استكشافه؟", emptyText: "اطرح سؤالك بلغة بسيطة واحصل على إجابة ورسم بياني والبيانات الداعمة.", noConnection: "لا يوجد اتصال نشط", connected: "متصل", connectFirst: "اربط مصدر بيانات لبدء طرح الأسئلة", composerHint: "Enter للإرسال · Shift + Enter لسطر جديد", retry: "إعادة المحاولة", failed: "فشل التحليل.", assistant: "Prompt2Insight", you: "أنت", suggestions: ["ما المنتجات الأكثر مبيعاً؟", "اعرض أحدث اتجاه للمبيعات", "قارن هذا الشهر بالشهر الماضي"],
  },
};

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
  const drawer = useRef<HTMLElement>(null);
  const drawerTrigger = useRef<HTMLButtonElement>(null);
  const nearBottom = useRef(true);
  const loadVersion = useRef(0);
  const resolvedLanguage = [...(conversation?.messages ?? [])].reverse().find((item) => item.metadata.analytics)?.metadata.analytics?.language;
  const activeLanguage = conversation?.language === "ar" || resolvedLanguage === "ar" || (!conversation && language === "ar") ? "ar" : "en";
  const t = chatCopy[activeLanguage];
  useDocumentLanguage(activeLanguage);

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
  useEffect(() => {
    if (!drawerOpen) return;
    const focusable = () => [...(drawer.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), summary') ?? [])].filter((element) => element.offsetParent !== null);
    focusable()[0]?.focus();
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setDrawerOpen(false); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0]; const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);
  useEffect(() => { if (!drawerOpen) drawerTrigger.current?.focus(); }, [drawerOpen]);

  const activeProfile = profiles.find((item) => item.id === (conversation?.connection_id ?? connectionId));
  const readyProfiles = profiles.filter((item) => item.state === "ready");
  const canQuery = activeProfile?.state === "ready";
  const filtered = history.filter((item) => item.title.toLowerCase().includes(query.toLowerCase()));
  const activeKey = conversationId ?? "new";
  const saveDraft = (value: string) => { drafts.current.set(activeKey, value); setDraft(value); };
  const resize = (element: HTMLTextAreaElement) => { element.style.height = "auto"; element.style.height = `${Math.min(element.scrollHeight, 220)}px`; };

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const content = retry.current?.content ?? draft.trim();
    if (!content || inFlight.current) return;
    if (!canQuery) { setError(t.connectFirst); return; }
    inFlight.current = true; setSending(true); setError(null);
    const clientMessageId = retry.current?.id ?? crypto.randomUUID();
    retry.current = {content, id: clientMessageId};
    try {
      let id = conversation?.id ?? conversationId;
      let createdConversation: Conversation | null = null;
      if (!id) {
        if (!connectionId) throw new Error("Choose a ready connection before starting a chat.");
        const created = await createConversation({connectionId, language});
        createdConversation = created;
        id = created.id;
        setConversation(created);
        drafts.current.set(id, content); drafts.current.delete("new");
      }
      const result = await submitConversationMessage(id, {content, clientMessageId});
      const messages = [result.user_message, ...(result.assistant_message ? [result.assistant_message] : [])];
      setConversation((current) => {
        const active = current?.id === id ? current : createdConversation;
        return active ? {...active, messages: mergeMessages(active.messages, messages)} : current;
      });
      drafts.current.delete("new"); drafts.current.delete(id); setDraft(""); retry.current = null;
      await refreshHistory();
      if (createdConversation) router.replace(`/chat/${id}`);
    } catch (caught) { setError(message(caught)); }
    finally { inFlight.current = false; setSending(false); }
  }
  function retryMessage(item: ConversationMessage) {
    retry.current = {content: item.content, id: crypto.randomUUID()};
    void send();
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
  async function changeLanguage(next: ResponseLanguage) {
    setLanguage(next);
    if (!conversation) return;
    try {
      const updated = await updateConversation(conversation.id, {language: next});
      setConversation(updated);
      await refreshHistory();
    } catch (caught) { setError(message(caught)); }
  }

  return <div className={`chat-shell ${collapsed ? "sidebar-collapsed" : ""}`} dir={activeLanguage === "ar" ? "rtl" : "ltr"} lang={activeLanguage}>
    <aside ref={drawer} className={`chat-sidebar ${drawerOpen ? "drawer-open" : ""}`} aria-label="Conversation history" aria-modal={drawerOpen || undefined} role={drawerOpen ? "dialog" : undefined}>
      <div className="sidebar-brand">
        <span className="brand-mark"><Icon name="spark" /></span>
        <span className="brand-copy"><strong>Prompt2Insight</strong><small>AI data analyst</small></span>
        <button className="sidebar-close" type="button" aria-label={t.closeSidebar} onClick={() => setDrawerOpen(false)}><Icon name="close" /></button>
      </div>
      <Link className="new-chat" href="/chat/new" onClick={() => setDrawerOpen(false)}><Icon name="plus" /><span>{t.newChat}</span></Link>
      <div className="sidebar-search"><Icon name="search" /><input aria-label={t.search} placeholder={t.search} value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <div className="history-section"><span className="sidebar-label">{t.conversations}</span><nav className="conversation-history">{filtered.map((item) => <div className={`history-item ${item.id === conversationId ? "active" : ""}`} key={item.id}><Link href={`/chat/${item.id}`} onClick={() => setDrawerOpen(false)}><Icon name="message" /><span>{item.title}</span></Link><details><summary aria-label={`Actions for ${item.title}`}>•••</summary><div className="history-actions"><button type="button" onClick={() => void rename(item)}>Rename</button><button type="button" onClick={() => void archive(item)}>Archive</button><button type="button" className="danger-button" onClick={() => void remove(item)}>Delete</button></div></details></div>)}</nav></div>
      <div className="sidebar-bottom"><Link href="/"><Icon name="settings" /><span>{t.settings}</span></Link><label><span>{t.language}</span><select value={language} onChange={(event) => void changeLanguage(event.target.value as ResponseLanguage)}><option value="auto">Auto</option><option value="en">English</option><option value="ar">العربية</option></select></label></div>
      <button className="collapse-button" type="button" aria-label={t.collapseSidebar} onClick={() => setCollapsed((value) => !value)}><Icon name="panel" /></button>
    </aside>
    {drawerOpen ? <button className="drawer-backdrop" aria-label={t.closeSidebar} onClick={() => setDrawerOpen(false)} /> : null}
    <main className="chat-main"><header className="chat-header"><button ref={drawerTrigger} className="mobile-menu" type="button" aria-label={t.openSidebar} aria-expanded={drawerOpen} onClick={() => setDrawerOpen(true)}><Icon name="menu" /></button><div className="chat-title"><strong>{conversation?.title ?? t.newChat}</strong><small><span className={`connection-dot ${canQuery ? "online" : ""}`} />{canQuery ? `${t.connected} · ${activeProfile.name}` : t.noConnection}</small></div><label className="header-connection"><Icon name="database" /><span className="sr-only">Active connection</span><select aria-label="Active connection" value={conversation?.connection_id ?? connectionId} onChange={(event) => { setConnectionId(event.target.value); window.localStorage.setItem(CONNECTION_KEY, event.target.value); }} disabled={Boolean(conversation)}>{readyProfiles.length === 0 ? <option value="">{t.noConnection}</option> : null}{readyProfiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></header>
      <section className="message-thread" aria-live="polite" aria-busy={loading || sending} onScroll={(event) => { const element = event.currentTarget; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96; }}>{loading ? <div className="chat-loading"><span className="message-avatar"><Icon name="spark" /></span><span>{t.loading}</span><span className="typing-dots"><i /><i /><i /></span></div> : null}{error && !conversation ? <div className="chat-error"><p>{t.unavailable}: {error}</p><Link href="/chat/new">{t.startNew}</Link></div> : null}{!loading && !error && !conversation ? <div className="chat-empty"><span className="empty-mark"><Icon name="spark" /></span><h1>{t.emptyTitle}</h1><p>{t.emptyText}</p><div className="prompt-suggestions">{t.suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => saveDraft(suggestion)}>{suggestion}<span aria-hidden="true">→</span></button>)}</div></div> : null}{conversation?.messages.map((item) => <MessageBubble key={item.id} item={item} onRetry={() => retryMessage(item)} retryLabel={t.retry} failedLabel={t.failed} assistantLabel={t.assistant} userLabel={t.you} language={activeLanguage} />)}{sending && conversation ? <div className="chat-loading"><span className="message-avatar"><Icon name="spark" /></span><span>{t.working}</span><span className="typing-dots"><i /><i /><i /></span></div> : null}{error && conversation ? <div className="chat-error" role="alert">{error}<button type="button" className="secondary" onClick={() => void send()}>{t.retry}</button></div> : null}<div ref={threadEnd} /></section>
      <form className="chat-composer" onSubmit={send}><div className="composer-box"><textarea rows={1} aria-label={t.message} aria-describedby={error ? "composer-error" : "composer-hint"} placeholder={canQuery ? t.placeholder : t.connectFirst} value={draft} disabled={sending} onChange={(event) => { saveDraft(event.target.value); resize(event.currentTarget); }} onKeyDown={onKeyDown} /><button className="send-button" aria-label={t.send} disabled={sending || !draft.trim() || !canQuery}>{sending ? <span className="button-spinner" /> : <Icon name="send" />}</button></div><div className="composer-meta"><span id="composer-hint">{canQuery ? t.composerHint : t.connectFirst}</span><span>{canQuery ? activeProfile.name : null}</span></div>{error ? <span id="composer-error" className="sr-only">{error}</span> : null}</form>
    </main>
  </div>;
}

function MessageBubble({item, onRetry, retryLabel, failedLabel, assistantLabel, userLabel, language: conversationLanguage}: {item: ConversationMessage; onRetry: () => void; retryLabel: string; failedLabel: string; assistantLabel: string; userLabel: string; language: "en" | "ar"}) {
  const language = item.metadata.analytics?.language ?? conversationLanguage;
  return <article className={`message message-${item.role}`} dir={language === "ar" ? "rtl" : "ltr"} lang={language}><span className="message-avatar" aria-hidden="true">{item.role === "assistant" ? <Icon name="spark" /> : userLabel.slice(0, 1)}</span><div className="message-body"><strong className="message-author">{item.role === "assistant" ? assistantLabel : userLabel}</strong>{item.role !== "assistant" || !item.metadata.analytics ? <div className="message-content">{item.content}</div> : null}{item.role === "assistant" && item.metadata.analytics ? <AnalyticsResult result={item.metadata.analytics} onRetry={onRetry} /> : null}{item.metadata.status === "failed" ? <small className="error">{failedLabel} <button type="button" className="secondary" onClick={onRetry}>{retryLabel}</button></small> : null}</div></article>;
}

function mergeMessages(current: ConversationMessage[], added: ConversationMessage[]) { return [...current, ...added.filter((item) => !current.some((existing) => existing.id === item.id))].sort((left, right) => left.sequence_number - right.sequence_number); }
function message(error: unknown) { return error instanceof Error ? error.message : "The request could not be completed."; }

type IconName = "close" | "database" | "menu" | "message" | "panel" | "plus" | "search" | "send" | "settings" | "spark";

function Icon({name}: {name: IconName}) {
  const paths: Record<IconName, React.ReactNode> = {
    close: <path d="m6 6 12 12M18 6 6 18" />,
    database: <><ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7" /></>,
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    message: <path d="M20 15a3 3 0 0 1-3 3H9l-5 3V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z" />,
    panel: <><rect width="18" height="16" x="3" y="4" rx="2" /><path d="M9 4v16" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    settings: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></>,
    spark: <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7Z" />,
  };
  return <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8">{paths[name]}</svg>;
}
