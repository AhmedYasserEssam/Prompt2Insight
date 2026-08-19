import assert from "node:assert/strict";
import test from "node:test";

import type {ChartSpecification, ResultTable} from "../lib/api.ts";
import {
  buildChartModel,
  compatibleChartTypes,
  formatAxisNumber,
  formatDate,
  formatFullNumber,
  formatPercentage,
  formatTableValue,
  formatTooltipValue,
  selectTicks,
} from "../lib/visualization.ts";

const monthlyChart: ChartSpecification = {
  type: "line",
  x_column: "month",
  y_columns: ["total_sales"],
  title: "Monthly sales",
};

test("monthly dates use clean axis and tooltip labels", () => {
  assert.equal(formatDate("2016-01-01T00:00:00", "en", "axis", "month"), "Jan 2016");
  assert.equal(
    formatDate("2016-01-01T00:00:00", "en", "tooltip", "month"),
    "January 2016",
  );
});

test("table dates use the full readable month and formatted values", () => {
  assert.equal(
    formatDate("2018-11-01T00:00:00+00:00", "en", "table", "month"),
    "November 2018",
  );
  assert.equal(formatFullNumber(117938.1550), "117,938.16");
});

test("time series points are copied and sorted chronologically", () => {
  const table: ResultTable = {
    columns: ["month", "total_sales"],
    rows: [
      ["2016-03-01T00:00:00", 30],
      ["2016-01-01T00:00:00", 10],
      ["2016-02-01T00:00:00", 20],
    ],
  };
  const original = structuredClone(table);

  const model = buildChartModel(monthlyChart, table, "en");

  assert.deepEqual(model.series[0].points.map((point) => point.xLabel), [
    "Jan 2016",
    "Feb 2016",
    "Mar 2016",
  ]);
  assert.deepEqual(table, original);
});

test("long monthly series receives reduced deterministic tick density", () => {
  const values = Array.from({length: 48}, (_, index) => index);

  assert.deepEqual(selectTicks(values), [0, 6, 12, 18, 24, 30, 36, 42]);
});

test("axis, tooltip, counts, and percentages use centralized precision", () => {
  assert.equal(formatAxisNumber(827455.873), "827.5K");
  assert.equal(formatAxisNumber(1250000), "1.25M");
  assert.equal(formatFullNumber(827455.873), "827,455.87");
  assert.equal(formatFullNumber(728658.5757), "728,658.58");
  assert.equal(formatFullNumber(4922), "4,922");
  assert.equal(formatPercentage(24.7358), "24.74%");
  assert.equal(formatTooltipValue(728658.5757, "total_sales"), "728,658.58");
  assert.equal(formatTooltipValue(24.7358, "margin_percentage"), "24.74%");
});

test("table formatting preserves identifiers and formats analytical values", () => {
  assert.equal(formatTableValue(1000001, "order_id", "en"), "1000001");
  assert.equal(formatTableValue(728658.5757, "total_sales", "en"), "728,658.58");
  assert.equal(formatTableValue("117938.1550", "total_sales", "en"), "117,938.16");
  assert.equal(formatTableValue(4922, "order_count", "en"), "4,922");
  assert.equal(formatTableValue("2016-01-01T00:00:00", "month", "en"), "January 2016");
});

test("long-form results become separate sorted series without mutating rows", () => {
  const table: ResultTable = {
    columns: ["month", "category", "total_sales"],
    rows: [
      ["2016-02-01", "تقنية", 20],
      ["2016-01-01", "تقنية", 10],
      ["2016-02-01", "أثاث", 15],
      ["2016-01-01", "أثاث", 5],
    ],
  };
  const original = structuredClone(table);
  const chart: ChartSpecification = {
    ...monthlyChart,
    series_column: "category",
    title: "إجمالي المبيعات حسب الفئة",
  };

  const model = buildChartModel(chart, table, "ar");

  assert.equal(model.title, "إجمالي المبيعات حسب الفئة");
  assert.deepEqual(model.series.map((series) => series.label), ["تقنية", "أثاث"]);
  assert.deepEqual(model.series[0].points.map((point) => point.value), [10, 20]);
  assert.deepEqual(table, original);
});

test("RTL language does not reverse chronological order", () => {
  const table: ResultTable = {
    columns: ["month", "total_sales"],
    rows: [["2016-02-01", 20], ["2016-01-01", 10]],
  };

  const model = buildChartModel({...monthlyChart, title: "الإيرادات الشهرية"}, table, "ar");

  assert.equal(model.title, "الإيرادات الشهرية");
  assert.deepEqual(model.series[0].points.map((point) => point.value), [10, 20]);
  assert.ok(model.series[0].points[0].xLabel.includes("٢٠١٦"));
});

test("chart switcher exposes only compatible alternatives", () => {
  const categoryTable: ResultTable = {
    columns: ["category", "sales"],
    rows: [["A", 60], ["B", 40]],
  };
  const categoryChart: ChartSpecification = {
    type: "bar",
    x_column: "category",
    y_columns: ["sales"],
  };

  assert.deepEqual(compatibleChartTypes(categoryChart, categoryTable), [
    "bar",
    "horizontal_bar",
    "table",
  ]);
  assert.deepEqual(compatibleChartTypes({...categoryChart, type: "donut"}, categoryTable), [
    "bar",
    "horizontal_bar",
    "donut",
    "table",
  ]);
  assert.deepEqual(compatibleChartTypes(monthlyChart, categoryTable), ["line", "area", "table"]);
});
