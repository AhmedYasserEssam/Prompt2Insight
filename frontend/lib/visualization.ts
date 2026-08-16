import type {
  ChartSpecification,
  ChartType,
  ResultTable,
} from "./api";

export type VisualizationLanguage = "en" | "ar";

export type ChartPoint = {
  x: unknown;
  xKey: string;
  xLabel: string;
  tooltipLabel: string;
  value: number;
};

export type ChartSeries = {
  key: string;
  label: string;
  points: ChartPoint[];
};

export type ChartModel = {
  type: ChartType;
  title: string;
  xLabel?: string | null;
  yLabel?: string | null;
  temporal: boolean;
  series: ChartSeries[];
};

const IDENTIFIER_COLUMN = /(?:^id$|_id$|^uuid$|_uuid$)/i;
const TEMPORAL_COLUMN = /(?:^|_)(?:date|datetime|day|week|month|quarter|year|time|timestamp)(?:_|$)/i;
const PERCENT_COLUMN = /(?:^|_)(?:percent|percentage|pct|ratio)(?:_|$)/i;
const INTEGER = /^-?\d+$/;

export function buildChartModel(
  chart: ChartSpecification,
  table: ResultTable,
  language: VisualizationLanguage,
): ChartModel {
  const title = chart.title || humanize(chart.y_columns.join(" · "));
  if (chart.type === "kpi") {
    return {
      type: chart.type,
      title,
      xLabel: chart.x_label,
      yLabel: chart.y_label,
      temporal: false,
      series: chart.y_columns.flatMap((column) => {
        const index = table.columns.indexOf(column);
        const value = Number(table.rows[0]?.[index]);
        return Number.isFinite(value)
          ? [{
              key: column,
              label: humanize(column),
              points: [{
                x: column,
                xKey: column,
                xLabel: humanize(column),
                tooltipLabel: humanize(column),
                value,
              }],
            }]
          : [];
      }),
    };
  }

  const xColumn = chart.x_column;
  const xIndex = xColumn ? table.columns.indexOf(xColumn) : -1;
  const xValues = table.rows.map((row) => row[xIndex]);
  const temporal = Boolean(xColumn && isTemporalColumn(xColumn, xValues));
  const seriesIndex = chart.series_column
    ? table.columns.indexOf(chart.series_column)
    : -1;
  const groups = new Map<string, ChartSeries>();

  for (const row of table.rows) {
    for (const yColumn of chart.y_columns) {
      const yIndex = table.columns.indexOf(yColumn);
      const value = Number(row[yIndex]);
      if (xIndex < 0 || yIndex < 0 || !Number.isFinite(value)) continue;
      const rawSeries = seriesIndex >= 0 ? row[seriesIndex] : yColumn;
      const key = String(rawSeries ?? "");
      const series = groups.get(key) ?? {
        key,
        label: seriesIndex >= 0 ? key : humanize(yColumn),
        points: [],
      };
      const x = row[xIndex];
      series.points.push({
        x,
        xKey: rawKey(x),
        xLabel: temporal
          ? formatDate(x, language, "axis", xColumn ?? "")
          : String(x ?? ""),
        tooltipLabel: temporal
          ? formatDate(x, language, "tooltip", xColumn ?? "")
          : String(x ?? ""),
        value,
      });
      groups.set(key, series);
    }
  }

  const series = [...groups.values()].map((item) => ({
    ...item,
    points: temporal
      ? [...item.points].sort((left, right) => temporalValue(left.x) - temporalValue(right.x))
      : [...item.points],
  }));
  return {
    type: chart.type,
    title,
    xLabel: chart.x_label,
    yLabel: chart.y_label,
    temporal,
    series,
  };
}

export function compatibleChartTypes(
  chart: ChartSpecification,
  table: ResultTable,
): Array<ChartType | "table"> {
  if (chart.type === "kpi") return ["kpi"];
  const choices: Array<ChartType | "table"> = [];
  if (["bar", "horizontal_bar", "donut"].includes(chart.type)) {
    choices.push("bar", "horizontal_bar");
    if (
      chart.type === "donut"
      &&
      chart.y_columns.length === 1
      && !chart.series_column
      && table.rows.length >= 2
      && table.rows.length <= 6
    ) {
      choices.push("donut");
    }
  } else if (["line", "area"].includes(chart.type)) {
    choices.push("line", "area");
  } else if (chart.type === "scatter") {
    choices.push("scatter");
  }
  choices.push("table");
  return [...new Set(choices)];
}

export function selectTicks<T>(values: T[], maxTicks = 8): T[] {
  if (values.length <= maxTicks) return [...values];
  const step = Math.ceil(values.length / maxTicks);
  return values.filter((_, index) => index % step === 0);
}

export function formatAxisNumber(value: number): string {
  const absolute = Math.abs(value);
  if (absolute < 1000) return formatFullNumber(value);
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    compactDisplay: "short",
    maximumFractionDigits: absolute >= 1_000_000 ? 2 : 1,
  }).format(value);
}

export function formatFullNumber(value: number): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPercentage(value: number): string {
  return `${new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
  }).format(value)}%`;
}

export function formatTooltipValue(value: number, column: string): string {
  return isPercentageColumn(column)
    ? formatPercentage(value)
    : formatFullNumber(value);
}

export function formatTableValue(
  value: unknown,
  column: string,
  language: VisualizationLanguage,
): string {
  if (value === null || value === undefined) return "";
  if (isIdentifierColumn(column)) return String(value);
  if (isTemporalColumn(column, [value])) {
    return formatDate(value, language, "table", column);
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return isPercentageColumn(column)
      ? formatPercentage(value)
      : formatFullNumber(value);
  }
  if (typeof value === "string" && INTEGER.test(value) && !isIdentifierColumn(column)) {
    return new Intl.NumberFormat("en-US").format(Number(value));
  }
  return String(value);
}

export function formatDate(
  value: unknown,
  language: VisualizationLanguage,
  context: "axis" | "tooltip" | "table",
  column = "",
): string {
  const parsed = parseTemporal(value);
  if (!parsed) return String(value ?? "");
  const locale = language === "ar" ? "ar-EG" : "en-US";
  if (/year/i.test(column)) {
    return new Intl.DateTimeFormat(locale, {
      year: "numeric",
      timeZone: "UTC",
    }).format(parsed);
  }
  const monthly = /month/i.test(column);
  if (monthly) {
    return new Intl.DateTimeFormat(locale, {
      month: context === "tooltip" ? "long" : "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(parsed);
  }
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: context === "tooltip" ? "long" : "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

export function humanize(column: string): string {
  return column
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function isIdentifierColumn(column: string): boolean {
  return IDENTIFIER_COLUMN.test(column);
}

export function isPercentageColumn(column: string): boolean {
  return PERCENT_COLUMN.test(column);
}

function isTemporalColumn(column: string, values: unknown[]): boolean {
  if (!TEMPORAL_COLUMN.test(column)) return false;
  const populated = values.filter((value) => value !== null && value !== undefined);
  return populated.length > 0 && populated.every((value) => parseTemporal(value) !== null);
}

function parseTemporal(value: unknown): Date | null {
  if (value instanceof Date && Number.isFinite(value.getTime())) {
    return new Date(value.getTime());
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= 1000 && value <= 9999) {
    return new Date(Date.UTC(value, 0, 1));
  }
  if (typeof value !== "string" || !/^\d{4}(?:-\d{2}(?:-\d{2})?)?(?:[T ].*)?$/.test(value)) {
    return null;
  }
  const normalized = value.length === 4
    ? `${value}-01-01T00:00:00Z`
    : value.includes("T") && !/(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
      ? `${value}Z`
      : value;
  const parsed = new Date(normalized);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function temporalValue(value: unknown): number {
  return parseTemporal(value)?.getTime() ?? Number.POSITIVE_INFINITY;
}

function rawKey(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  return String(value ?? "");
}
