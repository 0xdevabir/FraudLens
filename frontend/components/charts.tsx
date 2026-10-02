"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";

import { num } from "@/lib/format";

import { cx } from "./ui";

export interface Series {
  name: string;
  color: string;
  values: (number | null | undefined)[];
  dashed?: boolean;
}

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Round axis maximum up to 1, 2, 2.5 or 5 times a power of ten. */
function niceMax(value: number): number {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return ([1, 2, 2.5, 5, 10].find((step) => step * power >= value) ?? 10) * power;
}

export function Legend({ items }: { items: { name: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
      {items.map((item) => (
        <span key={item.name} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-0.5 w-4"
            style={item.dashed ? { borderTop: `2px dashed ${item.color}` } : { background: item.color, height: 3 }}
          />
          {item.name}
        </span>
      ))}
    </div>
  );
}

const PAD = { top: 10, right: 12, bottom: 24, left: 64 };

interface FrameProps {
  height: number;
  count: number;
  /** position of point i along the x axis, 0..1 */
  at: (i: number) => number;
  yMax: number;
  format: (value: number) => string;
  xLabel: (i: number) => string;
  xTitle?: string;
  series: Series[];
  mark?: { index: number; label: string };
  children: (scale: { x: (i: number) => number; y: (v: number) => number; w: number; h: number }) => ReactNode;
}

/** Axes, gridlines, hover read-out. Lines and bars draw themselves inside it. */
function Frame({ height, count, at, yMax, format, xLabel, xTitle, series, mark, children }: FrameProps) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const w = Math.max(width - PAD.left - PAD.right, 10);
  const h = height - PAD.top - PAD.bottom - (xTitle ? 14 : 0);
  const x = (i: number) => PAD.left + at(i) * w;
  const y = (v: number) => PAD.top + h - (Math.min(v, yMax) / yMax) * h;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * yMax);
  // x labels at least 56px apart, wherever the points fall
  const labelled: number[] = [];
  for (let i = 0; i < count; i++) if (!labelled.length || x(i) - x(labelled[labelled.length - 1]) >= 56) labelled.push(i);

  function move(event: React.MouseEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const position = (event.clientX - box.left - PAD.left) / w;
    let best = 0;
    for (let i = 1; i < count; i++) if (Math.abs(at(i) - position) < Math.abs(at(best) - position)) best = i;
    setHover(best);
  }

  return (
    <div ref={ref} className="relative">
      {width > 0 && count > 0 && (
        <svg width={width} height={height} onMouseMove={move} onMouseLeave={() => setHover(null)} role="img">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={PAD.left + w} y1={y(tick)} y2={y(tick)} stroke="#e2e8f0" strokeDasharray={tick ? "3 3" : undefined} />
              <text x={PAD.left - 6} y={y(tick) + 3.5} textAnchor="end" fontSize="10.5" fill="#64748b">{format(tick)}</text>
            </g>
          ))}
          {labelled.map((i) => (
              <text key={i} x={x(i)} y={PAD.top + h + 15} textAnchor="middle" fontSize="10.5" fill="#64748b">{xLabel(i)}</text>
            ))}
          {xTitle && (
            <text x={PAD.left + w / 2} y={height - 3} textAnchor="middle" fontSize="10.5" fill="#64748b">{xTitle}</text>
          )}
          {children({ x, y, w, h })}
          {mark && mark.index >= 0 && mark.index < count && (
            <g>
              <line x1={x(mark.index)} x2={x(mark.index)} y1={PAD.top} y2={PAD.top + h} stroke="#0f172a" strokeDasharray="4 3" />
              <text
                x={x(mark.index)}
                y={PAD.top + 9}
                dx={at(mark.index) > 0.7 ? -5 : 5}
                textAnchor={at(mark.index) > 0.7 ? "end" : "start"}
                fontSize="10.5"
                fontWeight="600"
                fill="#0f172a"
              >
                {mark.label}
              </text>
            </g>
          )}
          {hover !== null && (
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + h} stroke="#94a3b8" pointerEvents="none" />
          )}
        </svg>
      )}
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-1 z-10 rounded-md border border-slate-200 bg-white/95 px-2 py-1.5 text-xs shadow-md"
          style={at(hover) > 0.6 ? { right: width - x(hover) + 8 } : { left: x(hover) + 8 }}
        >
          <div className="mb-0.5 font-medium text-slate-900">{xLabel(hover)}{xTitle ? ` ${xTitle.toLowerCase()}` : ""}</div>
          {series.map((s) => (
            <div key={s.name} className="flex items-center justify-between gap-3 whitespace-nowrap text-slate-600">
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-sm" style={{ background: s.color }} />
                {s.name}
              </span>
              <span className="font-medium tabular-nums text-slate-900">
                {s.values[hover] === null || s.values[hover] === undefined ? "–" : format(s.values[hover] as number)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ChartProps {
  series: Series[];
  labels: string[];
  height?: number;
  format?: (value: number) => string;
  yMax?: number;
  xTitle?: string;
  mark?: { index: number; label: string };
}

/** Lines over evenly spaced labels, or over numeric `xs` when the spacing itself means something. */
export function LineChart({ series, labels, xs, height = 220, format = num, yMax, xTitle, mark }: ChartProps & { xs?: number[] }) {
  const count = labels.length;
  const top = yMax ?? niceMax(Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0))));
  const lo = xs ? Math.min(...xs) : 0;
  const span = xs ? Math.max(...xs) - lo || 1 : Math.max(count - 1, 1);
  const at = (i: number) => ((xs ? xs[i] : i) - lo) / span;
  return (
    <Frame height={height} count={count} at={at} yMax={top} format={format} xLabel={(i) => labels[i]} xTitle={xTitle} series={series} mark={mark}>
      {({ x, y }) =>
        series.map((s) => {
          let path = "";
          let pen = false;
          s.values.forEach((value, i) => {
            if (value === null || value === undefined) {
              pen = false;
              return;
            }
            path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(value).toFixed(1)}`;
            pen = true;
          });
          return (
            <path key={s.name} d={path} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={s.dashed ? "5 4" : undefined} />
          );
        })
      }
    </Frame>
  );
}

/** One bar per label, the series stacked on top of each other. */
export function StackedBars({ series, labels, height = 220, format = num, yMax, mark }: ChartProps) {
  const count = labels.length;
  const totals = labels.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  const top = yMax ?? niceMax(Math.max(0, ...totals));
  const at = (i: number) => (i + 0.5) / Math.max(count, 1);
  return (
    <Frame height={height} count={count} at={at} yMax={top} format={format} xLabel={(i) => labels[i]} series={series} mark={mark}>
      {({ x, y, w }) => {
        const bar = Math.max(2, (w / Math.max(count, 1)) * 0.7);
        return labels.map((label, i) => {
          let base = 0;
          return (
            <g key={label + i}>
              {series.map((s) => {
                const value = s.values[i] ?? 0;
                const y1 = y(base + value);
                const rect = <rect key={s.name} x={x(i) - bar / 2} y={y1} width={bar} height={Math.max(0, y(base) - y1)} fill={s.color} />;
                base += value;
                return rect;
              })}
            </g>
          );
        });
      }}
    </Frame>
  );
}

/** Horizontal bars for ranked lists. `limit` draws a reference line, e.g. a drift threshold. */
export function HBars({
  rows, format = num, max, limit,
}: {
  rows: { label: ReactNode; value: number | null; color?: string; note?: ReactNode; muted?: boolean }[];
  format?: (value: number) => string;
  max?: number;
  limit?: { value: number; label: string };
}) {
  const top = max ?? Math.max(1e-9, limit?.value ?? 0, ...rows.map((row) => row.value ?? 0)) * 1.05;
  return (
    <div className="space-y-1.5 text-sm">
      {rows.map((row, i) => (
        <div key={i} className={cx("grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto] items-center gap-3", row.muted && "opacity-50")}>
          <div className="truncate text-slate-700">{row.label}</div>
          <div className="relative h-4 rounded-sm bg-slate-100">
            <div
              className="h-full rounded-sm"
              style={{ width: `${Math.min(100, ((row.value ?? 0) / top) * 100)}%`, background: row.color ?? "#475569" }}
            />
            {limit && (
              <div className="absolute inset-y-[-2px] w-px bg-slate-900" style={{ left: `${(limit.value / top) * 100}%` }} title={limit.label} />
            )}
          </div>
          <div className="whitespace-nowrap text-right text-xs tabular-nums text-slate-600">
            {row.value === null ? "–" : format(row.value)}
            {row.note && <span className="ml-1.5 text-slate-400">{row.note}</span>}
          </div>
        </div>
      ))}
      {limit && <div className="text-xs text-slate-500">Vertical line: {limit.label}</div>}
    </div>
  );
}

/** A 0–100 risk score as a small bar. */
export function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) return <span className="text-slate-400">–</span>;
  const color = score >= 75 ? "#dc2626" : score >= 50 ? "#f97316" : score >= 25 ? "#f59e0b" : "#94a3b8";
  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-block h-1.5 w-12 overflow-hidden rounded-full bg-slate-200">
        <span className="block h-full rounded-full" style={{ width: `${score}%`, background: color }} />
      </span>
      <span className="w-6 text-right text-xs font-medium tabular-nums text-slate-700">{score}</span>
    </span>
  );
}
