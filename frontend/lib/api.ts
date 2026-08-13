export type ResponseLanguage = "auto" | "en" | "ar";

export type AnalyticsResponse = {
  status: "success" | "needs_clarification" | "unsupported" | "empty_result" | "failed";
  request_id: string;
  language: "en" | "ar";
  answer?: string | null;
  error_code?: string | null;
  retryable: boolean;
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

export type SchemaSnapshot = {dialect: "postgres" | "mysql"; database_name: string; tables: {schema_name: string | null; table_name: string; table_type: string; columns: {name: string; data_type: string; nullable: boolean}[]}[]};
export type Catalog = {catalog_version: string; metrics: Record<string, Definition & {allowed_dimensions: string[]}>; dimensions: Record<string, Definition>; join_contracts: {left: string; right: string; relationship: string; allowed_types: string[]}[]; column_policies: Record<string, "non_sensitive" | "sensitive" | "prohibited">; privacy: {privacy_unit: string; minimum_group_size: number}};
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

export function getCatalogStatus(profileId: string): Promise<CatalogStatus> { return connectionRequest(`/${profileId}/catalog`); }
export function validateCatalog(profileId: string, catalog: Catalog): Promise<CatalogValidationResult> { return connectionRequest(`/${profileId}/catalog/validate`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(catalog)}); }
export function publishCatalog(profileId: string, catalog: Catalog): Promise<CatalogStatus> { return connectionRequest(`/${profileId}/catalog/publish`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(catalog)}); }
