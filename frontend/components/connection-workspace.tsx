"use client";

import {FormEvent, useEffect, useMemo, useState} from "react";
import {useRouter} from "next/navigation";
import {CatalogConfigurator} from "@/components/catalog-configurator";
import {ConnectionProfile, ConnectionProfileInput, listConnectionProfiles, refreshConnectionSchema, setupConnection, testConnection} from "@/lib/api";
import {useDocumentLanguage} from "@/lib/document-language";

type Locale = "en" | "ar";
type ProfileState = ConnectionProfile["state"] | "all";
const SELECTED_CONNECTION_KEY = "prompt2insight.selected-connection";
const PAGE_SIZE = 5;
const copy = {
  en: {connect: "Connect a database", description: "Prompt2Insight needs a database connection before you can ask questions.", open: "Connect Database", name: "Connection name", type: "Database type", host: "Host", port: "Port", database: "Database", username: "Username", credential: "Credential environment variable", test: "Test Connection", save: "Save & Continue", cancel: "Cancel", testing: "Testing connection…", saving: "Saving profile…", refreshing: "Refreshing schema…", schema: "Reading schema…", catalog: "Catalog needs configuration", stale: "The database schema has changed. Revalidate and publish the semantic catalog.", settings: "Settings · Data Connections", select: "Use connection", editCatalog: "Edit Semantic Catalog", configureCatalog: "Configure Semantic Catalog", revalidateCatalog: "Revalidate Semantic Catalog", refreshSchema: "Refresh Schema", refreshHelp: "Re-inspects the current database schema. Existing catalog revisions are not modified.", success: "Connection successful", failed: "Connection failed", credentialHelp: "Use the server environment variable that contains the database URL. Passwords are never stored by Prompt2Insight.", search: "Search connections", filter: "Filter by status", all: "All statuses", actions: "More actions", active: "Active connection", ready: "Ready", page: "Page", previous: "Previous", next: "Next", noMatches: "No connections match your filters.", add: "Add connection"},
  ar: {connect: "ربط قاعدة بيانات", description: "يحتاج Prompt2Insight إلى اتصال بقاعدة بيانات قبل أن تتمكن من طرح الأسئلة.", open: "ربط قاعدة بيانات", name: "اسم الاتصال", type: "نوع قاعدة البيانات", host: "المضيف", port: "المنفذ", database: "قاعدة البيانات", username: "اسم المستخدم", credential: "متغير بيئة بيانات الاعتماد", test: "اختبار الاتصال", save: "حفظ ومتابعة", cancel: "إلغاء", testing: "جارٍ اختبار الاتصال…", saving: "جارٍ حفظ الاتصال…", refreshing: "جارٍ تحديث المخطط…", schema: "جارٍ قراءة المخطط…", catalog: "يحتاج الكتالوج إلى إعداد", stale: "تغير مخطط قاعدة البيانات. أعد التحقق من الكتالوج الدلالي وانشره.", settings: "الإعدادات · اتصالات البيانات", select: "استخدام الاتصال", editCatalog: "تحرير الكتالوج الدلالي", configureCatalog: "إعداد الكتالوج الدلالي", revalidateCatalog: "إعادة التحقق من الكتالوج الدلالي", refreshSchema: "تحديث مخطط قاعدة البيانات", refreshHelp: "يعيد فحص مخطط قاعدة البيانات الحالي. لا يتم تعديل مراجعات الكتالوج الحالية.", success: "تم الاتصال بنجاح", failed: "فشل الاتصال", credentialHelp: "استخدم متغير بيئة الخادم الذي يحتوي على رابط قاعدة البيانات. لا يخزن Prompt2Insight كلمات المرور.", search: "البحث في الاتصالات", filter: "التصفية حسب الحالة", all: "كل الحالات", actions: "إجراءات إضافية", active: "الاتصال النشط", ready: "جاهز", page: "الصفحة", previous: "السابق", next: "التالي", noMatches: "لا توجد اتصالات تطابق عوامل التصفية.", add: "إضافة اتصال"},
};
const initialForm: ConnectionProfileInput = {name: "", dialect: "postgres", host: "", port: 5432, database_name: "", username: "", credential_reference: ""};

function profileLabel(profile: ConnectionProfile, ready: string) {
  return `${profile.name} · ${profile.state === "ready" ? ready : profile.state.replaceAll("_", " ")}`;
}

export function ConnectionWorkspace() {
  const router = useRouter();
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<"test" | "save" | "refresh" | null>(null);
  const [locale, setLocale] = useState<Locale>("en");
  const [catalogProfileId, setCatalogProfileId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<ProfileState>("all");
  const [page, setPage] = useState(1);
  const t = copy[locale];
  useDocumentLanguage(locale);
  const activeProfile = profiles.find((profile) => profile.id === activeProfileId) ?? null;

  useEffect(() => {
    void listConnectionProfiles().then((loaded) => {
      setProfiles(loaded);
      const saved = window.localStorage.getItem(SELECTED_CONNECTION_KEY);
      const savedProfile = loaded.find((profile) => profile.id === saved);
      if (savedProfile) setActiveProfileId(savedProfile.id);
    }).catch((error: unknown) => setMessage(error instanceof Error ? error.message : "Unable to load connections."));
  }, []);
  useEffect(() => { setPage(1); }, [search, stateFilter]);

  const filteredProfiles = useMemo(() => profiles.filter((profile) => (
    (stateFilter === "all" || profile.state === stateFilter)
    && `${profile.name} ${profile.dialect} ${profile.host} ${profile.database_name}`.toLowerCase().includes(search.toLowerCase())
  )), [profiles, search, stateFilter]);
  const pageCount = Math.max(1, Math.ceil(filteredProfiles.length / PAGE_SIZE));
  const visibleProfiles = filteredProfiles.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function update<K extends keyof ConnectionProfileInput>(key: K, value: ConnectionProfileInput[K]) { setForm((current) => ({...current, [key]: value})); }
  function changeDialect(dialect: "postgres" | "mysql") { setForm((current) => ({...current, dialect, port: dialect === "postgres" ? 5432 : 3306})); }
  async function test() { setPending("test"); setMessage(null); try { const result = await testConnection(form); setMessage(result.status === "success" ? t.success : result.message); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }
  async function save(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPending("save"); setMessage(t.saving); try { const progress = await setupConnection(form); setProfiles((current) => [...current, progress.profile]); setShowForm(false); setMessage(`${t.success} ${t.schema} ${t.catalog}.`); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }
  function select(profile: ConnectionProfile) { setActiveProfileId(profile.id); window.localStorage.setItem(SELECTED_CONNECTION_KEY, profile.id); router.push("/chat/"); }
  async function refresh(profile: ConnectionProfile) { setPending("refresh"); setMessage(t.refreshing); try { await refreshConnectionSchema(profile.id); setProfiles(await listConnectionProfiles()); setMessage(null); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }

  if (catalogProfileId) return <CatalogConfigurator profileId={catalogProfileId} locale={locale} onClose={() => setCatalogProfileId(null)} onReady={() => { setCatalogProfileId(null); void listConnectionProfiles().then(setProfiles); }} />;
  return <section className="stack" dir={locale === "ar" ? "rtl" : "ltr"} lang={locale}>
    <div className="row workspace-header"><h2>{t.settings}</h2><div className="row"><button type="button" className="secondary" onClick={() => setLocale(locale === "en" ? "ar" : "en")}>{locale === "en" ? "العربية" : "English"}</button>{activeProfile ? <label className="connection-selector">{t.active}<select value={activeProfileId ?? ""} onChange={(event) => { const profile = profiles.find((item) => item.id === event.target.value); if (profile) select(profile); }}><option value={activeProfile.id}>{profileLabel(activeProfile, t.ready)}</option>{profiles.filter((profile) => profile.id !== activeProfile.id && profile.state === "ready").map((profile) => <option key={profile.id} value={profile.id}>{profileLabel(profile, t.ready)}</option>)}</select></label> : null}</div></div>
      {!showForm && profiles.length === 0 ? <div className="card stack"><h2>{t.connect}</h2><p>{t.description}</p><button type="button" onClick={() => setShowForm(true)}>{t.open}</button></div> : null}
      {showForm ? <form className="card stack" onSubmit={save}><h2>{t.connect}</h2><label>{t.name}<input required value={form.name} onChange={(e) => update("name", e.target.value)} /></label><label>{t.type}<select value={form.dialect} onChange={(e) => changeDialect(e.target.value as "postgres" | "mysql")}><option value="postgres">PostgreSQL</option><option value="mysql">MySQL</option></select></label><label>{t.host}<input required value={form.host} onChange={(e) => update("host", e.target.value)} /></label><label>{t.port}<input required type="number" value={form.port} onChange={(e) => update("port", Number(e.target.value))} /></label><label>{t.database}<input required value={form.database_name} onChange={(e) => update("database_name", e.target.value)} /></label><label>{t.username}<input required value={form.username} onChange={(e) => update("username", e.target.value)} /></label><label>{t.credential}<input required value={form.credential_reference} onChange={(e) => update("credential_reference", e.target.value)} /></label><small>{t.credentialHelp}</small><div className="row"><button type="button" className="secondary" disabled={pending !== null} onClick={() => void test()}>{pending === "test" ? t.testing : t.test}</button><button disabled={pending !== null}>{pending === "save" ? t.saving : t.save}</button><button type="button" className="secondary" disabled={pending !== null} onClick={() => setShowForm(false)}>{t.cancel}</button></div></form> : null}
      {profiles.length > 0 ? <div className="card stack"><div className="row"><h3>{t.settings}</h3><button type="button" className="secondary" onClick={() => setShowForm(true)}>{t.add}</button></div><div className="connection-toolbar"><input aria-label={t.search} placeholder={t.search} value={search} onChange={(event) => setSearch(event.target.value)} /><select aria-label={t.filter} value={stateFilter} onChange={(event) => setStateFilter(event.target.value as ProfileState)}><option value="all">{t.all}</option><option value="ready">{t.ready}</option><option value="stale">stale</option><option value="catalog_needs_configuration">{t.catalog}</option></select></div><div className="connection-list">{visibleProfiles.map((profile) => <article className="connection-card" key={profile.id}><div className="stack"><div className="row"><h4>{profile.name}</h4><span className={`status-chip ${profile.state === "stale" ? "stale" : profile.state === "ready" ? "" : "pending"}`}>{profileLabel(profile, t.ready).split(" · ")[1]}</span></div><p dir="ltr">{profile.dialect} · {profile.host}/{profile.database_name}</p>{profile.state === "stale" ? <small className="error">{t.stale}</small> : null}</div><details className="connection-menu"><summary aria-label={`${t.actions}: ${profile.name}`}>•••</summary><div className="connection-menu-actions">{profile.state === "ready" ? <button disabled={pending !== null} onClick={() => select(profile)}>{t.select}</button> : null}<button type="button" disabled={pending !== null} onClick={() => setCatalogProfileId(profile.id)}>{profile.state === "ready" ? t.editCatalog : profile.state === "stale" ? t.revalidateCatalog : t.configureCatalog}</button><button type="button" disabled={pending !== null} onClick={() => void refresh(profile)}>{pending === "refresh" ? t.refreshing : t.refreshSchema}</button></div></details><div className="connection-actions">{profile.state === "ready" ? <button disabled={pending !== null} onClick={() => select(profile)}>{t.select}</button> : <button type="button" disabled={pending !== null} onClick={() => setCatalogProfileId(profile.id)}>{profile.state === "stale" ? t.revalidateCatalog : t.configureCatalog}</button>}</div></article>)}</div>{filteredProfiles.length === 0 ? <p>{t.noMatches}</p> : null}{pageCount > 1 ? <div className="pagination"><button type="button" className="secondary" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>{t.previous}</button><span>{t.page} {page} / {pageCount}</span><button type="button" className="secondary" disabled={page === pageCount} onClick={() => setPage((current) => current + 1)}>{t.next}</button></div> : null}<small>{t.refreshHelp}</small></div> : null}
    {message ? <div className="card" role="status">{message}</div> : null}
  </section>;
}
