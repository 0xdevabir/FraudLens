"use client";

import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { useMemo, useState } from "react";

import { maskId } from "@/lib/format";

import { useSession } from "./session";

export interface GraphNode {
  id: string;
  kind: "wallet" | "agent";
  /** confirmed fraud */
  flagged?: boolean;
  frozen?: boolean;
  /** the wallet the picture is about */
  subject?: boolean;
  /** drawn hollow: connected, but not itself suspected (a takeover victim, say) */
  hollow?: boolean;
  /** scored above the mule threshold without being confirmed */
  suspected?: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  /** device links are drawn dashed: a shared handset, not money */
  kind: string;
  count?: number;
}

type Placed = GraphNode & SimulationNodeDatum;

const W = 760;

function colour(node: GraphNode): string {
  if (node.kind === "agent") return "var(--color-rule)";
  if (node.flagged) return "var(--color-bad)";
  if (node.suspected) return "var(--color-warn)";
  return "var(--color-fg-4)";
}

export const GRAPH_LEGEND = [
  { label: "Confirmed fraud", swatch: "var(--color-bad)" },
  { label: "Suspected mule", swatch: "var(--color-warn)" },
  { label: "Other wallet", swatch: "var(--color-fg-4)" },
  { label: "Agent", swatch: "var(--color-rule)", square: true },
  { label: "Frozen", ring: "var(--color-info)" },
];

/**
 * A static force layout: the simulation runs to rest before anything is drawn, so the picture
 * does not move under the reviewer's cursor.
 */
export function Graph({
  nodes, edges, height = 440, selected, onSelect,
}: {
  nodes: GraphNode[]; edges: GraphEdge[]; height?: number; selected?: string | null; onSelect?: (id: string) => void;
}) {
  const { revealed } = useSession();
  const [hover, setHover] = useState<string | null>(null);

  const layout = useMemo(() => {
    const placed: Placed[] = nodes.map((node) => ({ ...node }));
    const known = new Set(nodes.map((node) => node.id));
    const links = edges
      .filter((edge) => known.has(edge.source) && known.has(edge.target))
      .map((edge) => ({ ...edge }));
    const simulation = forceSimulation(placed)
      .force("link", forceLink<Placed, (typeof links)[number]>(links).id((node) => node.id).distance(46).strength(0.6))
      .force("charge", forceManyBody().strength(-150))
      .force("collide", forceCollide(15))
      .force("center", forceCenter(0, 0))
      .force("x", forceX(0).strength(0.05))
      .force("y", forceY(0).strength(0.08))
      .stop();
    for (let i = 0; i < 300; i++) simulation.tick();

    const xs = placed.map((node) => node.x ?? 0);
    const ys = placed.map((node) => node.y ?? 0);
    const [x0, x1, y0, y1] = [Math.min(...xs, 0), Math.max(...xs, 0), Math.min(...ys, 0), Math.max(...ys, 0)];
    const scale = Math.min((W - 80) / Math.max(x1 - x0, 1), (height - 60) / Math.max(y1 - y0, 1), 1.6);
    const at = new Map(
      placed.map((node) => [
        node.id,
        { x: W / 2 + ((node.x ?? 0) - (x0 + x1) / 2) * scale, y: height / 2 + ((node.y ?? 0) - (y0 + y1) / 2) * scale },
      ]),
    );
    return { at, links: links.map((link) => ({ kind: link.kind, count: link.count, a: (link.source as unknown as Placed).id, b: (link.target as unknown as Placed).id })) };
  }, [nodes, edges, height]);

  const focus = hover ?? selected ?? null;
  const near = useMemo(() => {
    if (!focus) return null;
    const set = new Set([focus]);
    for (const link of layout.links) {
      if (link.a === focus) set.add(link.b);
      if (link.b === focus) set.add(link.a);
    }
    return set;
  }, [focus, layout]);

  if (!nodes.length) return <div className="py-10 text-center text-sm text-fg-3">Nothing is connected to this wallet yet.</div>;

  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full select-none" role="img" aria-label="Network of connected wallets and agents">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="19" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--color-fg-4)" />
        </marker>
      </defs>
      {layout.links.map((link, i) => {
        const a = layout.at.get(link.a)!;
        const b = layout.at.get(link.b)!;
        const lit = !near || (near.has(link.a) && near.has(link.b) && (link.a === focus || link.b === focus));
        const device = link.kind === "device";
        return (
          <line
            key={i}
            x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke={device ? "var(--color-info)" : "var(--color-fg-4)"}
            strokeWidth={Math.min(1 + Math.log2(link.count ?? 1) * 0.6, 4)}
            strokeDasharray={device ? "4 3" : undefined}
            markerEnd={device ? undefined : "url(#arrow)"}
            opacity={lit ? 0.9 : 0.15}
          >
            <title>{device ? "Shared handset" : `${link.kind.replace(/_/g, " ")}${link.count ? ` × ${link.count}` : ""}`}</title>
          </line>
        );
      })}
      {nodes.map((node) => {
        const point = layout.at.get(node.id)!;
        const fill = colour(node);
        const lit = !near || near.has(node.id);
        const size = node.subject ? 11 : 8;
        return (
          <g
            key={node.id}
            transform={`translate(${point.x},${point.y})`}
            opacity={lit ? 1 : 0.25}
            onMouseEnter={() => setHover(node.id)}
            onMouseLeave={() => setHover(null)}
            onClick={() => onSelect?.(node.id)}
            className={onSelect ? "cursor-pointer" : undefined}
          >
            {node.frozen && <circle r={size + 5} fill="none" stroke="var(--color-info)" strokeWidth="2" />}
            {(node.subject || selected === node.id) && <circle r={size + 3} fill="none" stroke="var(--color-fg)" strokeWidth="1.5" />}
            {node.kind === "agent" ? (
              <rect x={-size + 1} y={-size + 1} width={size * 2 - 2} height={size * 2 - 2} rx="2" fill={fill} />
            ) : (
              <circle r={size} fill={node.hollow ? "var(--color-card)" : fill} stroke={fill} strokeWidth="2" />
            )}
            <text y={size + 12} textAnchor="middle" fontSize="10" fill="var(--color-fg-2)" paintOrder="stroke" stroke="var(--color-card)" strokeWidth="3">
              {revealed.has(node.id) ? node.id : maskId(node.id)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function GraphLegend({ extra }: { extra?: { label: string; dashed?: boolean; hollow?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-2">
      {GRAPH_LEGEND.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block size-2.5"
            style={{
              background: item.swatch ?? "transparent",
              borderRadius: item.square ? 2 : 999,
              boxShadow: item.ring ? `0 0 0 2px ${item.ring}` : undefined,
            }}
          />
          {item.label}
        </span>
      ))}
      {extra?.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          {item.dashed ? (
            <span className="inline-block w-4 border-t-2 border-dashed border-info" />
          ) : (
            <span className="inline-block size-2.5 rounded-full border-2 border-fg-3 bg-card" />
          )}
          {item.label}
        </span>
      ))}
    </div>
  );
}
