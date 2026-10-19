import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AfChip, AfChipButton, AfTabs, Icon } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { CARD_DELAY_MS, communitySlots, themeName, edgeKey, edgeLabelShown, firstSentence, litBy, placeCard, radiusScale, strongestLinks, type Hover } from "../lib/graph_focus";
import { useDebounced, useRead, type ReadState } from "../lib/hooks";
import { boundsOf, fitView, layoutGraph, settleFrames, toWorld, zoomAt, type Positioned, type ViewTransform } from "../lib/graph_layout";
import { milestones } from "../lib/milestones";
import { momentParams, navigate, noteAbout, useAt, useByReceived, type Route } from "../lib/nav";
import { thenNowView } from "../lib/shapes";
import type { Communities, Community, Day, EntityCard, EntityListItem, EntityRecord, Graph, GraphNode, ListOf, ThenNowRaw, TimelineItem } from "../lib/types";
import { capitalize, claimSentence, longDate, predicateWords, shortDate } from "../lib/words";
import { EmptyNotebook, EntityChip, KindBadge, NoteLink, ReadView, Section, Sparkline, openClaim, openNote, openRelation, useApp } from "../components/common";
import { ThenNowCard } from "../components/ThenNow";
import { TimeScrubber } from "../components/TimeScrubber";
import { OverviewView } from "./Overview";

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export function GraphView({ route }: { route: Route }) {
  const { api } = useApp();
  const [at] = useAt();
  const focus = useMemo(() => (route.params.get("focus") || "").split(",").filter(Boolean), [route.params]);
  const depth = Math.min(3, Math.max(1, Number(route.params.get("depth") || 1)));
  const selected = route.params.get("entity") || "";
  const day = DAY_RE.test(route.params.get("day") || "") ? route.params.get("day")! : "";
  const viewParam = route.params.get("view");
  const asList = viewParam === "list";
  // Explore's landing: with nothing focused, chosen or dated, the overview of what the notebook holds.
  const overview = viewParam === "overview" || (!viewParam && !focus.length && !selected && !day);
  const mode: ShowMode = overview ? "overview" : asList ? "list" : "picture";
  // One theme shown alone (a click in the legend or on a theme card in the overview).
  const isolated = route.params.get("community") || "";

  const go = (patch: Record<string, string | number | null>) => {
    const cur: Record<string, string | number | null> = { focus: focus.join(","), depth, entity: selected, day, view: viewParam, community: isolated };
    navigate("graph", { ...cur, ...patch });
  };
  // Read once for the legend, the colours and the hover card.
  const communities = useRead<Communities>(() => api.get<Communities>("note/communities"), [api]);

  const now = useRead<Graph>(() => api.get<Graph>("note/graph", { focus, depth, limit: 80 }), [api, focus.join(","), depth]);
  const [byReceived] = useByReceived();
  const then = useRead<Graph | null>(() => (at ? api.get<Graph>("note/graph", { focus, depth, limit: 80, ...momentParams(at, byReceived) }) : Promise.resolve(null)), [api, focus.join(","), depth, at, byReceived]);

  const bar = <FocusBar focus={focus} depth={depth} onFocus={(ids) => go({ focus: ids.join(","), entity: ids[ids.length - 1] || selected, view: overview ? "picture" : viewParam })} onDepth={(d) => go({ depth: d })} mode={mode} onMode={(m) => go({ view: m, ...(m === "overview" ? { focus: "", entity: "", day: "", community: "" } : {}) })} />;
  if (overview) {
    return (
      <div className="nt-graph nt-graph--wide">
        <div className="nt-graph__main">
          {bar}
          <OverviewView communities={communities} onTheme={(id) => go({ view: "picture", community: id })} onEntity={(id) => go({ view: "picture", focus: id, entity: id, community: "" })} />
        </div>
      </div>
    );
  }
  return (
    <div className="nt-graph">
      <div className="nt-graph__main">
        {bar}
        <div className="nt-graph__canvas">
          <ReadView read={now} what="the connections">
            {(g) =>
              !g.nodes.length ? (
                <div className="nt-center">
                  <EmptyNotebook />
                </div>
              ) : asList ? (
                <RelationList graph={g} then={at ? then.data : null} onSelect={(id) => go({ entity: id, day: "" })} />
              ) : (
                <GraphCanvas graph={g} then={at ? then.data : null} selected={selected} focus={focus} onSelect={(id) => go({ entity: id, day: "" })} communities={communities} isolated={isolated} onIsolate={(id) => go({ community: id })} />
              )
            }
          </ReadView>
        </div>
        <TimeScrubber archive onDay={(d) => go({ day: d, entity: "" })} />
      </div>
      <aside className="nt-graph__side" aria-label="Details">
        {day ? <DayPanel date={day} onClose={() => go({ day: "" })} /> : selected ? <EntityPanel id={selected} at={at} onFocus={() => go({ focus: selected, entity: selected })} slots={communitySlots(communities.data?.items || [])} onTheme={(cid) => go({ community: isolated === cid ? null : cid, view: asList ? "list" : "picture" })} /> : <p className="nt-muted nt-pad">Pick a person, project or idea to see how it evolved, then choose a date to see what you knew then.</p>}
      </aside>
    </div>
  );
}

type ShowMode = "overview" | "picture" | "list";
const MODES: Array<[ShowMode, string]> = [["overview", "Overview"], ["picture", "Picture"], ["list", "List"]];

function FocusBar({ focus, depth, onFocus, onDepth, mode, onMode }: { focus: string[]; depth: number; onFocus: (ids: string[]) => void; onDepth: (d: number) => void; mode: ShowMode; onMode: (m: ShowMode) => void }) {
  const { api } = useApp();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 200);
  const [hits, setHits] = useState<EntityListItem[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!dq) {
      setHits([]);
      return;
    }
    let live = true;
    api.get<ListOf<EntityListItem>>("note/entities", { q: dq, limit: 8, sort: "frequent" }).then(
      (d) => live && setHits(d.items || []),
      () => live && setHits([]),
    );
    return () => {
      live = false;
    };
  }, [api, dq]);
  useEffect(() => {
    const missing = focus.filter((id) => !names[id]);
    if (!missing.length) return;
    let live = true;
    Promise.all(missing.map((id) => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`).then((e) => [id, e.name] as const, () => [id, id] as const))).then((rows) => {
      if (live) setNames((prev) => ({ ...prev, ...Object.fromEntries(rows) }));
    });
    return () => {
      live = false;
    };
  }, [api, focus, names]);
  return (
    <div className="nt-focusbar">
      <div className="nt-search">
        <input className="nt-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Focus on a person, project, idea…" aria-label="Focus on" />
        {hits.length ? (
          <ul className="nt-suggest" role="listbox" aria-label="Matching entities">
            {hits.map((h) => (
              <li key={h.entity_id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    setNames((prev) => ({ ...prev, [h.entity_id]: h.name }));
                    onFocus([...focus.filter((f) => f !== h.entity_id), h.entity_id]);
                    setQ("");
                  }}
                >
                  <span>{h.name}</span>
                  <span className="nt-muted">{h.kind}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="nt-chips">
        {focus.map((id) => (
          <AfChip key={id} size="sm" tone="accent" onRemove={() => onFocus(focus.filter((f) => f !== id))} removeLabel={`Stop focusing on ${names[id] || "this"}`}>
            {names[id] || "…"}
          </AfChip>
        ))}
        {focus.length ? (
          <label className="nt-depth">
            <span>Reach</span>
            <select className="nt-input" value={depth} onChange={(e) => onDepth(Number(e.target.value))}>
              <option value={1}>Direct links</option>
              <option value={2}>Two steps</option>
              <option value={3}>Three steps</option>
            </select>
          </label>
        ) : mode === "overview" ? null : (
          <span className="nt-muted">Showing everything you know.</span>
        )}
      </div>
      <div className="nt-segmented nt-focusbar__end" role="radiogroup" aria-label="Show as">
        {MODES.map(([m, label]) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} className="nt-segmented__opt" onClick={() => onMode(m)}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The same connections as a readable table (every graph has its list; keyboard and screen readers). */
function RelationList({ graph, then, onSelect }: { graph: Graph; then: Graph | null; onSelect: (id: string) => void }) {
  const names = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.name])), [graph]);
  const thenNodes = useMemo(() => (then ? new Set(then.nodes.map((n) => n.id)) : null), [then]);
  const thenEdges = useMemo(() => (then ? new Map(then.edges.map((e) => [`${e.source}|${e.target}|${e.predicate}`, e])) : null), [then]);
  const linked = new Set(graph.edges.flatMap((e) => [e.source, e.target]));
  const alone = graph.nodes.filter((n) => !linked.has(n.id));
  const rows = [...graph.edges].sort((a, b) => (names.get(a.source) || "").localeCompare(names.get(b.source) || "") || predicateWords(a.predicate).localeCompare(predicateWords(b.predicate)));
  const entity = (id: string) => (
    <button type="button" className="nt-link" onClick={() => onSelect(id)}>
      <span className="nt-link__title">{names.get(id) || "Someone"}</span>
    </button>
  );
  return (
    <div className="nt-rels__wrap">
      <table className="nt-rels">
        <caption>{`${graph.edges.length} ${graph.edges.length === 1 ? "link" : "links"} between ${graph.nodes.length} people, projects and ideas`}</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">Link</th>
            <th scope="col">To</th>
            <th scope="col">Since</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => {
            const te = thenEdges ? thenEdges.get(`${e.source}|${e.target}|${e.predicate}`) : e;
            const state = !te ? "not known yet" : te.active ? "believed" : "no longer believed";
            return (
              <tr key={`${e.source}|${e.target}|${e.predicate}`} className={!te ? "nt-rels__future" : !te.active ? "nt-rels__old" : undefined}>
                <td>{entity(e.source)}</td>
                <td>
                  <button type="button" className="nt-link" onClick={() => openRelation(e.source, e.target)} title="How this link changed">
                    <span className="nt-link__title">{`${predicateWords(e.predicate)}${(te?.count ?? e.count) > 1 ? ` ×${te?.count ?? e.count}` : ""}`}</span>
                  </button>
                </td>
                <td>{entity(e.target)}</td>
                <td className="nt-link__date">{shortDate(e.first_seen)}</td>
                <td>{state}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {alone.length ? (
        <p className="nt-chips">
          <span className="nt-muted">Not linked yet:</span>
          {alone.map((n) => (
            <AfChipButton key={n.id} size="sm" tone="muted" onClick={() => onSelect(n.id)} ariaLabel={`${n.name}${thenNodes && !thenNodes.has(n.id) ? ", not known yet at that time" : ""}`}>
              {n.name}
            </AfChipButton>
          ))}
        </p>
      ) : null}
    </div>
  );
}

/** The canvas's size in pixels (it fills the space the layout gives it). */
function useSize<T extends HTMLElement>(): [React.RefObject<T>, { w: number; h: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 800, h: 520 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      // The canvas box has its own height (CSS); the picture takes all of it.
      const w = Math.max(280, Math.round(el.clientWidth));
      const h = Math.max(240, Math.round(el.clientHeight));
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

/** The layout's own space: fixed, so a graph lands the same way at every window size (the view fits it). */
const WORLD = { w: 1000, h: 700 };
/** The closest a theme shown alone is fitted. */
const ISOLATE_MAX_ZOOM = 1.5;

function reducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

type Drag = { kind: "pan"; sx: number; sy: number; vx: number; vy: number } | { kind: "node"; id: string; sx: number; sy: number; moved: boolean } | null;

function GraphCanvas({ graph, then, selected, focus, onSelect, communities, isolated, onIsolate }: { graph: Graph; then: Graph | null; selected: string; focus: string[]; onSelect: (id: string) => void; communities: ReadState<Communities>; isolated: string; onIsolate: (id: string | null) => void }) {
  const [ref, size] = useSize<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement>(null);
  // Node size by note count, square-root scale (area grows with the count).
  const radius = useMemo(() => radiusScale(Math.max(1, ...graph.nodes.map((n) => n.note_count || 0))), [graph]);
  // Laid out once per graph (not per date: the picture stays where it is when the date changes).
  const laid = useMemo(() => layoutGraph({ nodes: graph.nodes.map((n) => ({ id: n.id, weight: n.note_count, r: radius(n.note_count) })), edges: graph.edges, width: WORLD.w, height: WORLD.h, focus }), [graph, focus, radius]);
  const [placed, setPlaced] = useState<Positioned[]>(laid);
  const [view, setView] = useState<ViewTransform>({ k: 1, x: 0, y: 0 });
  const drag = useRef<Drag>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number } | null>(null);
  const slots = useMemo(() => communitySlots(communities.data?.items || []), [communities.data]);
  const communityOf = useMemo(() => new Map((communities.data?.items || []).map((c) => [c.community_id, c])), [communities.data]);
  // The communities in this picture, in colour order (the legend lists them; a click isolates one).
  const present = useMemo(() => {
    const ids = new Set(graph.nodes.map((n) => n.community_id).filter(Boolean) as string[]);
    return (communities.data?.items || []).filter((c) => ids.has(c.community_id)).sort((a, b) => (slots.get(a.community_id) || 99) - (slots.get(b.community_id) || 99));
  }, [graph, communities.data, slots]);
  const inIsolated = (id: string) => !isolated || graph.nodes.find((n) => n.id === id)?.community_id === isolated;
  // The legend sits along the bottom edge: the fit leaves it its strip.
  const LEGEND = size.w < 600 ? 52 : 34;
  const fitNodes = (nodes: Positioned[]) => {
    // A theme alone is brought closer, never so close its circles fill the view.
    const iso = isolated ? nodes.filter((p) => inIsolated(p.id)) : [];
    return iso.length ? fitView(boundsOf(iso), size.w, size.h - LEGEND, 48, ISOLATE_MAX_ZOOM) : fitView(boundsOf(nodes), size.w, size.h - LEGEND);
  };
  const fitNow = (nodes: Positioned[] = placed) => setView(fitNodes(nodes));
  // Fit on load and whenever the data, the space or the isolated community changes.
  useEffect(() => {
    setPlaced(laid);
    setView(fitNodes(laid));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [laid, size.w, size.h, isolated]);
  const pos = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const names = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.name])), [graph]);
  const thenNodes = useMemo(() => (then ? new Set(then.nodes.map((n) => n.id)) : null), [then]);
  const thenEdges = useMemo(() => (then ? new Map(then.edges.map((e) => [edgeKey(e), e])) : null), [then]);
  const pairs = new Map<string, number>();

  // Hover: everything but the hovered node, its links and its neighbours fades; after a rest, a card.
  const [hover, setHover] = useState<Hover>(null);
  const [card, setCard] = useState<{ id: string; x: number; y: number } | null>(null);
  const cardTimer = useRef<number | null>(null);
  const lastPointer = useRef({ x: 0, y: 0 });
  const lit = useMemo(() => litBy(hover, graph.edges), [hover, graph.edges]);
  const clearCard = () => {
    if (cardTimer.current !== null) window.clearTimeout(cardTimer.current);
    cardTimer.current = null;
    setCard(null);
  };
  useEffect(() => () => clearCard(), []);
  const hoverNode = (id: string | null, at?: { x: number; y: number }) => {
    if (id === (hover?.kind === "node" ? hover.id : null) && id) return;
    clearCard();
    setHover(id ? { kind: "node", id } : null);
    if (!id) return;
    cardTimer.current = window.setTimeout(() => {
      const p = at || lastPointer.current;
      setCard({ id, x: p.x, y: p.y });
    }, CARD_DELAY_MS);
  };
  const hoverEdge = (key: string | null) => {
    if (!key) return;
    const [source, target] = key.split("|");
    if (hover?.kind === "edge" && hover.key === key) return;
    clearCard();
    setHover({ kind: "edge", key, source, target });
  };
  /** Pointer over the picture (a mouse or pen, never a finger): what is under it lights up. */
  const trackHover = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "touch" || drag.current || pointers.current.size) return;
    lastPointer.current = { x: e.clientX, y: e.clientY };
    const t = e.target as Element;
    const node = t.closest?.("[data-node]");
    if (node) return hoverNode(node.getAttribute("data-node"));
    const edge = t.closest?.("[data-edge]");
    if (edge) return hoverEdge(edge.getAttribute("data-edge"));
    if (hover) {
      clearCard();
      setHover(null);
    }
  };

  const local = (e: { clientX: number; clientY: number }) => {
    const r = svgRef.current?.getBoundingClientRect();
    return { x: e.clientX - (r?.left || 0), y: e.clientY - (r?.top || 0) };
  };
  // Wheel and trackpad pinch (a pinch arrives as a wheel with ctrlKey): zoom about the pointer.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = local(e);
      setView((v) => zoomAt(v, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), p.x, p.y));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const settle = (id: string) => {
    const p = pos.get(id);
    if (!p) return;
    const frames = settleFrames(placed, graph.edges, { id, x: p.x, y: p.y });
    if (reducedMotion()) {
      setPlaced(frames[frames.length - 1]);
      return;
    }
    let i = 0;
    const step = () => {
      setPlaced(frames[i]);
      i += 1;
      if (i < frames.length) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    const p = local(e);
    pointers.current.set(e.pointerId, p);
    svgRef.current?.setPointerCapture?.(e.pointerId);
    clearCard();
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y) };
      drag.current = null;
      return;
    }
    const node = (e.target as Element).closest?.("[data-node]");
    if (node) drag.current = { kind: "node", id: node.getAttribute("data-node") || "", sx: p.x, sy: p.y, moved: false };
    else if ((e.target as Element).closest?.("[data-edge]")) drag.current = null;
    else drag.current = { kind: "pan", sx: p.x, sy: p.y, vx: view.x, vy: view.y };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pointers.current.has(e.pointerId)) return trackHover(e);
    const p = local(e);
    pointers.current.set(e.pointerId, p);
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const factor = d / Math.max(1, pinch.current.d);
      pinch.current = { d };
      setView((v) => zoomAt(v, factor, (a.x + b.x) / 2, (a.y + b.y) / 2));
      return;
    }
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan") {
      setView((v) => ({ ...v, x: d.vx + p.x - d.sx, y: d.vy + p.y - d.sy }));
      return;
    }
    if (!d.moved && Math.hypot(p.x - d.sx, p.y - d.sy) < 4) return;
    d.moved = true;
    const w = toWorld(view, p.x, p.y);
    setPlaced((prev) => prev.map((n) => (n.id === d.id ? { ...n, x: Math.round(w.x), y: Math.round(w.y) } : n)));
  };
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "node") {
      if (d.moved) settle(d.id);
      else onSelect(d.id);
    }
  };
  // Labels and their halo keep their screen size whatever the zoom.
  const fontPx = (px: number) => ({ fontSize: `calc(${px}px * var(--font-scale, 1) / ${view.k.toFixed(3)})`, strokeWidth: 3 / view.k });
  const slotClass = (cid: string | null | undefined) => ` nt-c${(cid && slots.get(cid)) || 0}`;
  const cardNode = card ? graph.nodes.find((n) => n.id === card.id) : null;

  return (
    <>
    <CommunityLegend communities={communities} present={present} slots={slots} isolated={isolated} onIsolate={onIsolate} />
    <div ref={ref} className="nt-canvas">
      <div className="nt-canvas__tools" role="group" aria-label="Zoom">
        <button type="button" className="nt-icon-btn" aria-label="Zoom in" title="Zoom in" onClick={() => setView((v) => zoomAt(v, 1.25, size.w / 2, size.h / 2))}>
          <Icon name="zoomIn" size={16} />
        </button>
        <button type="button" className="nt-icon-btn" aria-label="Zoom out" title="Zoom out" onClick={() => setView((v) => zoomAt(v, 0.8, size.w / 2, size.h / 2))}>
          <Icon name="zoomOut" size={16} />
        </button>
        <button type="button" className="nt-icon-btn" aria-label="Fit to view" title="Fit to view (or double-click the background)" onClick={() => fitNow()}>
          <Icon name="fitView" size={16} />
        </button>
      </div>
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        viewBox={`0 0 ${size.w} ${size.h}`}
        role="img"
        aria-label={`Graph of ${graph.nodes.length} entities and ${graph.edges.length} links; drag to move, scroll or pinch to zoom`}
        className={`nt-canvas__svg${lit ? " nt-canvas__svg--hover" : ""}`}
        data-zoom={view.k.toFixed(2)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => {
          if (drag.current) return;
          clearCard();
          setHover(null);
        }}
        onDoubleClick={(e) => {
          if (!(e.target as Element).closest?.("[data-node], [data-edge]")) fitNow();
        }}
      >
        <defs>
          <marker id="nt-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="nt-arrow" />
          </marker>
        </defs>
        <rect className="nt-canvas__bg" x={0} y={0} width={size.w} height={size.h} />
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {graph.edges.map((e) => {
            const a = pos.get(e.source);
            const b = pos.get(e.target);
            if (!a || !b) return null;
            const key = edgeKey(e);
            const te = thenEdges ? thenEdges.get(key) : e;
            const existed = Boolean(te);
            const active = te ? te.active : false;
            const pairKey = [e.source, e.target].sort().join("|");
            const nth = pairs.get(pairKey) || 0;
            pairs.set(pairKey, nth + 1);
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const len = Math.max(1, Math.hypot(dx, dy));
            const ux = dx / len;
            const uy = dy / len;
            const sx = a.x + ux * a.r;
            const sy = a.y + uy * a.r;
            const tx = b.x - ux * (b.r + 2);
            const ty = b.y - uy * (b.r + 2);
            const off = nth * 14;
            const mx = (sx + tx) / 2 - uy * off;
            const my = (sy + ty) / 2 + ux * off;
            const count = te ? te.count : e.count;
            const isLit = Boolean(lit?.edges.has(key));
            const faded = lit ? !isLit : false;
            const out = !lit && Boolean(isolated) && !(inIsolated(e.source) && inIsolated(e.target));
            return (
              <g key={key} data-edge={key} className={`nt-edge${existed ? "" : " nt-edge--future"}${existed && !active ? " nt-edge--old" : ""}${isLit ? " nt-edge--lit" : ""}${faded ? " nt-fade" : ""}${out ? " nt-out" : ""}`}>
                <line x1={sx} y1={sy} x2={tx} y2={ty} markerEnd="url(#nt-arrow)" vectorEffect="non-scaling-stroke" />
                <line x1={sx} y1={sy} x2={tx} y2={ty} className="nt-edge__hit" onClick={() => openRelation(e.source, e.target)}>
                  <title>{`${predicateWords(e.predicate)}${count > 1 ? ` · ${count} times` : ""} · since ${shortDate(e.first_seen)}, open how this link changed`}</title>
                </line>
                {edgeLabelShown(view.k, isLit, faded || out, Math.hypot(tx - sx, ty - sy) * view.k) ? (
                  <text x={mx} y={my - 4 / view.k} textAnchor="middle" className="nt-edge__label" style={fontPx(10.5)} onClick={() => openRelation(e.source, e.target)}>
                    {predicateWords(e.predicate)}
                    {count > 1 ? ` ×${count}` : ""}
                  </text>
                ) : null}
              </g>
            );
          })}
          {graph.nodes.map((n) => {
            const p = pos.get(n.id);
            if (!p) return null;
            const existed = !thenNodes || thenNodes.has(n.id);
            const faded = lit ? !lit.nodes.has(n.id) : false;
            const out = !lit && !inIsolated(n.id);
            const community = n.community_id ? communityOf.get(n.community_id) : null;
            return (
              <g
                key={n.id}
                data-node={n.id}
                className={`nt-node nt-node--${n.kind}${slotClass(n.community_id)}${existed ? "" : " nt-node--future"}${selected === n.id ? " nt-node--selected" : ""}${focus.includes(n.id) ? " nt-node--focus" : ""}${faded ? " nt-fade" : ""}${out ? " nt-out" : ""}${lit?.nodes.has(n.id) ? " nt-node--lit" : ""}`}
                transform={`translate(${p.x},${p.y})`}
                tabIndex={0}
                role="button"
                aria-label={`${n.name}, ${n.kind}${community ? `, in ${themeName(community)}` : ""}${existed ? "" : ", not known yet at that time"}`}
                onFocus={(ev) => {
                  const r = (ev.currentTarget as Element).getBoundingClientRect();
                  hoverNode(n.id, { x: r.left + r.width / 2, y: r.top + r.height / 2 });
                }}
                onBlur={() => {
                  clearCard();
                  setHover(null);
                }}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter" || ev.key === " ") {
                    ev.preventDefault();
                    onSelect(n.id);
                  }
                  if (ev.key === "Escape") clearCard();
                }}
              >
                <title>{`${n.name} · ${n.kind}${n.note_count ? ` · ${n.note_count} ${n.note_count === 1 ? "note" : "notes"}` : ""}`}</title>
                {/* The focus ring: a circle around the node, shown on keyboard focus (never a rectangle). */}
                <circle className="nt-node__ring" r={p.r + 5 / view.k} vectorEffect="non-scaling-stroke" />
                <circle className="nt-node__dot" r={p.r} vectorEffect="non-scaling-stroke" />
                <text y={p.r + 14 / view.k} textAnchor="middle" style={fontPx(12)}>
                  {n.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      {card && cardNode ? <HoverCard node={cardNode} at={card} edges={graph.edges} names={names} community={cardNode.community_id ? communityOf.get(cardNode.community_id) || null : null} slot={(cardNode.community_id && slots.get(cardNode.community_id)) || 0} /> : null}
      <p className="nt-legend">
        <span className="nt-legend__item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="nt-legend__solid" />
          </svg>
          believed
        </span>
        <span className="nt-legend__item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="nt-legend__dashed" />
          </svg>
          no longer believed
        </span>
        <span className="nt-legend__item">size: how often you noted it</span>
        {then ? <span className="nt-legend__item nt-legend__item--faded">faded: not known yet</span> : null}
        <span className="nt-legend__item nt-legend__hint">hover to see links · zoom in for their words · double-click to fit</span>
      </p>
    </div>
    </>
  );
}

/** The themes in the picture, each with its colour: a click shows that theme alone (and again, everything). */
function CommunityLegend({ communities, present, slots, isolated, onIsolate }: { communities: ReadState<Communities>; present: Community[]; slots: Map<string, number>; isolated: string; onIsolate: (id: string | null) => void }) {
  const row = useRef<HTMLDivElement>(null);
  // On a phone the row scrolls sideways: the theme shown alone is brought into view.
  useEffect(() => {
    const on = row.current?.querySelector<HTMLElement>("[aria-pressed=true]");
    const box = row.current;
    if (on && box) box.scrollLeft = Math.max(0, on.offsetLeft - 12);
  }, [isolated, present.length]);
  if (communities.loading && !communities.data) return null;
  if (!communities.data) {
    return (
      <div className="nt-themes">
        <span className="nt-error-text">{communities.error instanceof ApiError && (communities.error.status === 404 || communities.error.status === 501) ? "Your notebook cannot show themes yet." : errorSentence(communities.error, "the themes")}</span>
        <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={communities.reload}>
          Retry
        </button>
      </div>
    );
  }
  if (!present.length) return null;
  return (
    <div ref={row} className="nt-themes" role="group" aria-label="Themes: choose one to show it alone">
      {present.map((c) => {
        const on = isolated === c.community_id;
        return (
          <button key={c.community_id} type="button" className={`nt-theme nt-c${slots.get(c.community_id) || 0}`} aria-pressed={on} title={on ? "Show every theme" : `Show ${themeName(c)} alone`} onClick={() => onIsolate(on ? null : c.community_id)}>
            <span className="nt-theme__dot" aria-hidden="true" />
            <span className="nt-theme__label">{themeName(c)}</span>
          </button>
        );
      })}
      {isolated ? (
        <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => onIsolate(null)}>
          Show all
        </button>
      ) : null}
    </div>
  );
}

/** The card a resting pointer (or keyboard focus) opens beside a node; kept inside the window. */
function HoverCard({ node, at, edges, names, community, slot }: { node: GraphNode; at: { x: number; y: number }; edges: Graph["edges"]; names: Map<string, string>; community: Community | null; slot: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos(placeCard(at.x, at.y, r.width, r.height, { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }));
  }, [at.x, at.y, node.id]);
  const links = strongestLinks(node.id, node.name, edges, names, 3);
  const summary = firstSentence(node.summary);
  const count = node.note_count || 0;
  return createPortal(
    <div ref={ref} className="nt-hovercard" role="tooltip" style={pos ? { left: pos.x, top: pos.y } : { left: -9999, top: 0, visibility: "hidden" }}>
      <div className="nt-hovercard__head">
        <span className="nt-hovercard__name">{node.name}</span>
        <span className="nt-kind">{node.kind}</span>
      </div>
      {summary ? <p className="nt-hovercard__summary">{summary}</p> : null}
      <p className="nt-hovercard__meta">
        {`${count} ${count === 1 ? "note" : "notes"}`}
        {node.first_seen ? ` · first ${shortDate(node.first_seen)}` : ""}
        {node.last_seen ? ` · latest ${shortDate(node.last_seen)}` : ""}
      </p>
      {community ? (
        <p className={`nt-hovercard__theme nt-c${slot}`}>
          <span className="nt-theme__dot" aria-hidden="true" />
          <span>{themeName(community)}</span>
        </p>
      ) : null}
      {links.length ? (
        <ul className="nt-hovercard__links" aria-label="Strongest links">
          {links.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
    </div>,
    document.body,
  );
}

type PanelTab = "evolved" | "changes" | "actions" | "sources" | "links";

/** The topic portrait: how it evolved (first), changes, open actions, sources and links. */
export function EntityPanel({ id, at, onFocus, slots, onTheme }: { id: string; at: string | null; onFocus?: () => void; slots?: Map<string, number>; onTheme?: (communityId: string) => void }) {
  const { api } = useApp();
  const [, setAt] = useAt();
  const [tab, setTab] = useState<PanelTab>("evolved");
  const [byReceived] = useByReceived();
  const card = useRead<EntityCard>(() => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`, momentParams(at, byReceived)), [api, id, at, byReceived]);
  const timeline = useRead<{ items: TimelineItem[] }>(() => api.get<{ items: TimelineItem[] }>(`note/entities/${encodeURIComponent(id)}/timeline`), [api, id]);
  const thenNow = useRead<ThenNowRaw>(() => api.get<ThenNowRaw>(`note/entities/${encodeURIComponent(id)}/then-now`), [api, id]);
  // Milestones come from the card as it is now, so the list keeps its shape when the date changes.
  const current = useRead<EntityCard | null>(() => (at ? api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`) : Promise.resolve(null)), [api, id, Boolean(at)]);
  const tn = useMemo(() => thenNowView(thenNow.data), [thenNow.data]);
  const noteDates = useMemo(() => (timeline.data?.items || []).filter((i) => i.type === "note").map((i) => i.at), [timeline.data]);
  const range = useMemo(() => {
    const ts = noteDates.map((d) => Date.parse(d)).filter(Number.isFinite);
    const to = Date.now();
    const from = Math.min(to - 90 * 86400_000, ...ts);
    return { from, to };
  }, [noteDates]);
  const steps = useMemo(() => milestones(at ? current.data : card.data, tn, timeline.data?.items), [at, current.data, card.data, tn, timeline.data]);

  // Asked for a moment before this entry existed: say so, plainly (the server refuses that read).
  if (at && card.data === null && !card.loading && card.error instanceof ApiError && (card.error.status === 400 || card.error.status === 404)) {
    return (
      <div className="nt-panel">
        <p className="nt-notyet">{`This was not in your notebook yet on ${longDate(at)}.`}</p>
        <div className="nt-actions">
          <button type="button" className="nt-btn" onClick={() => setAt(null)}>
            Back to now
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="nt-panel">
      <ReadView read={card} what="this">
        {(e) => {
          const tabs = [
            { id: "evolved", label: "How it evolved" },
            { id: "changes", label: "Changes" },
            { id: "actions", label: "Open actions" },
            { id: "sources", label: "Sources" },
            { id: "links", label: "Links" },
          ];
          return (
            <>
              <header className="nt-panel__head">
                <div>
                  <h2>{e.name}</h2>
                  <p className="nt-meta">
                    {capitalize(e.kind)}
                    {e.aliases?.length ? ` · also ${e.aliases.join(", ")}` : ""}
                    {at ? (byReceived ? ` · as held on ${longDate(at)}` : ` · as of ${longDate(at)}`) : ""}
                  </p>
                  {e.community && onTheme ? (
                    <button type="button" className={`nt-theme nt-theme--inline nt-c${slots?.get(e.community.community_id) || 0}`} title={`Show ${themeName(e.community)} in the picture`} onClick={() => onTheme(e.community!.community_id)}>
                      <span className="nt-theme__dot" aria-hidden="true" />
                      <span className="nt-theme__label">{themeName(e.community)}</span>
                    </button>
                  ) : null}
                  {(e.merged_from || []).length ? <MergedFrom ids={e.merged_from || []} /> : null}
                </div>
                {timeline.data ? <Sparkline dates={noteDates} from={range.from} to={range.to} label={`${e.name} activity`} /> : null}
              </header>
              {/* With a summary history, the latest view closes "How it evolved"; without one the summary shows here. */}
              {e.summary && !(e.summary_history || []).length ? <p className="nt-summary">{e.summary}</p> : null}
              <p className="nt-muted">
                {e.note_count ?? 0} {e.note_count === 1 ? "note" : "notes"}
                {e.first_seen ? ` · first ${shortDate(e.first_seen)}` : ""}
                {e.last_seen ? ` · latest ${shortDate(e.last_seen)}` : ""}
              </p>
              <AfTabs tabs={tabs} value={tab} onChange={(v) => setTab(v as PanelTab)} ariaLabel={`About ${e.name}`} idBase={`nt-entity-${e.entity_id.replace(/[^A-Za-z0-9_-]/g, "-")}`}>
                {tab === "evolved" ? <Evolved steps={steps} at={at} loading={timeline.loading || thenNow.loading} /> : null}
                {tab === "changes" ? (
                  <>
                    {(e.beliefs || []).length ? (
                      <Section title={at ? "What you believed then" : "What you believe"}>
                        <ul className="nt-list">
                          {(e.beliefs || []).map((c) => (
                            <li key={c.claim_id} className="nt-list__row">
                              <span>{claimSentence(c)}</span>
                              <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(c.claim_id)}>
                                Why
                              </button>
                            </li>
                          ))}
                        </ul>
                      </Section>
                    ) : (
                      <p className="nt-muted">Nothing stated about it yet.</p>
                    )}
                    {tn && (tn.earliest || tn.latest) ? (
                      <Section title="Then and now">
                        <ThenNowCard data={{ ...tn, turning: [] }} />
                      </Section>
                    ) : null}
                  </>
                ) : null}
                {tab === "actions" ? (
                  (e.open_tasks || []).length ? (
                    <ul className="nt-list">
                      {(e.open_tasks || []).map((t) => (
                        <li key={t.task_id} className="nt-item">
                          <p className="nt-item__title">{t.text}</p>
                          {t.due ? <p className="nt-muted">Due {longDate(t.due)}</p> : null}
                          <div className="nt-actions">
                            <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(t.note_id)}>
                              Open note
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="nt-muted">No open actions.</p>
                  )
                ) : null}
                {tab === "sources" ? (
                  (e.notes || []).length ? (
                    <ul className="nt-list">
                      {(e.notes || []).map((n) => (
                        <li key={n.note_id} className="nt-list__row">
                          <NoteLink note={n} />
                          <KindBadge kind={n.kind} />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="nt-muted">No notes yet.</p>
                  )
                ) : null}
                {tab === "links" ? (
                  (e.relations || []).length ? (
                    <ul className="nt-list">
                      {(e.relations || []).map((r) => (
                        <li key={`${r.other_id}|${r.predicate}|${r.direction}`} className="nt-list__row">
                          <button type="button" className={`nt-link${r.active ? "" : " nt-struck"}`} onClick={() => openRelation(e.entity_id, r.other_id)}>
                            <span className="nt-link__title">{r.direction === "out" ? `${predicateWords(r.predicate)} ${r.other_name}` : `${r.other_name} ${predicateWords(r.predicate)} ${e.name}`}</span>
                            <span className="nt-link__date">{r.count > 1 ? `${r.count} times` : shortDate(r.first_seen)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="nt-muted">Not linked to anything yet.</p>
                  )
                ) : null}
              </AfTabs>
              {onFocus ? (
                <div className="nt-actions">
                  <button type="button" className="nt-btn" onClick={onFocus}>
                    Focus here
                  </button>
                  <button type="button" className="nt-btn" onClick={() => noteAbout("entity", e.entity_id, e.name)}>
                    Add a note
                  </button>
                </div>
              ) : null}
            </>
          );
        }}
      </ReadView>
    </div>
  );
}

/** The entries joined into this one, by their own names (`as_merged`: the entry's own record, W10). */
function MergedFrom({ ids }: { ids: string[] }) {
  const { api } = useApp();
  const entries = useRead<EntityRecord[]>(() => Promise.all(ids.slice(0, 5).map((id) => api.get<EntityRecord>(`note/entities/${encodeURIComponent(id)}`, { as_merged: true }))), [api, ids.join(",")]);
  if (!entries.data?.length) return null;
  return <p className="nt-muted">{`Includes ${entries.data.map((r) => `${r.name} (${r.note_count === 1 ? "1 note" : `${r.note_count} notes`})`).join(", ")}`}</p>;
}

/** Milestones, oldest first; those after the chosen date stay in place, faded, so nothing jumps. */
function Evolved({ steps, at, loading }: { steps: ReturnType<typeof milestones>; at: string | null; loading: boolean }) {
  if (!steps.length) return <p className="nt-muted">{loading ? "Loading…" : "One note so far; its history starts here."}</p>;
  const cut = at ? Date.parse(at) : Infinity;
  return (
    <ol className="nt-milestones" aria-label="How it evolved, oldest first">
      {steps.map((m, i) => {
        const later = Date.parse(m.at) > cut;
        return (
          <li key={`${m.kind}:${m.at}:${i}`} className={`nt-milestone nt-milestone--${m.kind}${later ? " nt-milestone--later" : ""}`}>
            <div className="nt-milestone__head">
              <span className="nt-milestone__label">{later ? `${m.label} · later` : m.label}</span>
              <time className="nt-milestone__date" dateTime={m.at}>
                {longDate(m.at)}
              </time>
            </div>
            <p className="nt-milestone__text">{capitalize(m.text)}</p>
            {m.note_id || m.claim_id ? (
              <div className="nt-actions">
                {m.note_id ? (
                  <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(m.note_id!)}>
                    Open note
                  </button>
                ) : null}
                {m.claim_id ? (
                  <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openClaim(m.claim_id!)}>
                    Why
                  </button>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function DayPanel({ date, onClose }: { date: string; onClose: () => void }) {
  const { api } = useApp();
  const tz = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return "";
    }
  })();
  const [at] = useAt();
  const [byReceived] = useByReceived();
  const day = useRead<Day>(() => api.get<Day>(`note/day/${date}`, { tz, ...(byReceived && at ? { as_received: at } : {}) }), [api, date, byReceived, at]);
  return (
    <div className="nt-panel">
      <header className="nt-panel__head">
        <div>
          <h2>{longDate(`${date}T12:00:00Z`)}</h2>
          <p className="nt-meta">What happened that day</p>
        </div>
      </header>
      <ReadView read={day} what="that day" empty={(d) => (d.notes.length || d.revisions.length || d.entities_touched.length ? false : "Nothing was noted that day.")}>
        {(d) => (
          <>
            {d.notes.length ? (
              <Section title="Notes">
                {d.notes.map((n) => (
                  <NoteLink key={n.note_id} note={n} showDate={false} />
                ))}
              </Section>
            ) : null}
            {d.revisions.length ? (
              <Section title="What changed">
                <ul className="nt-list">
                  {d.revisions.map((r) => (
                    <li key={r.revision_id}>{capitalize(r.summary)}</li>
                  ))}
                </ul>
              </Section>
            ) : null}
            {d.entities_touched.length ? (
              <Section title="Touched">
                <div className="nt-chips">
                  {d.entities_touched.map((e) => (
                    <EntityChip key={e.entity_id} entity={e} />
                  ))}
                </div>
              </Section>
            ) : null}
            {d.claims_superseded.length ? (
              <Section title="No longer believed">
                <ul className="nt-list">
                  {d.claims_superseded.map((c) => (
                    <li key={c.claim_id} className="nt-list__row">
                      <span className="nt-struck">{claimSentence(c)}</span>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(c.claim_id)}>
                        Why
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
          </>
        )}
      </ReadView>
      <div className="nt-actions">
        <AfChipButton size="md" onClick={onClose}>
          Close the day
        </AfChipButton>
      </div>
    </div>
  );
}
