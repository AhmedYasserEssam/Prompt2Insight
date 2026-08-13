"use client";

import {FormEvent, useEffect, useState} from "react";
import {AnalyticsChat} from "@/components/analytics-chat";
import {CatalogConfigurator} from "@/components/catalog-configurator";
import {ConnectionProfile, ConnectionProfileInput, listConnectionProfiles, selectConnection, setupConnection, testConnection} from "@/lib/api";

type Locale = "en" | "ar";
const copy = {
  en: {connect: "Connect a database", description: "Prompt2Insight needs a database connection before you can ask questions.", open: "Connect Database", name: "Connection name", type: "Database type", host: "Host", port: "Port", database: "Database", username: "Username", credential: "Credential environment variable", test: "Test Connection", save: "Save & Continue", cancel: "Cancel", testing: "Testing connection…", saving: "Saving profile…", schema: "Reading schema…", catalog: "Catalog needs configuration", stale: "The database schema has changed. Revalidate and publish the semantic catalog.", settings: "Settings · Data Connections", configured: "The schema is ready. Add a semantic catalog revision through the existing catalog workflow before analytics can be enabled.", select: "Connect / select", success: "Connection successful", failed: "Connection failed", credentialHelp: "Use the server environment variable that contains the database URL. Passwords are never stored by Prompt2Insight."},
  ar: {connect: "ربط قاعدة بيانات", description: "يحتاج Prompt2Insight إلى اتصال بقاعدة بيانات قبل أن تتمكن من طرح الأسئلة.", open: "ربط قاعدة بيانات", name: "اسم الاتصال", type: "نوع قاعدة البيانات", host: "المضيف", port: "المنفذ", database: "قاعدة البيانات", username: "اسم المستخدم", credential: "متغير بيئة بيانات الاعتماد", test: "اختبار الاتصال", save: "حفظ ومتابعة", cancel: "إلغاء", testing: "جارٍ اختبار الاتصال…", saving: "جارٍ حفظ الاتصال…", schema: "جارٍ قراءة المخطط…", catalog: "يحتاج الكتالوج إلى إعداد", stale: "تغير مخطط قاعدة البيانات. أعد التحقق من الكتالوج الدلالي وانشره.", settings: "الإعدادات · اتصالات البيانات", configured: "المخطط جاهز. أضف مراجعة للكتالوج الدلالي من خلال سير عمل الكتالوج الحالي قبل تفعيل التحليلات.", select: "اتصال / اختيار", success: "تم الاتصال بنجاح", failed: "فشل الاتصال", credentialHelp: "استخدم متغير بيئة الخادم الذي يحتوي على رابط قاعدة البيانات. لا يخزن Prompt2Insight كلمات المرور."},
};
const initialForm: ConnectionProfileInput = {name: "", dialect: "postgres", host: "", port: 5432, database_name: "", username: "", credential_reference: ""};

export function ConnectionWorkspace() {
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<"test" | "save" | "select" | null>(null);
  const [locale, setLocale] = useState<Locale>("en");
  const [catalogProfileId, setCatalogProfileId] = useState<string | null>(null);
  const t = copy[locale];

  useEffect(() => { void listConnectionProfiles().then(setProfiles).catch((error: unknown) => setMessage(error instanceof Error ? error.message : "Unable to load connections.")); }, []);
  function update<K extends keyof ConnectionProfileInput>(key: K, value: ConnectionProfileInput[K]) { setForm((current) => ({...current, [key]: value})); }
  function changeDialect(dialect: "postgres" | "mysql") { setForm((current) => ({...current, dialect, port: dialect === "postgres" ? 5432 : 3306})); }
  async function test() { setPending("test"); setMessage(null); try { const result = await testConnection(form); setMessage(result.status === "success" ? t.success : result.message); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }
  async function save(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPending("save"); setMessage(t.saving); try { const progress = await setupConnection(form); setProfiles((current) => [...current, progress.profile]); setShowForm(false); setMessage(`${t.success} ${t.schema} ${t.catalog}.`); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }
  async function select(profile: ConnectionProfile) { setPending("select"); setMessage(null); try { const progress = await selectConnection(profile.id); if (progress.conversation_id) setConversationId(progress.conversation_id); else setMessage(t.configured); } catch (error) { setMessage(error instanceof Error ? error.message : t.failed); } finally { setPending(null); } }

  if (conversationId) return <AnalyticsChat conversationId={conversationId} />;
  if (catalogProfileId) return <CatalogConfigurator profileId={catalogProfileId} locale={locale} onReady={() => { setCatalogProfileId(null); void listConnectionProfiles().then(setProfiles); }} />;
  return <section className="stack" dir={locale === "ar" ? "rtl" : "ltr"} lang={locale}>
    <div className="row"><h2>{t.settings}</h2><button type="button" className="secondary" onClick={() => setLocale(locale === "en" ? "ar" : "en")}>{locale === "en" ? "العربية" : "English"}</button></div>
    {!showForm && profiles.length === 0 ? <div className="card stack"><h2>{t.connect}</h2><p>{t.description}</p><button type="button" onClick={() => setShowForm(true)}>{t.open}</button></div> : null}
    {showForm ? <form className="card stack" onSubmit={save}>
      <h2>{t.connect}</h2><label>{t.name}<input required value={form.name} onChange={(e) => update("name", e.target.value)} /></label><label>{t.type}<select value={form.dialect} onChange={(e) => changeDialect(e.target.value as "postgres" | "mysql")}><option value="postgres">PostgreSQL</option><option value="mysql">MySQL</option></select></label>
      <label>{t.host}<input required value={form.host} onChange={(e) => update("host", e.target.value)} /></label><label>{t.port}<input required type="number" value={form.port} onChange={(e) => update("port", Number(e.target.value))} /></label><label>{t.database}<input required value={form.database_name} onChange={(e) => update("database_name", e.target.value)} /></label><label>{t.username}<input required value={form.username} onChange={(e) => update("username", e.target.value)} /></label>
      <label>{t.credential}<input required value={form.credential_reference} onChange={(e) => update("credential_reference", e.target.value)} /></label><small>{t.credentialHelp}</small>
      <div className="row"><button type="button" className="secondary" disabled={pending !== null} onClick={() => void test()}>{pending === "test" ? t.testing : t.test}</button><button disabled={pending !== null}>{pending === "save" ? t.saving : t.save}</button><button type="button" className="secondary" disabled={pending !== null} onClick={() => setShowForm(false)}>{t.cancel}</button></div>
    </form> : null}
    {profiles.length > 0 ? <div className="card stack"><h3>{t.settings}</h3>{profiles.map((profile) => <div className="stack" key={profile.id}><div className="row"><span dir="ltr">{profile.name} · {profile.dialect} · {profile.host}/{profile.database_name} · {profile.state}</span>{profile.state === "ready" ? <button disabled={pending !== null} onClick={() => void select(profile)}>{t.select}</button> : <button disabled={pending !== null} onClick={() => setCatalogProfileId(profile.id)}>Configure Semantic Catalog</button>}</div>{profile.state === "stale" ? <span className="error">{t.stale}</span> : null}</div>)}<button type="button" className="secondary" onClick={() => setShowForm(true)}>{t.open}</button></div> : null}
    {message ? <div className="card" role="status">{message}</div> : null}
  </section>;
}
