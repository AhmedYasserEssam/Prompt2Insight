"use client";

import {FormEvent, useMemo, useState} from "react";
import {
  AnalyticsResponse,
  askAnalyticsQuestion,
  ResponseLanguage,
} from "@/lib/api";

export function AnalyticsChat({conversationId}: {conversationId: string}) {
  const [question, setQuestion] = useState("");
  const [responseLanguage, setResponseLanguage] =
    useState<ResponseLanguage>("auto");
  const [result, setResult] = useState<AnalyticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const direction = useMemo(
    () => (result?.language === "ar" ? "rtl" : "ltr"),
    [result?.language],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      setResult(
        await askAnalyticsQuestion({
          conversationId,
          requestId: crypto.randomUUID(),
          question,
          responseLanguage,
        }),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unknown error.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={submit}>
        <div className="row">
          <label htmlFor="language">Response language</label>
          <select
            id="language"
            value={responseLanguage}
            onChange={(event) =>
              setResponseLanguage(event.target.value as ResponseLanguage)
            }
          >
            <option value="auto">Auto</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </select>
        </div>

        <textarea
          required
          maxLength={4000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Show monthly revenue / اعرض الإيرادات الشهرية"
        />

        <button disabled={submitting || question.trim().length === 0}>
          {submitting ? "Running…" : "Ask"}
        </button>
      </form>

      {error ? <div className="card error">{error}</div> : null}

      {result ? (
        <section className="card stack" dir={direction} lang={result.language}>
          <strong>{result.status}</strong>
          <div>{result.answer}</div>
          {result.error_code ? <code dir="ltr">{result.error_code}</code> : null}
        </section>
      ) : null}
    </div>
  );
}
