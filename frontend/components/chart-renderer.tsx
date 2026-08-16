"use client";

import {useEffect, useMemo, useState} from "react";

import type {
  ChartSpecification,
  ChartType,
  ResultTable,
} from "@/lib/api";
import {
  buildChartModel,
  compatibleChartTypes,
  formatAxisNumber,
  formatTooltipValue,
  selectTicks,
  type ChartModel,
  type ChartPoint,
  type VisualizationLanguage,
} from "@/lib/visualization";

const PALETTE = ["#2563eb", "#0f766e", "#9333ea", "#ea580c", "#0891b2", "#be123c", "#4d7c0f", "#7c3aed"];

const switcherLabels = {
  en: {bar: "Bar", horizontal_bar: "Horizontal bar", line: "Line", area: "Area", scatter: "Scatter", donut: "Donut", table: "Table", tableHint: "The formatted result table is shown below."},
  ar: {bar: "أعمدة", horizontal_bar: "أعمدة أفقية", line: "خطي", area: "مساحة", scatter: "مبعثر", donut: "حلقي", table: "جدول", tableHint: "يظهر جدول النتائج المنسق أدناه."},
};

export function ChartRenderer({
  chart,
  table,
  language,
  empty,
}: {
  chart: ChartSpecification;
  table: ResultTable;
  language: VisualizationLanguage;
  empty: string;
}) {
  const [selected, setSelected] = useState<ChartType | "table">(chart.type);
  useEffect(() => setSelected(chart.type), [chart]);
  const choices = useMemo(() => compatibleChartTypes(chart, table), [chart, table]);
  const activeChart = useMemo(
    () => ({...chart, type: selected === "table" ? chart.type : selected}),
    [chart, selected],
  );
  const model = useMemo(
    () => buildChartModel(activeChart, table, language),
    [activeChart, table, language],
  );
  const text = switcherLabels[language];

  return (
    <figure className="chart" aria-label={model.title}>
      <div className="chart-heading">
        <figcaption>{model.title}</figcaption>
        {choices.length > 1 ? (
          <div className="chart-switcher" role="group" aria-label="Visualization type">
            {choices.map((choice) => (
              <button
                className={selected === choice ? "active" : ""}
                key={choice}
                onClick={() => setSelected(choice)}
                type="button"
              >
                {text[choice as keyof typeof text]}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {selected === "table" ? <small>{text.tableHint}</small> : (
        <ChartSurface model={model} language={language} empty={empty} />
      )}
    </figure>
  );
}

function ChartSurface({
  model,
  language,
  empty,
}: {
  model: ChartModel;
  language: VisualizationLanguage;
  empty: string;
}) {
  if (!model.series.some((series) => series.points.length)) return <small>{empty}</small>;
  if (model.type === "kpi") return <KpiChart model={model} />;
  if (model.type === "horizontal_bar") return <HorizontalBarChart model={model} language={language} />;
  if (model.type === "bar") return <VerticalBarChart model={model} language={language} />;
  if (model.type === "line" || model.type === "area") return <LineChart model={model} language={language} />;
  if (model.type === "scatter") return <ScatterChart model={model} language={language} />;
  return <DonutChart model={model} language={language} />;
}

function KpiChart({model}: {model: ChartModel}) {
  return (
    <div className="kpi-grid">
      {model.series.map((series) => (
        <div className="kpi" key={series.key}>
          <span>{series.label}</span>
          <strong dir="ltr">{formatTooltipValue(series.points[0].value, series.key)}</strong>
        </div>
      ))}
    </div>
  );
}

function HorizontalBarChart({model, language}: {model: ChartModel; language: VisualizationLanguage}) {
  const categories = categoryPoints(model);
  const width = 800;
  const left = 190;
  const right = 90;
  const top = 22;
  const rowHeight = Math.max(34, model.series.length * 23 + 12);
  const height = top + categories.length * rowHeight + 42;
  const values = model.series.flatMap((series) => series.points.map((point) => point.value));
  const maximum = Math.max(...values, 0);
  const plotWidth = width - left - right;
  const ticks = numericTicks(maximum);
  const barHeight = Math.min(18, (rowHeight - 10) / model.series.length);

  return (
    <ChartSvg width={width} height={height} label={model.title}>
      {ticks.map((tick) => {
        const x = left + scale(tick, 0, maximum, 0, plotWidth);
        return <g key={tick}><line className="chart-grid" x1={x} x2={x} y1={top} y2={height - 30} /><text className="chart-axis-label" x={x} y={height - 8} textAnchor="middle">{formatAxisNumber(tick)}</text></g>;
      })}
      {categories.map((category, categoryIndex) => (
        <g key={category.xKey}>
          <text className="chart-category-label" direction={language === "ar" ? "rtl" : "ltr"} x={left - 12} y={top + categoryIndex * rowHeight + rowHeight / 2 + 4} textAnchor="end">{category.xLabel}</text>
          {model.series.map((series, seriesIndex) => {
            const point = series.points.find((candidate) => candidate.xKey === category.xKey);
            if (!point) return null;
            const y = top + categoryIndex * rowHeight + 6 + seriesIndex * (barHeight + 3);
            const barWidth = Math.max(1, scale(point.value, 0, maximum, 0, plotWidth));
            return (
              <g key={`${series.key}-${category.xKey}`}>
                <rect className="chart-mark" fill={color(seriesIndex)} height={barHeight} rx="3" width={barWidth} x={left} y={y}>
                  <title>{tooltip(point, series.label, series.key, model.series.length > 1)}</title>
                </rect>
                {model.series.length === 1 ? <text className="chart-value-label" x={Math.min(left + barWidth + 8, width - right + 8)} y={y + barHeight - 3}>{formatAxisNumber(point.value)}</text> : null}
              </g>
            );
          })}
        </g>
      ))}
      <AxisTitles model={model} width={width} height={height} left={left} />
      <Legend language={language} model={model} width={width} y={height - 24} />
    </ChartSvg>
  );
}

function VerticalBarChart({model, language}: {model: ChartModel; language: VisualizationLanguage}) {
  const categories = categoryPoints(model);
  const width = 800;
  const height = 380;
  const left = 72;
  const right = 24;
  const top = 22;
  const bottom = 82;
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - right;
  const values = model.series.flatMap((series) => series.points.map((point) => point.value));
  const maximum = Math.max(...values, 0);
  const ticks = numericTicks(maximum);
  const groupWidth = plotWidth / Math.max(categories.length, 1);
  const barWidth = Math.min(44, (groupWidth * 0.72) / model.series.length);

  return (
    <ChartSvg width={width} height={height} label={model.title}>
      {ticks.map((tick) => {
        const y = top + plotHeight - scale(tick, 0, maximum, 0, plotHeight);
        return <g key={tick}><line className="chart-grid" x1={left} x2={width - right} y1={y} y2={y} /><text className="chart-axis-label" x={left - 10} y={y + 4} textAnchor="end">{formatAxisNumber(tick)}</text></g>;
      })}
      {categories.map((category, categoryIndex) => {
        const center = left + groupWidth * categoryIndex + groupWidth / 2;
        return (
          <g key={category.xKey}>
            <text className="chart-category-label" direction={language === "ar" ? "rtl" : "ltr"} x={center} y={height - bottom + 22} textAnchor="middle">{category.xLabel}</text>
            {model.series.map((series, seriesIndex) => {
              const point = series.points.find((candidate) => candidate.xKey === category.xKey);
              if (!point) return null;
              const markHeight = Math.max(1, scale(point.value, 0, maximum, 0, plotHeight));
              const x = center - (model.series.length * barWidth) / 2 + seriesIndex * barWidth;
              return <rect className="chart-mark" fill={color(seriesIndex)} height={markHeight} key={`${series.key}-${category.xKey}`} rx="3" width={Math.max(2, barWidth - 3)} x={x} y={top + plotHeight - markHeight}><title>{tooltip(point, series.label, series.key, model.series.length > 1)}</title></rect>;
            })}
          </g>
        );
      })}
      <AxisTitles model={model} width={width} height={height} left={left} />
      <Legend language={language} model={model} width={width} y={height - 24} />
    </ChartSvg>
  );
}

function LineChart({model, language}: {model: ChartModel; language: VisualizationLanguage}) {
  const width = 800;
  const height = 380;
  const left = 72;
  const right = 24;
  const top = 22;
  const bottom = 78;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const domain = categoryPoints(model);
  const values = model.series.flatMap((series) => series.points.map((point) => point.value));
  const minimum = Math.min(...values, 0);
  const maximum = Math.max(...values, 0);
  const yTicks = numericTicks(maximum, minimum);
  const xTicks = selectTicks(domain, 8);

  return (
    <ChartSvg width={width} height={height} label={model.title}>
      {yTicks.map((tick) => {
        const y = top + plotHeight - scale(tick, minimum, maximum, 0, plotHeight);
        return <g key={tick}><line className="chart-grid" x1={left} x2={width - right} y1={y} y2={y} /><text className="chart-axis-label" x={left - 10} y={y + 4} textAnchor="end">{formatAxisNumber(tick)}</text></g>;
      })}
      {xTicks.map((point) => {
        const index = domain.findIndex((candidate) => candidate.xKey === point.xKey);
        const x = left + scale(index, 0, Math.max(domain.length - 1, 1), 0, plotWidth);
        return <text className="chart-axis-label" direction={language === "ar" ? "rtl" : "ltr"} key={point.xKey} textAnchor="middle" x={x} y={height - bottom + 24}>{point.xLabel}</text>;
      })}
      {model.series.map((series, seriesIndex) => {
        const coordinates = series.points.map((point) => {
          const index = domain.findIndex((candidate) => candidate.xKey === point.xKey);
          return {
            point,
            x: left + scale(index, 0, Math.max(domain.length - 1, 1), 0, plotWidth),
            y: top + plotHeight - scale(point.value, minimum, maximum, 0, plotHeight),
          };
        });
        const line = coordinates.map((coordinate, index) => `${index ? "L" : "M"}${coordinate.x},${coordinate.y}`).join(" ");
        const area = `${line} L${coordinates.at(-1)?.x ?? left},${top + plotHeight} L${coordinates[0]?.x ?? left},${top + plotHeight} Z`;
        return (
          <g key={series.key}>
            {model.type === "area" ? <path d={area} fill={color(seriesIndex)} fillOpacity="0.12" /> : null}
            <path className="chart-line" d={line} fill="none" stroke={color(seriesIndex)} />
            {coordinates.map(({point, x, y}) => <circle className="chart-point-mark" cx={x} cy={y} fill={color(seriesIndex)} key={point.xKey} r="4"><title>{tooltip(point, series.label, series.key, model.series.length > 1)}</title></circle>)}
          </g>
        );
      })}
      <AxisTitles model={model} width={width} height={height} left={left} />
      <Legend language={language} model={model} width={width} y={height - 22} />
    </ChartSvg>
  );
}

function ScatterChart({model, language}: {model: ChartModel; language: VisualizationLanguage}) {
  const width = 800;
  const height = 380;
  const left = 72;
  const right = 24;
  const top = 22;
  const bottom = 68;
  const points = model.series.flatMap((series) => series.points.map((point) => ({series, point, x: Number(point.x)}))).filter((item) => Number.isFinite(item.x));
  const xValues = points.map((item) => item.x);
  const yValues = points.map((item) => item.point.value);
  const xMin = Math.min(...xValues, 0);
  const xMax = Math.max(...xValues, 0);
  const yMin = Math.min(...yValues, 0);
  const yMax = Math.max(...yValues, 0);
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  return (
    <ChartSvg width={width} height={height} label={model.title}>
      {numericTicks(yMax, yMin).map((tick) => {
        const y = top + plotHeight - scale(tick, yMin, yMax, 0, plotHeight);
        return <g key={tick}><line className="chart-grid" x1={left} x2={width - right} y1={y} y2={y} /><text className="chart-axis-label" x={left - 10} y={y + 4} textAnchor="end">{formatAxisNumber(tick)}</text></g>;
      })}
      {numericTicks(xMax, xMin).map((tick) => {
        const x = left + scale(tick, xMin, xMax, 0, plotWidth);
        return <g key={tick}><line className="chart-grid" x1={x} x2={x} y1={top} y2={top + plotHeight} /><text className="chart-axis-label" x={x} y={height - bottom + 22} textAnchor="middle">{formatAxisNumber(tick)}</text></g>;
      })}
      {points.map(({series, point, x}, index) => {
        const cx = left + scale(x, xMin, xMax, 0, plotWidth);
        const cy = top + plotHeight - scale(point.value, yMin, yMax, 0, plotHeight);
        return <circle className="chart-point-mark" cx={cx} cy={cy} fill={color(index)} key={`${point.xKey}-${index}`} r="5"><title>{`${formatAxisNumber(x)} · ${tooltip(point, series.label, series.key, false)}`}</title></circle>;
      })}
      <text className="chart-axis-title" direction={language === "ar" ? "rtl" : "ltr"} textAnchor="middle" x={left + plotWidth / 2} y={height - 10}>{model.xLabel}</text>
      <AxisTitles model={model} width={width} height={height} left={left} />
    </ChartSvg>
  );
}

function DonutChart({model, language}: {model: ChartModel; language: VisualizationLanguage}) {
  const points = model.series[0]?.points ?? [];
  const total = points.reduce((sum, point) => sum + Math.max(0, point.value), 0);
  let angle = -Math.PI / 2;
  return (
    <div className="donut-layout">
      <ChartSvg width={360} height={300} label={model.title}>
        {points.map((point, index) => {
          const start = angle;
          angle += total ? (Math.max(0, point.value) / total) * Math.PI * 2 : 0;
          return <path className="chart-mark" d={donutPath(180, 145, 112, 62, start, angle)} fill={color(index)} key={point.xKey}><title>{tooltip(point, model.series[0].label, model.series[0].key, false)}</title></path>;
        })}
      </ChartSvg>
      <div className="chart-legend donut-legend" dir={language === "ar" ? "rtl" : "ltr"}>
        {points.map((point, index) => <span key={point.xKey}><i style={{background: color(index)}} />{point.xLabel} <b dir="ltr">{formatAxisNumber(point.value)}</b></span>)}
      </div>
    </div>
  );
}

function ChartSvg({width, height, label, children}: {width: number; height: number; label: string; children: React.ReactNode}) {
  return <div className="chart-canvas"><svg aria-label={label} role="img" viewBox={`0 0 ${width} ${height}`}>{children}</svg></div>;
}

function AxisTitles({model, width, height, left}: {model: ChartModel; width: number; height: number; left: number}) {
  return <>{model.xLabel ? <text className="chart-axis-title" textAnchor="middle" x={(left + width) / 2} y={height - 1}>{model.xLabel}</text> : null}{model.yLabel ? <text className="chart-axis-title" textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`} x="14" y={height / 2}>{model.yLabel}</text> : null}</>;
}

function Legend({model, width, y, language}: {model: ChartModel; width: number; y: number; language: VisualizationLanguage}) {
  if (model.series.length <= 1) return null;
  const itemWidth = Math.min(150, width / model.series.length);
  const start = (width - itemWidth * model.series.length) / 2;
  const entries = model.series.map((series, index) => ({series, index}));
  if (language === "ar") entries.reverse();
  return <g className="chart-svg-legend" direction={language === "ar" ? "rtl" : "ltr"}>{entries.map(({series, index}, position) => <g key={series.key} transform={`translate(${start + position * itemWidth} ${y})`}><rect fill={color(index)} height="9" rx="2" width="9" x={language === "ar" ? itemWidth - 9 : 0} /><text textAnchor={language === "ar" ? "end" : "start"} x={language === "ar" ? itemWidth - 15 : 15} y="9">{series.label}</text></g>)}</g>;
}

function categoryPoints(model: ChartModel): ChartPoint[] {
  const points = new Map<string, ChartPoint>();
  for (const series of model.series) {
    for (const point of series.points) if (!points.has(point.xKey)) points.set(point.xKey, point);
  }
  return [...points.values()];
}

function numericTicks(maximum: number, minimum = 0): number[] {
  if (maximum === minimum) return [minimum];
  return Array.from({length: 5}, (_, index) => minimum + ((maximum - minimum) * index) / 4);
}

function scale(value: number, inputMin: number, inputMax: number, outputMin: number, outputMax: number): number {
  if (inputMax === inputMin) return outputMax / 2;
  return outputMin + ((value - inputMin) / (inputMax - inputMin)) * (outputMax - outputMin);
}

function color(index: number): string {
  return PALETTE[index % PALETTE.length];
}

function tooltip(point: ChartPoint, seriesLabel: string, column: string, includeSeries: boolean): string {
  return `${point.tooltipLabel}${includeSeries ? ` · ${seriesLabel}` : ""}: ${formatTooltipValue(point.value, column)}`;
}

function donutPath(cx: number, cy: number, outer: number, inner: number, start: number, end: number): string {
  const large = end - start > Math.PI ? 1 : 0;
  const point = (radius: number, angle: number) => `${cx + Math.cos(angle) * radius},${cy + Math.sin(angle) * radius}`;
  return `M${point(outer, start)} A${outer},${outer} 0 ${large} 1 ${point(outer, end)} L${point(inner, end)} A${inner},${inner} 0 ${large} 0 ${point(inner, start)} Z`;
}
