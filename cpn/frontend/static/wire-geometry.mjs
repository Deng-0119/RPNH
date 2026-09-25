/** Display-only rounded routes and circuit-style bridges over independent wires.
 * Reads ELK geometry, never changes a link endpoint, vertex, PN arc or run data.
 * Hidden resource links do not leave phantom crossings on visible wires.
 */
import { edgeKey, resourceEdge } from './model.mjs';

const EPS = 1e-6;
const distance = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
const at = (segment, d) => ({ x: segment.a.x + segment.ux * d, y: segment.a.y + segment.uy * d });
const number = n => Number(n.toFixed(3));
const xy = p => `${number(p.x)} ${number(p.y)}`;

function cleanPoints(points) {
  const out = [];
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) throw new Error('Non-finite wire coordinate');
    if (out.length && distance(out.at(-1), p) < EPS) continue;
    while (out.length > 1) {
      const a = out.at(-2), b = out.at(-1);
      const cross = (b.x-a.x)*(p.y-b.y) - (b.y-a.y)*(p.x-b.x);
      const dot = (b.x-a.x)*(p.x-b.x) + (b.y-a.y)*(p.y-b.y);
      if (Math.abs(cross) > EPS || dot <= 0) break;
      out.pop();
    }
    out.push({ x:p.x, y:p.y });
  }
  return out;
}
function segments(points) {
  return points.slice(1).map((b,i) => {
    const a=points[i],length=distance(a,b);
    return {a,b,length,ux:(b.x-a.x)/length,uy:(b.y-a.y)/length,index:i,
      axis:Math.abs(a.y-b.y)<EPS?'h':Math.abs(a.x-b.x)<EPS?'v':'d',events:[]};
  });
}
function roundedPath(route, radius) {
  if (!route.length) return '';
  const parts=['M '+xy(route[0].a)];
  for(let i=0;i<route.length;i++) {
    const s=route[i],trimStart=i?Math.min(radius,s.length/3,route[i-1].length/3):0;
    const trimEnd=i+1<route.length?Math.min(radius,s.length/3,route[i+1].length/3):0;
    // Adjacent bridges or gaps are merged, preserving one readable arch and
    // avoiding little loops caused by overlapping local decorations.
    const events=[];
    for(const e of [...s.events].sort((a,b)=>a.start-b.start)) {
      const previous=events.at(-1);
      if(previous&&previous.kind===e.kind&&e.start<=previous.end+1) previous.end=Math.max(previous.end,e.end);
      else events.push({...e});
    }
    for(const e of events) {
      if(e.start<=trimStart || e.end>=s.length-trimEnd) continue;
      const a=at(s,e.start),b=at(s,e.end);parts.push('L '+xy(a));
      if(e.kind==='gap') parts.push('M '+xy(b));
      else { // All chosen overpasses are horizontal; draw their arch upward.
        const height=e.radius*4/3;
        parts.push(`C ${xy({x:a.x,y:a.y-height})} ${xy({x:b.x,y:b.y-height})} ${xy(b)}`);
      }
    }
    parts.push('L '+xy(at(s,s.length-trimEnd)));
    if(trimEnd) {
      // JointJS g.Path.parse accepts cubic commands, not SVG Q commands.
      // Convert this quadratic round-corner exactly to cubic control points.
      const a=at(s,s.length-trimEnd),b=at(route[i+1],trimEnd);
      const c1={x:a.x+2*(s.b.x-a.x)/3,y:a.y+2*(s.b.y-a.y)/3};
      const c2={x:b.x+2*(s.b.x-b.x)/3,y:b.y+2*(s.b.y-b.y)/3};
      parts.push(`C ${xy(c1)} ${xy(c2)} ${xy(b)}`);
    }
  }
  return parts.join(' ');
}
/** Compute actual route paths and testable crossing provenance. */
export function planWirePaths(snapshot,layout,{showResources=false,enabled=true,radius=6,cornerRadius=8}={}) {
  if(!Number.isFinite(radius)||radius<=0||!Number.isFinite(cornerRadius)||cornerRadius<0) throw new Error('Invalid display wire radius');
  const by=new Map(snapshot.nodes.map(n=>[n.id,n])),routes=new Map(),hs=[],vs=[];
  for(const edge of [...snapshot.edges].sort((a,b)=>a.id.localeCompare(b.id,'en'))) {
    const record=layout.edges.get(edge.id),section=record?.sections?.[0];
    if(!section||record.sections.length!==1) throw new Error('A display wire requires one complete route');
    const route=segments(cleanPoints([section.startPoint,...(section.bendPoints??[]),section.endPoint]));
    const visible=showResources||!resourceEdge(edge,by);routes.set(edge.id,{edge,route,visible});
    if(!visible) continue;
    for(const segment of route) {const value={...segment,edge};
      // Share the event collection with the route consumed by roundedPath.
      if(segment.axis==='h') hs.push(value);else if(segment.axis==='v') vs.push(value);
    }
  }
  const boxes=[...layout.nodes.values()].map(n=>({x:n.x-2,y:n.y-2,right:n.x+n.width+2,bottom:n.y+n.height+2}));
  const crossings=[],clearance=radius+cornerRadius+3;
  if(enabled) {
    vs.sort((a,b)=>a.a.x-b.a.x);
    const lowerBound=x=>{let lo=0,hi=vs.length;while(lo<hi){const m=(lo+hi)>>1;if(vs[m].a.x<x)lo=m+1;else hi=m;}return lo;};
    for(const h of hs) {
      const min=Math.min(h.a.x,h.b.x)+clearance,max=Math.max(h.a.x,h.b.x)-clearance;
      for(let i=lowerBound(min);i<vs.length&&vs[i].a.x<max;i++) {
        const v=vs[i],a=h.edge,b=v.edge,x=v.a.x,y=h.a.y;
        // Shared endpoint/fanout is intentional, not a crossing bridge.
        if(a.id===b.id||a.source===b.source||a.source===b.target||a.target===b.source||a.target===b.target)continue;
        if(y<=Math.min(v.a.y,v.b.y)+clearance||y>=Math.max(v.a.y,v.b.y)-clearance)continue;
        if(boxes.some(n=>x>=n.x-radius&&x<=n.right+radius&&y>=n.y-radius&&y<=n.bottom+radius))continue;
        const hd=distance(h.a,{x,y}),vd=distance(v.a,{x,y});
        h.events.push({start:hd-radius,end:hd+radius,kind:'bridge',radius});
        // A short under-wire gap disambiguates depth at the arch itself, like a
        // circuit overpass. It is a path decoration, not a disconnected graph.
        v.events.push({start:vd-radius-2,end:vd+radius+2,kind:'gap',radius});
        crossings.push({over:a.id,under:b.id,x,y});
      }
    }
  }
  const paths=new Map();
  for(const [id,value] of routes) paths.set(id,{
    path:roundedPath(value.route,cornerRadius),
    bridges:value.route.reduce((n,s)=>n+s.events.filter(e=>e.kind==='bridge').length,0),
    gaps:value.route.reduce((n,s)=>n+s.events.filter(e=>e.kind==='gap').length,0)
  });
  return {paths,crossings};
}
const cache=new WeakMap();
/** Update the existing link connector, including its click-target path. */
export function applyWireBridges(renderer,snapshot,layout,options={}) {
  const key=JSON.stringify([renderer.signature,Boolean(options.showResources),options.enabled!==false]);
  const old=cache.get(renderer);if(old?.key===key&&old.layout===layout)return old.summary;
  const plan=planWirePaths(snapshot,layout,options);
  for(const edge of snapshot.edges) {
    const cell=renderer.graph.getCell(edgeKey(edge.id)),route=plan.paths.get(edge.id);
    if(!cell||!route)continue;
    cell.connector(function(_source,_target,_vertices,opts={}) {
      return opts.raw?renderer.joint.g.Path.parse(route.path):route.path;
    });
    cell.attr('root/data-original-count',edge.source_ids?.length??1);
    cell.attr('root/data-bridge-count',route.bridges);
    cell.attr('root/data-gap-count',route.gaps);
    cell.attr('root/data-wire-style',options.enabled===false?'rounded':'bridged');
  }
  const summary={crossings:plan.crossings.length};cache.set(renderer,{key,layout,summary});return summary;
}
