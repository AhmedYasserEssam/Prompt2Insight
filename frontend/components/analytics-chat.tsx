"use client";

import {FormEvent, useMemo, useState} from "react";
import {
  AnalyticsResponse,
  askAnalyticsQuestion,
  ResponseLanguage,
} from "@/lib/api";
import {formatTableValue} from "@/lib/visualization";
import {
  partitionWarnings,
  tableStartsExpanded,
} from "@/lib/analytics-presentation";
import {ChartRenderer} from "@/components/chart-renderer";

const labels = {
  en: {
    answer: "Answer / Insight", visualization: "Chart or KPI",
    viewData: (count: number) => `View data (${count} ${count === 1 ? "row" : "rows"})`,
    technicalDetails: "Technical details", sql: "Executed SQL",
    empty: "No matching rows were returned.", chartEmpty: "No numeric chart values were returned.", warnings: "Warnings", status: "Status",
    statuses: {success: "Complete", needs_clarification: "Needs clarification", unsupported: "Unsupported", empty_result: "No results", failed: "Failed"},
  },
  ar: {
    answer: "الإجابة / الرؤية", visualization: "الرسم البياني أو مؤشر الأداء",
    viewData: (count: number) => `عرض البيانات (${count} صفوف)`,
    technicalDetails: "التفاصيل التقنية", sql: "استعلام SQL المنفذ",
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
  const completedResult =
    result?.status === "success" || result?.status === "empty_result";
  const warningGroups = partitionWarnings(result?.warnings ?? []);
  const visibleWarnings = completedResult
    ? warningGroups.visible
    : result?.warnings ?? [];
  const technicalWarnings = completedResult ? warningGroups.technical : [];
  const hasTechnicalDetails = Boolean(
    result?.sql || result?.error_code || technicalWarnings.length,
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
        <section className="card response-stack" dir={direction} lang={result.language}>
          {!completedResult ? (
            <div className="row">
              <strong>{t.status}</strong>
              <span>{t.statuses[result.status]}</span>
            </div>
          ) : null}

          {result.answer || result.insights?.length || result.status === "empty_result" ? (
            <section className="result-section stack">
              <strong className="section-label">{t.answer}</strong>
              {result.answer ? <div className="answer-copy">{result.answer}</div> : null}
              {!result.answer && result.status === "empty_result" ? <div>{t.empty}</div> : null}
              {result.insights?.length ? (
                <ul>{result.insights.map((insight) => <li key={insight}>{insight}</li>)}</ul>
              ) : null}
            </section>
          ) : null}

          {visibleWarnings.length ? (
            <section className="visible-warnings stack">
              <strong>{t.warnings}</strong>
              <ul>{visibleWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </section>
          ) : null}

          {result.chart && result.table ? (
            <section className="result-section stack">
              <strong className="section-label">{t.visualization}</strong>
              <ChartRenderer
                chart={result.chart}
                table={result.table}
                language={result.language}
                empty={t.chartEmpty}
              />
            </section>
          ) : null}

          {result.table ? (
            <details
              className="result-disclosure"
              open={tableStartsExpanded(result.table.rows.length)}
            >
              <summary>{t.viewData(result.table.rows.length)}</summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>{result.table.columns.map((column) => <th key={column}>{column}</th>)}</tr>
                  </thead>
                  <tbody>
                    {result.table.rows.map((row, index) => (
                      <tr key={index}>
                        {row.map((value, cell) => (
                          <td className={typeof value === "number" ? "numeric-cell" : ""} key={cell}>
                            {formatTableValue(value, result.table?.columns[cell] ?? "", result.language)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}

          {hasTechnicalDetails ? (
            <details className="result-disclosure technical-details">
              <summary>{t.technicalDetails}</summary>
              <div className="stack disclosure-content">
                {technicalWarnings.length ? (
                  <ul>{technicalWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
                ) : null}
                {result.sql ? <div className="stack"><strong>{t.sql}</strong><pre dir="ltr">{result.sql}</pre></div> : null}
                {result.error_code ? <code dir="ltr">{result.error_code}</code> : null}
              </div>
            </details>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
