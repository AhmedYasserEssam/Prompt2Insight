"use client";

import {useState} from "react";

import {ChartRenderer} from "@/components/chart-renderer";
import {partitionWarnings, tableStartsExpanded} from "@/lib/analytics-presentation";
import type {AnalyticsResponse} from "@/lib/api";
import {formatTableValue} from "@/lib/visualization";

const copy = {
  en: {empty: "No matching rows were returned.", chartEmpty: "No numeric chart values were returned.", warnings: "Warnings", viewData: (count: number) => `View data (${count} rows)`, resultData: "Result data", technical: "Technical details", copy: "Copy SQL", copied: "Copied", retry: "Retry"},
  ar: {empty: "لم يتم العثور على صفوف مطابقة.", chartEmpty: "لم يتم إرجاع قيم رقمية للمخطط.", warnings: "تنبيهات", viewData: (count: number) => `عرض البيانات (${count} صفوف)`, resultData: "بيانات النتيجة", technical: "التفاصيل التقنية", copy: "نسخ SQL", copied: "تم النسخ", retry: "إعادة المحاولة"},
};

export function AnalyticsResult({result, onRetry}: {result: AnalyticsResponse; onRetry?: () => void}) {
  const [copied, setCopied] = useState(false);
  const completed = result.status === "success" || result.status === "empty_result";
  const warnings = partitionWarnings(result.warnings ?? []);
  const visibleWarnings = completed ? warnings.visible : result.warnings ?? [];
  const technicalWarnings = completed ? warnings.technical : [];
  const t = copy[result.language];
  const copySql = async () => {
    if (!result.sql) return;
    await navigator.clipboard?.writeText(result.sql);
    setCopied(true);
  };

  return <section className="analytics-result" dir={result.language === "ar" ? "rtl" : "ltr"} lang={result.language}>
    {!completed ? <p className="result-status">{result.status.replaceAll("_", " ")}</p> : null}
    {result.answer ? <div className="answer-copy">{result.answer}</div> : result.status === "empty_result" ? <p>{t.empty}</p> : null}
    {result.insights?.length ? <ul>{result.insights.map((item) => <li key={item}>{item}</li>)}</ul> : null}
    {visibleWarnings.length ? <div className="visible-warnings"><strong>{t.warnings}</strong><ul>{visibleWarnings.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
    {result.chart && result.table ? <ChartRenderer chart={result.chart} table={result.table} language={result.language} empty={t.chartEmpty} /> : null}
    {result.table ? <details className="result-disclosure" open={tableStartsExpanded(result.table.rows.length)}><summary>{t.viewData(result.table.rows.length)}</summary><div className="table-wrap" tabIndex={0} aria-label={t.resultData}><table><thead><tr>{result.table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{result.table.rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td className={typeof value === "number" ? "numeric-cell" : ""} key={cell}>{formatTableValue(value, result.table?.columns[cell] ?? "", result.language)}</td>)}</tr>)}</tbody></table></div></details> : null}
    {result.sql || result.error_code || technicalWarnings.length ? <details className="result-disclosure technical-details"><summary>{t.technical}</summary>{technicalWarnings.length ? <ul>{technicalWarnings.map((item) => <li key={item}>{item}</li>)}</ul> : null}{result.sql ? <div className="sql-row"><pre dir="ltr">{result.sql}</pre><button type="button" className="secondary" onClick={() => void copySql()}>{copied ? t.copied : t.copy}</button></div> : null}{result.error_code ? <code dir="ltr">{result.error_code}</code> : null}</details> : null}
    {onRetry && result.retryable ? <button type="button" className="secondary" onClick={onRetry}>{t.retry}</button> : null}
  </section>;
}
