"use client";

import {FormEvent, useMemo, useState} from "react";
import {
  AnalyticsResponse,
  askAnalyticsQuestion,
  ResponseLanguage,
} from "@/lib/api";
import {formatTableValue} from "@/lib/visualization";
import {ChartRenderer} from "@/components/chart-renderer";

const labels = {
  en: {
    answer: "Answer", results: "Results", sql: "Executed SQL",
    empty: "No matching rows were returned.", chartEmpty: "No numeric chart values were returned.", warnings: "Warnings", status: "Status",
    statuses: {success: "Complete", needs_clarification: "Needs clarification", unsupported: "Unsupported", empty_result: "No results", failed: "Failed"},
  },
  ar: {
    answer: "الإجابة", results: "النتائج", sql: "استعلام SQL المنفذ",
    empty: "لم يتم العثور على صفوف مطابقة.", chartEmpty: "لم يتم إرجاع قيم رقمية للمخطط.", warnings: "تنبيهات", status: "الحالة",
    statuses: {success: "مكتمل", needs_clarification: "يحتاج إلى توضيح", unsupported: "غير مدعوم", empty_result: "لا توجد نتائج", failed: "فشل"},
  },
};

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
  const t = result?.language === "ar" ? labels.ar : labels.en;

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
          <div className="row">
            <strong>{t.status}</strong><span>{t.statuses[result.status]}</span>
          </div>
          {result.answer ? <div className="stack"><strong>{t.answer}</strong><div>{result.answer}</div></div> : null}
          {result.insights?.length ? <ul>{result.insights.map((insight) => <li key={insight}>{insight}</li>)}</ul> : null}
          {result.status === "empty_result" ? <div>{t.empty}</div> : null}
          {result.chart && result.table ? <ChartRenderer chart={result.chart} table={result.table} language={result.language} empty={t.chartEmpty} /> : null}
          {result.table ? <div className="stack"><strong>{t.results}</strong><div className="table-wrap"><table><thead><tr>{result.table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{result.table.rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td className={typeof value === "number" ? "numeric-cell" : ""} key={cell}>{formatTableValue(value, result.table?.columns[cell] ?? "", result.language)}</td>)}</tr>)}</tbody></table></div></div> : null}
          {result.warnings?.length ? <div className="stack"><strong>{t.warnings}</strong><ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div> : null}
          {result.sql ? <div className="stack"><strong>{t.sql}</strong><pre dir="ltr">{result.sql}</pre></div> : null}
          {result.error_code ? <code dir="ltr">{result.error_code}</code> : null}
        </section>
      ) : null}
    </div>
  );
}
