export type ResponseLanguage = "auto" | "en" | "ar";

export type AnalyticsResponse = {
  status: "success" | "needs_clarification" | "unsupported" | "empty_result" | "failed";
  request_id: string;
  language: "en" | "ar";
  answer?: string | null;
  error_code?: string | null;
  retryable: boolean;
};

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

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}.`);
  }

  return (await response.json()) as AnalyticsResponse;
}
