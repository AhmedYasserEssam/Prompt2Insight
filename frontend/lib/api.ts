export type ResponseLanguage = "auto" | "en" | "ar";

export type ChartType =
  | "bar"
  | "horizontal_bar"
  | "line"
  | "area"
  | "scatter"
  | "donut"
  | "kpi";

export type ChartSpecification = {
  type: ChartType;
  x_column?: string | null;
  y_columns: string[];
  series_column?: string | null;
  title?: string | null;
  x_label?: string | null;
  y_label?: string | null;
};

export type ResultTable = {columns: string[]; rows: unknown[][]};

export type AnalyticsResponse = {
  status: "success" | "needs_clarification" | "unsupported" | "empty_result" | "failed";
  request_id: string;
  language: "en" | "ar";
  answer?: string | null;
  insights: string[];
  table?: ResultTable | null;
  chart?: ChartSpecification | null;
  sql?: string | null;
  warnings: string[];
  error_code?: string | null;
  retryable: boolean;
};

export type ConversationMessage = {
  id: string;
  conversation_id: string;
  sequence_number: number;
  role: "user" | "assistant" | "system";
  content: string;
  metadata: {status?: string; error_code?: string; request_id?: string; analytics?: AnalyticsResponse};
  created_at: string;
};

export type ConversationSummary = {
  id: string;
  connection_id: string | null;
  title: string;
  language: ResponseLanguage;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type Conversation = ConversationSummary & {messages: ConversationMessage[]};

export type ConversationSubmission = {
  user_message: ConversationMessage;
  assistant_message: ConversationMessage | null;
  analytics: AnalyticsResponse | null;
  failure: {status: "failed"; code: string; message: string} | null;
};

export type ConnectionProfileInput = {
  name: string;
  dialect: "postgres" | "mysql";
  host: string;
  port: number;
  database_name: string;
  username: string;
  credential_reference: string;
};

export type ConnectionProfile = ConnectionProfileInput & {
  id: string;
  state: "draft" | "catalog_needs_configuration" | "ready" | "stale";
};

export type ConnectionTestResult = {
  status: "success" | "failed";
  message: string;
  code?: string | null;
};

export type SetupProgress = {
  profile: ConnectionProfile;
  schema_state: string;
  catalog_state: string;
  conversation_id?: string | null;
};

export type SchemaRefreshResult = {
  profile_id: string;
  schema_snapshot_id: string;
  schema_changed: boolean;
  state: ConnectionProfile["state"];
};

export type SchemaSnapshot = {dialect: "postgres" | "mysql"; database_name: string; tables: {schema_name: string | null; table_name: string; table_type: string; columns: {name: string; data_type: string; nullable: boolean}[]}[]};
export type Catalog = {catalog_version: string; metrics: Record<string, Definition>; dimensions: Record<string, Definition>};
type Definition = {labels: {en: string; ar: string}; aliases: {en: string[]; ar: string[]}; descriptions: {en: string; ar: string}; expressions: {postgres: string; mysql: string}};
export type CatalogStatus = {catalog: Catalog | null; schema_snapshot: SchemaSnapshot; state: string; content_hash?: string | null};
export type CatalogValidationResult = {valid: boolean; errors: string[]};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export async function askAnalyticsQuestion(input: {
  conversationId: string;
  requestId: string;
  question: string;
  responseLanguage: ResponseLanguage;
}): Promise<AnalyticsResponse> {
  const response = await fetch(
    `${API_BASE_URL}/conversations/${input.conversationId}/requests`,
    {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        request_id: input.requestId,
        question: input.question,
        response_language: input.responseLanguage,
      }),
    },
  );

  if (!response.ok) throw await responseError(response);

  return (await response.json()) as AnalyticsResponse;
}

async function responseError(response: Response): Promise<Error> {
  const body = (await response.json().catch(() => null)) as {
    message?: string;
    detail?: string;
  } | null;
  return new Error(body?.message ?? body?.detail ?? "The request could not be completed.");
}

async function conversationRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/conversations${path}`, options);
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listConversations(includeArchived = false): Promise<{items: ConversationSummary[]}> {
  return conversationRequest(`?limit=100${includeArchived ? "&include_archived=true" : ""}`);
}

export function getConversation(conversationId: string): Promise<Conversation> {
  return conversationRequest(`/${conversationId}`);
}

export function createConversation(input: {connectionId: string; language: ResponseLanguage}): Promise<Conversation> {
  return conversationRequest("", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({connection_id: input.connectionId, language: input.language})});
}

export function updateConversation(conversationId: string, input: {title?: string; archived?: boolean; language?: ResponseLanguage}): Promise<Conversation> {
  return conversationRequest(`/${conversationId}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(input)});
}

export function deleteConversation(conversationId: string): Promise<void> {
  return conversationRequest(`/${conversationId}`, {method: "DELETE"});
}

export function submitConversationMessage(conversationId: string, input: {content: string; clientMessageId: string}): Promise<ConversationSubmission> {
  return conversationRequest(`/${conversationId}/messages`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({content: input.content, client_message_id: input.clientMessageId})});
}

async function connectionRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/connection-profiles${path}`, options);
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}

export function listConnectionProfiles(): Promise<ConnectionProfile[]> {
  return connectionRequest("");
}

export function testConnection(input: ConnectionProfileInput): Promise<ConnectionTestResult> {
  return connectionRequest("/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(input)});
}

export function setupConnection(input: ConnectionProfileInput): Promise<SetupProgress> {
  return connectionRequest("/setup", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(input)});
}

export function selectConnection(profileId: string): Promise<SetupProgress> {
  return connectionRequest(`/${profileId}/select`, {method: "POST"});
}

export function refreshConnectionSchema(profileId: string): Promise<SchemaRefreshResult> {
  return connectionRequest(`/${profileId}/refresh-schema`, {method: "POST"});
}

export function getCatalogStatus(profileId: string): Promise<CatalogStatus> { return connectionRequest(`/${profileId}/catalog`); }
export function validateCatalog(profileId: string, catalog: Catalog): Promise<CatalogValidationResult> { return connectionRequest(`/${profileId}/catalog/validate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(catalog)}); }
export function publishCatalog(profileId: string, catalog: Catalog): Promise<CatalogStatus> { return connectionRequest(`/${profileId}/catalog/publish`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(catalog)}); }
