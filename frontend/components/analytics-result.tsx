"use client";

import {useState} from "react";

import {ChartRenderer} from "@/components/chart-renderer";
import {partitionWarnings, tableStartsExpanded} from "@/lib/analytics-presentation";
import type {AnalyticsResponse} from "@/lib/api";
import {formatTableValue} from "@/lib/visualization";

export function AnalyticsResult({result, onRetry}: {result: AnalyticsResponse; onRetry?: () => void}) {
  const [copied, setCopied] = useState(false);
  const completed = result.status === "success" || result.status === "empty_result";
  const warnings = partitionWarnings(result.warnings ?? []);
  const visibleWarnings = completed ? warnings.visible : result.warnings ?? [];
  const technicalWarnings = completed ? warnings.technical : [];
  const copySql = async () => {
    if (!result.sql) return;
    await navigator.clipboard?.writeText(result.sql);
    setCopied(true);
  };

  return <section className="analytics-result" dir={result.language === "ar" ? "rtl" : "ltr"} lang={result.language}>
    {!completed ? <p className="result-status">{result.status.replaceAll("_", " ")}</p> : null}
    {result.answer ? <div className="answer-copy">{result.answer}</div> : result.status === "empty_result" ? <p>No matching rows were returned.</p> : null}
    {result.insights?.length ? <ul>{result.insights.map((item) => <li key={item}>{item}</li>)}</ul> : null}
    {visibleWarnings.length ? <div className="visible-warnings"><strong>Warnings</strong><ul>{visibleWarnings.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
    {result.chart && result.table ? <ChartRenderer chart={result.chart} table={result.table} language={result.language} empty="No numeric chart values were returned." /> : null}
    {result.table ? <details className="result-disclosure" open={tableStartsExpanded(result.table.rows.length)}><summary>View data ({result.table.rows.length} rows)</summary><div className="table-wrap" tabIndex={0} aria-label="Result data"><table><thead><tr>{result.table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{result.table.rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td key={cell}>{formatTableValue(value, result.table?.columns[cell] ?? "", result.language)}</td>)}</tr>)}</tbody></table></div></details> : null}
    {result.sql || result.error_code || technicalWarnings.length ? <details className="result-disclosure technical-details"><summary>Technical details</summary>{technicalWarnings.length ? <ul>{technicalWarnings.map((item) => <li key={item}>{item}</li>)}</ul> : null}{result.sql ? <div className="sql-row"><pre>{result.sql}</pre><button type="button" className="secondary" onClick={() => void copySql()}>{copied ? "Copied" : "Copy SQL"}</button></div> : null}{result.error_code ? <code>{result.error_code}</code> : null}</details> : null}
    {onRetry && result.retryable ? <button type="button" className="secondary" onClick={onRetry}>Retry</button> : null}
  </section>;
}
