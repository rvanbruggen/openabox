/* OpenABox graph explorer.
 *
 * Deliberately dependency-free: no build step, no CDN, no npm. The force
 * layout below is a plain velocity-Verlet simulation, which is ample for the
 * few hundred nodes a company neighbourhood produces and keeps the whole app
 * runnable offline on a private Docker host.
 */

/* Warm, kraft-leaning palette to match the chrome, while keeping the labels
 * distinguishable from one another — City takes the one cool hue so the
 * geographic anchor stands out against the browns. */
const LABEL_STYLE = {
  Company:            { color: '#c2703d', r: 20 },
  Address:            { color: '#8b6f47', r: 15 },
  City:               { color: '#4a7ba7', r: 17 },
  Establishment:      { color: '#d9a441', r: 11 },
  NaceCode:           { color: '#5f8a6a', r: 12 },
  JuridicalForm:      { color: '#8a6a9e', r: 12 },
  JuridicalSituation: { color: '#8a8178', r: 10 },
  Person:             { color: '#b5527a', r: 17 },
  ExternalEntity:     { color: '#7d5f9e', r: 17 },
  Deposit:            { color: '#6b8f9e', r: 11 },
};
const DEFAULT_STYLE = { color: '#9c8b76', r: 12 };

/* Edges that describe ownership or control rather than structure. Drawn with
 * emphasis, and captioned with the percentage when the filing gave one. */
const OWNERSHIP_EDGES = new Set([
  'SHAREHOLDER_OF', 'HOLDS_PARTICIPATION', 'DIRECTOR_OF', 'CONSOLIDATED_BY',
]);

const SAVED_QUERIES = [
  ['Companies sharing an address',
   'MATCH (a:Address)<-[:REGISTERED_AT]-(c:Company)\nWITH a, collect(c) AS cos WHERE size(cos) > 1\nRETURN a, cos'],
  ['Busiest addresses',
   'MATCH (a:Address)<-[:REGISTERED_AT]-(c:Company)\nRETURN a.full_address AS address, count(c) AS companies\nORDER BY companies DESC LIMIT 20'],
  ['Companies per city',
   'MATCH (ct:City)<-[:IN_CITY]-(:Address)<-[:REGISTERED_AT]-(c:Company)\nRETURN ct.post_code AS post_code, ct.name AS city,\n       ct.aliases AS also_filed_as, count(DISTINCT c) AS companies\nORDER BY companies DESC LIMIT 25'],
  ['Post codes filed under several names',
   'MATCH (ct:City) WHERE size(ct.aliases) > 1\nRETURN ct.key AS city_key, ct.post_code AS post_code, ct.aliases AS names'],
  ['Companies sharing an activity',
   'MATCH (c1:Company)-[:HAS_ACTIVITY]->(n:NaceCode)<-[:HAS_ACTIVITY]-(c2:Company)\nWHERE elementId(c1) < elementId(c2)\nRETURN n.code AS nace, n.description AS description,\n       collect(DISTINCT c1.denomination + " / " + c2.denomination)[0..5] AS pairs'],
  ['Ownership graph',
   'MATCH p = ()-[:SHAREHOLDER_OF|HOLDS_PARTICIPATION|CONSOLIDATED_BY]->()\nRETURN p LIMIT 100'],
  ['Controlling shareholders (>25%)',
   'MATCH (h)-[s:SHAREHOLDER_OF]->(c:Company)\nWHERE s.pct > 25 AND h <> c\nRETURN coalesce(h.denomination, h.name) AS owner,\n       c.denomination AS company, s.pct AS pct, s.as_of AS as_of\nORDER BY pct DESC LIMIT 50'],
  ['Companies sharing a shareholder',
   'MATCH (h)-[s1:SHAREHOLDER_OF]->(a:Company), (h)-[s2:SHAREHOLDER_OF]->(b:Company)\nWHERE elementId(a) < elementId(b) AND h <> a AND h <> b\nRETURN coalesce(h.denomination, h.name) AS shared_owner,\n       a.denomination AS company_a, s1.pct AS pct_a,\n       b.denomination AS company_b, s2.pct AS pct_b\nORDER BY shared_owner LIMIT 50'],
  ['People behind a company (ownership chain)',
   'MATCH path = (p:Person)-[:SHAREHOLDER_OF*1..4]->(c:Company)\nWHERE all(r IN relationships(path) WHERE startNode(r) <> endNode(r))\nRETURN p.name AS person, c.denomination AS company,\n       [r IN relationships(path) | r.pct] AS pct_chain,\n       length(path) AS hops\nORDER BY hops DESC LIMIT 50'],
  ['Cache provenance',
   'MATCH (c:Company)\nRETURN c.denomination AS company, c._source AS source,\n       c._fetched_at AS fetched, c._hydrated AS hydrated\nORDER BY fetched DESC'],
];

const state = {
  nodes: new Map(),
  links: new Map(),
  alpha: 0,
  view: { x: 0, y: 0, k: 1 },
  drag: null,
  pan: null,
};

const svg       = document.getElementById('canvas');
const viewport  = document.getElementById('viewport');
const linkLayer = document.getElementById('links');
const nodeLayer = document.getElementById('nodes');
const tooltip   = document.getElementById('tooltip');

/* ---------- helpers ---------- */

/* Labels arrive sorted, so a foreign shareholder (:Company:ExternalEntity)
 * would otherwise render as a plain Company. The more specific label wins, so
 * "this owner is not in the Belgian register" is visible on the canvas. */
function primaryLabel(n) {
  return n.labels.find((l) => l !== 'Company' && LABEL_STYLE[l]) || n.labels[0];
}

const styleFor = (n) => LABEL_STYLE[primaryLabel(n)] || DEFAULT_STYLE;

function caption(n) {
  const p = n.props;
  switch (primaryLabel(n)) {
    case 'Company':            return p.denomination || p.cbe_number || 'Company';
    case 'Address':            return p.full_address || p.key || 'Address';
    case 'City':               return [p.post_code, p.name].filter(Boolean).join(' ') || p.key;
    case 'Establishment':      return p.establishment_number || 'Establishment';
    case 'NaceCode':           return `${p.code} (${p.version || '?'})`;
    case 'JuridicalForm':      return p.short_label || p.label || p.code;
    case 'JuridicalSituation': return p.label || p.code;
    case 'Person':             return p.name || 'Person';
    case 'ExternalEntity':     return p.denomination || p.identifier || 'Foreign entity';
    case 'Deposit':            return `Filing ${String(p.period_end || '').slice(0, 10)}`;
    default:                   return n.labels[0] || 'Node';
  }
}

const truncate = (s, n = 34) =>
  (s = String(s ?? ''), s.length > n ? s.slice(0, n - 1) + '…' : s);

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

/* ---------- graph state ---------- */

function mergeGraph(payload) {
  const rect = svg.getBoundingClientRect();
  let added = 0;

  for (const raw of payload.nodes || []) {
    if (state.nodes.has(raw._id)) continue;
    const { _type, _id, _labels, ...props } = raw;
    state.nodes.set(_id, {
      id: _id,
      labels: _labels,
      props,
      // Seed near the middle with jitter; the simulation does the rest.
      x: rect.width / 2 + (Math.random() - 0.5) * 220,
      y: rect.height / 2 + (Math.random() - 0.5) * 220,
      vx: 0, vy: 0, pinned: false,
    });
    added++;
  }

  for (const raw of payload.relationships || []) {
    if (state.links.has(raw._id)) continue;
    const { _type, _id, _rel_type, _start, _end, ...props } = raw;
    state.links.set(raw._id, {
      id: raw._id, type: raw._rel_type, from: raw._start, to: raw._end, props,
    });
  }

  if (added) { render(); reheat(); }
  return added;
}

function clearGraph() {
  state.nodes.clear();
  state.links.clear();
  render();
}

/* ---------- force simulation ---------- */

const REPULSION = 9000, SPRING = 0.02, LINK_DIST = 95, GRAVITY = 0.012, DAMPING = 0.82;

function reheat() { state.alpha = 1; }

function tick() {
  const nodes = [...state.nodes.values()];
  const rect = svg.getBoundingClientRect();
  const cx = rect.width / 2, cy = rect.height / 2;

  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
      const d = Math.sqrt(d2);
      const f = REPULSION / d2;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    }
  }

  for (const link of state.links.values()) {
    const a = state.nodes.get(link.from), b = state.nodes.get(link.to);
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const f = (d - LINK_DIST) * SPRING;
    const fx = (dx / d) * f, fy = (dy / d) * f;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }

  for (const n of nodes) {
    if (n.pinned) { n.vx = n.vy = 0; continue; }
    n.vx += (cx - n.x) * GRAVITY;
    n.vy += (cy - n.y) * GRAVITY;
    n.vx *= DAMPING; n.vy *= DAMPING;
    n.x += n.vx * state.alpha;
    n.y += n.vy * state.alpha;
  }
}

function loop() {
  if (state.alpha > 0.005) {
    tick();
    state.alpha *= 0.985;
    paint();
  }
  requestAnimationFrame(loop);
}

/* ---------- rendering ---------- */

/* A stake's weight, not its exact number: >50% is control, 25-50% is a
 * blocking minority, below that is a holding. Those are the thresholds that
 * actually change what a shareholding *means*. */
function stakeClass(link) {
  const pct = link.props?.pct;
  if (typeof pct !== 'number') return '';
  if (pct > 50) return 'stake-major';
  if (pct >= 25) return 'stake-mid';
  return '';
}

/* Ownership edges are captioned with the percentage when the filing gave one,
 * because "64.4%" is the answer and "SHAREHOLDER_OF" is only the question. */
function linkCaption(link) {
  if (!OWNERSHIP_EDGES.has(link.type)) return link.type;
  const { pct, role_label } = link.props || {};
  if (typeof pct === 'number') return `${pct.toFixed(2).replace(/\.?0+$/, '')}%`;
  if (link.type === 'DIRECTOR_OF') return role_label || 'director';
  if (link.type === 'CONSOLIDATED_BY') return 'consolidated by';
  return link.type.replace(/_/g, ' ').toLowerCase();
}

function render() {
  linkLayer.replaceChildren();
  nodeLayer.replaceChildren();

  for (const link of state.links.values()) {
    if (!state.nodes.has(link.from) || !state.nodes.has(link.to)) continue;
    const owned = OWNERSHIP_EDGES.has(link.type);

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('class', `link${owned ? ` ${stakeClass(link)} rel-${link.type}` : ''}`);
    line.dataset.id = link.id;
    if (owned) line.setAttribute('marker-end', `url(#arrow-${link.type})`);
    linkLayer.append(line);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('class', `link-label${owned ? ` rel-${link.type}` : ''}`);
    label.dataset.id = link.id;
    label.textContent = linkCaption(link);
    linkLayer.append(label);
  }

  for (const node of state.nodes.values()) {
    const style = styleFor(node);
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'node');
    g.dataset.id = node.id;

    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', style.r);
    circle.setAttribute('fill', style.color);
    g.append(circle);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('dy', style.r + 12);
    text.textContent = truncate(caption(node), 26);
    g.append(text);

    nodeLayer.append(g);
  }

  renderLegend();
  paint();
}

function paint() {
  const { x, y, k } = state.view;
  viewport.setAttribute('transform', `translate(${x},${y}) scale(${k})`);

  for (const el of linkLayer.children) {
    const link = state.links.get(el.dataset.id);
    const a = state.nodes.get(link.from), b = state.nodes.get(link.to);
    if (!a || !b) continue;
    if (el.tagName === 'line') {
      let [x2, y2] = [b.x, b.y];
      if (OWNERSHIP_EDGES.has(link.type)) {
        // Stop the line at the target's edge so the arrowhead sits outside the
        // circle instead of being painted underneath it.
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 1;
        const gap = styleFor(b).r + 5;
        x2 = b.x - (dx / dist) * gap;
        y2 = b.y - (dy / dist) * gap;
      }
      el.setAttribute('x1', a.x); el.setAttribute('y1', a.y);
      el.setAttribute('x2', x2); el.setAttribute('y2', y2);
    } else {
      el.setAttribute('x', (a.x + b.x) / 2);
      el.setAttribute('y', (a.y + b.y) / 2 - 3);
    }
  }

  for (const el of nodeLayer.children) {
    const node = state.nodes.get(el.dataset.id);
    el.setAttribute('transform', `translate(${node.x},${node.y})`);
    el.classList.toggle('pinned', node.pinned);
  }
}

function renderLegend() {
  const present = new Set();
  for (const n of state.nodes.values()) present.add(primaryLabel(n));
  const legend = document.getElementById('legend');
  legend.replaceChildren();
  legend.hidden = present.size === 0;
  for (const label of [...present].sort()) {
    const style = LABEL_STYLE[label] || DEFAULT_STYLE;
    const row = document.createElement('div');
    row.innerHTML = `<span class="dot" style="background:${style.color}"></span>${label}`;
    legend.append(row);
  }

  // Only explain the ownership colours once there are ownership edges to
  // explain — otherwise the legend describes a graph the user cannot see.
  const edges = new Set();
  for (const l of state.links.values()) if (OWNERSHIP_EDGES.has(l.type)) edges.add(l.type);
  if (!edges.size) return;
  const EDGE_KEY = {
    SHAREHOLDER_OF:      ['#c2703d', 'solid',  'owns'],
    HOLDS_PARTICIPATION: ['#5f8a6a', 'solid',  'holds stake in'],
    DIRECTOR_OF:         ['#8a6a9e', 'dashed', 'directs'],
    CONSOLIDATED_BY:     ['#4a7ba7', 'dotted', 'consolidated by'],
  };
  const box = document.createElement('div');
  box.className = 'edge-key';
  box.innerHTML = [...edges].sort().map((t) => {
    const [color, style, text] = EDGE_KEY[t];
    return `<div><span class="bar" style="border-top-color:${color};` +
           `border-top-style:${style}"></span>${text}</div>`;
  }).join('') + '<div style="opacity:.7">thicker line = larger stake</div>';
  legend.append(box);
}

/* ---------- canvas interaction ---------- */

function toWorld(evt) {
  const rect = svg.getBoundingClientRect();
  const { x, y, k } = state.view;
  return {
    x: (evt.clientX - rect.left - x) / k,
    y: (evt.clientY - rect.top - y) / k,
  };
}

svg.addEventListener('mousedown', (evt) => {
  const g = evt.target.closest('.node');
  if (g) {
    const node = state.nodes.get(g.dataset.id);
    const p = toWorld(evt);
    state.drag = { node, dx: node.x - p.x, dy: node.y - p.y, moved: false };
    node.pinned = true;
  } else {
    state.pan = { x: evt.clientX - state.view.x, y: evt.clientY - state.view.y };
    svg.classList.add('panning');
  }
});

window.addEventListener('mousemove', (evt) => {
  if (state.drag) {
    const p = toWorld(evt);
    state.drag.node.x = p.x + state.drag.dx;
    state.drag.node.y = p.y + state.drag.dy;
    state.drag.moved = true;
    reheat();
    paint();
  } else if (state.pan) {
    state.view.x = evt.clientX - state.pan.x;
    state.view.y = evt.clientY - state.pan.y;
    paint();
  }
});

window.addEventListener('mouseup', async (evt) => {
  const drag = state.drag;
  state.drag = null;
  state.pan = null;
  svg.classList.remove('panning');
  // A click that never moved is an expand request, not a drag.
  if (drag && !drag.moved) {
    drag.node.pinned = false;
    await showDetails(drag.node);
    await expand(drag.node);
  }
});

svg.addEventListener('dblclick', (evt) => {
  const g = evt.target.closest('.node');
  if (!g) return;
  const node = state.nodes.get(g.dataset.id);
  node.pinned = !node.pinned;
  paint();
});

svg.addEventListener('wheel', (evt) => {
  evt.preventDefault();
  const rect = svg.getBoundingClientRect();
  const mx = evt.clientX - rect.left, my = evt.clientY - rect.top;
  const factor = evt.deltaY < 0 ? 1.12 : 1 / 1.12;
  const k = Math.min(4, Math.max(0.15, state.view.k * factor));
  // Keep the point under the cursor fixed while zooming.
  state.view.x = mx - (mx - state.view.x) * (k / state.view.k);
  state.view.y = my - (my - state.view.y) * (k / state.view.k);
  state.view.k = k;
  paint();
}, { passive: false });

svg.addEventListener('mousemove', (evt) => {
  const g = evt.target.closest('.node');
  if (!g) { tooltip.hidden = true; return; }
  const node = state.nodes.get(g.dataset.id);
  const rows = Object.entries(node.props)
    .filter(([k, v]) => v !== null && v !== '' && !k.startsWith('_'))
    .slice(0, 6)
    .map(([k, v]) => `<div><b>${k}</b>: ${truncate(v, 40)}</div>`)
    .join('');
  tooltip.innerHTML = `<div><b>${node.labels.join(', ')}</b></div>${rows}`;
  tooltip.hidden = false;
  const rect = svg.getBoundingClientRect();
  tooltip.style.left = (evt.clientX - rect.left + 14) + 'px';
  tooltip.style.top = (evt.clientY - rect.top + 14) + 'px';
});

svg.addEventListener('mouseleave', () => { tooltip.hidden = true; });

/* ---------- shareholder investigation ---------- */

const nodeMenu = document.getElementById('node-menu');

function closeMenu() { nodeMenu.hidden = true; }
window.addEventListener('click', closeMenu);
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
svg.addEventListener('wheel', closeMenu, { passive: true });

svg.addEventListener('contextmenu', (evt) => {
  const g = evt.target.closest('.node');
  if (!g) return closeMenu();
  const node = state.nodes.get(g.dataset.id);
  if (!node) return;
  evt.preventDefault();

  const cbe = node.props.cbe_number;
  const isCompany = node.labels.includes('Company');
  // A foreign shareholder has no CBE number, so there is nothing to look up at
  // the NBB. Say so rather than offering an action that cannot work.
  const canInvestigate = isCompany && Boolean(cbe);

  nodeMenu.innerHTML = `
    <div class="menu-title">${caption(node)}</div>
    <button id="menu-investigate" ${canInvestigate ? '' : 'disabled'}>
      Investigate shareholders (NBB)
    </button>
    <button id="menu-financials" ${canInvestigate ? '' : 'disabled'}>
      Financials over time
    </button>
    <button id="menu-expand">Expand neighbours</button>
    ${canInvestigate ? '' :
      `<div class="menu-note">${isCompany
        ? 'No CBE number — not in the Belgian register.'
        : 'Only companies file annual accounts.'}</div>`}`;

  const rect = svg.getBoundingClientRect();
  nodeMenu.style.left = Math.min(evt.clientX - rect.left, rect.width - 220) + 'px';
  nodeMenu.style.top = Math.min(evt.clientY - rect.top, rect.height - 90) + 'px';
  nodeMenu.hidden = false;

  document.getElementById('menu-expand').addEventListener('click', () => {
    closeMenu();
    expand(node);
  });
  if (canInvestigate) {
    document.getElementById('menu-investigate')
      .addEventListener('click', () => { closeMenu(); investigate(node, cbe); });
    document.getElementById('menu-financials')
      .addEventListener('click', () => { closeMenu(); showFinancials(node, cbe); });
  }
});

// Clicking inside the menu must not bubble to the window handler that closes it
// before the button's own listener runs.
nodeMenu.addEventListener('click', (evt) => evt.stopPropagation());

/* Fetch the company's annual accounts from the NBB, ingest them, and pull the
 * resulting ownership subgraph onto the canvas. */
async function investigate(node, cbe) {
  const details = document.getElementById('details');
  details.innerHTML =
    `<div class="source-tag">NBB · Central Balance Sheet Office</div>
     <div class="detail-title">${caption(node)}</div>
     <p class="empty">Fetching the latest annual accounts…</p>`;

  let data;
  try {
    data = await api(`/api/company/${cbe}/shareholders`);
  } catch (err) {
    details.innerHTML =
      `<div class="source-tag">NBB · failed</div>
       <div class="detail-title">${caption(node)}</div>
       <p class="empty">${err.message}</p>`;
    return;
  }

  try {
    mergeGraph(await api(`/api/graph/company/${cbe}/ownership`));
  } catch (err) {
    console.warn('ownership graph failed', err);
  }
  showInvestigation(node, data);
  // The filings are already being read for ownership, so the figures come at
  // no extra round trip to the NBB beyond the CSV per year.
  showFinancials(node, cbe);
}

/* ---------- financials panel ---------- */

const finPanel = document.getElementById('financials');
document.getElementById('fin-close')
  .addEventListener('click', () => { finPanel.hidden = true; });

/* Compact money: filings run from a few thousand to billions, and raw digits
 * at those magnitudes are unreadable side by side. */
function money(v) {
  if (typeof v !== 'number') return '—';
  const abs = Math.abs(v);
  if (abs >= 1e9) return (v / 1e9).toFixed(2) + 'bn';
  if (abs >= 1e6) return (v / 1e6).toFixed(1) + 'm';
  if (abs >= 1e3) return Math.round(v / 1e3) + 'k';
  return Math.round(v).toString();
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* Grouped bar chart with a real zero line, so losses point downwards instead
 * of being hidden by an axis that starts at the minimum. */
function barChart(rows, seriesDefs, { height = 120 } = {}) {
  const W = 320, H = height, padL = 4, padB = 16, padT = 12;
  const values = rows.flatMap((r) => seriesDefs.map((s) => r[s.key])
    .filter((v) => typeof v === 'number'));
  if (!values.length) return '<p class="empty">Not disclosed in these filings.</p>';

  const max = Math.max(0, ...values), min = Math.min(0, ...values);
  const span = (max - min) || 1;
  const plotH = H - padB - padT;
  const y = (v) => padT + (max - v) / span * plotH;
  const zeroY = y(0);
  const groupW = (W - padL) / rows.length;
  const barW = Math.min(18, (groupW - 8) / seriesDefs.length);

  const bars = rows.map((r, i) => {
    const gx = padL + i * groupW + (groupW - barW * seriesDefs.length) / 2;
    return seriesDefs.map((s, j) => {
      const v = r[s.key];
      if (typeof v !== 'number') return '';
      const top = Math.min(y(v), zeroY), h = Math.max(1, Math.abs(y(v) - zeroY));
      return `<rect x="${gx + j * barW}" y="${top}" width="${barW - 2}" height="${h}"
                    fill="${s.color}" rx="1"><title>${esc(s.label)} ${r.year}: ${money(v)}</title></rect>
              <text class="val" x="${gx + j * barW + (barW - 2) / 2}"
                    y="${v >= 0 ? top - 2 : top + h + 7}" text-anchor="middle">${money(v)}</text>`;
    }).join('');
  }).join('');

  const labels = rows.map((r, i) =>
    `<text x="${padL + i * groupW + groupW / 2}" y="${H - 3}" text-anchor="middle">${esc(r.year)}</text>`
  ).join('');

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <line class="zero" x1="${padL}" y1="${zeroY}" x2="${W}" y2="${zeroY}"></line>
    ${bars}${labels}</svg>`;
}

/* Stacked equity-vs-liabilities: the shape of the bar *is* the answer to
 * "how much of this is owned rather than borrowed". */
function capitalChart(rows) {
  const W = 320, H = 130, padB = 16, padT = 14, padL = 4;
  const totals = rows.map((r) => (r.equity || 0) + (r.liabilities || 0));
  const max = Math.max(...totals, 1);
  const plotH = H - padB - padT;
  const groupW = (W - padL) / rows.length;
  const barW = Math.min(30, groupW - 10);

  const bars = rows.map((r, i) => {
    const x = padL + i * groupW + (groupW - barW) / 2;
    const eq = Math.max(0, r.equity || 0), li = Math.max(0, r.liabilities || 0);
    const eqH = eq / max * plotH, liH = li / max * plotH;
    const base = padT + plotH;
    return `
      <rect x="${x}" y="${base - liH}" width="${barW}" height="${liH}" fill="#b08968" rx="1">
        <title>Liabilities ${r.year}: ${money(li)}</title></rect>
      <rect x="${x}" y="${base - liH - eqH}" width="${barW}" height="${eqH}" fill="#5f8a6a" rx="1">
        <title>Equity ${r.year}: ${money(eq)}</title></rect>
      <text class="val" x="${x + barW / 2}" y="${base - liH - eqH - 3}" text-anchor="middle">
        ${typeof r.equity_ratio === 'number' ? r.equity_ratio.toFixed(0) + '%' : ''}</text>
      <text x="${x + barW / 2}" y="${H - 3}" text-anchor="middle">${esc(r.year)}</text>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${bars}</svg>`;
}

function finKey(items) {
  return `<div class="fin-key">${items.map(([c, l]) =>
    `<span><i style="background:${c}"></i>${esc(l)}</span>`).join('')}</div>`;
}

async function showFinancials(node, cbe) {
  finPanel.hidden = false;
  document.getElementById('fin-title').textContent = caption(node);
  const body = document.getElementById('fin-body');
  body.innerHTML = '<p class="empty">Fetching filed accounts from the NBB…</p>';

  let data;
  try {
    data = await api(`/api/company/${cbe}/financials?years=8`);
  } catch (err) {
    body.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    return;
  }

  const rows = data.series || [];
  if (!rows.length) {
    body.innerHTML =
      '<p class="empty">No machine-readable filings. Older accounts exist only ' +
      'as scanned images, which carry no extractable figures.</p>';
    return;
  }

  // Turnover is absent from abbreviated and micro filings. Saying so is the
  // difference between "this company shrank" and "this figure is not public".
  const missingTurnover = rows.some((r) => typeof r.turnover !== 'number');

  body.innerHTML = `
    <div class="fin-chart">
      <h4>Turnover and result</h4>
      <div class="sub">${rows[0].year}–${rows[rows.length - 1].year} · as filed</div>
      ${finKey([['#c2703d', 'Turnover'], ['#5f8a6a', 'Result for the period']])}
      ${barChart(rows, [
        { key: 'turnover', label: 'Turnover', color: '#c2703d' },
        { key: 'result', label: 'Result', color: '#5f8a6a' },
      ])}
      ${missingTurnover ? '<div class="sub">Years without a turnover bar filed an ' +
        'abbreviated or micro scheme, which does not disclose it.</div>' : ''}
    </div>

    <div class="fin-chart">
      <h4>Capital structure</h4>
      <div class="sub">Equity vs liabilities · % is the equity ratio</div>
      ${finKey([['#5f8a6a', 'Equity (own)'], ['#b08968', 'Liabilities (third-party)']])}
      ${capitalChart(rows)}
    </div>

    <div class="fin-chart">
      <h4>Operating result</h4>
      ${barChart(rows, [{ key: 'operating_result', label: 'Operating result', color: '#8a6a9e' }],
                 { height: 100 })}
    </div>

    <table class="fin-table">
      <thead><tr><th>Year</th><th>Equity</th><th>Assets</th><th>Result</th><th>FTE</th></tr></thead>
      <tbody>${rows.slice().reverse().map((r) => `
        <tr>
          <td>${esc(r.year)}</td>
          <td>${money(r.equity)}</td>
          <td>${money(r.total_assets)}</td>
          <td class="${r.result < 0 ? 'neg' : ''}">${money(r.result)}</td>
          <td>${typeof r.employees_fte === 'number' ? r.employees_fte.toFixed(0) : '—'}</td>
        </tr>`).join('')}</tbody>
    </table>
    <p class="menu-note">Source: annual accounts filed with the NBB
      (${esc(rows.map((r) => r.model_id).filter((v, i, a) => a.indexOf(v) === i).join(', '))}).
      Figures are as filed and not restated.</p>`;
}

function partyRows(parties, emptyText) {
  if (!parties || !parties.length) return `<p class="empty">${emptyText}</p>`;
  return parties.map((p) => {
    const pct = typeof p.pct === 'number' ? `${p.pct}%` : '';
    const meta = [
      p.cbe_number || p.identifier || (p.kind === 'person' ? 'natural person' : ''),
      p.shares ? `${p.shares.toLocaleString()} shares` : '',
      p.role_label || '',
      p.represented_by ? `rep. ${p.represented_by.join(', ')}` : '',
    ].filter(Boolean).join(' · ');
    // Only a party with a CBE number can be loaded; the rest are terminal.
    return `<div class="result${p.cbe_number ? ' occupant' : ''}"
                 ${p.cbe_number ? `data-cbe="${p.cbe_number}"` : ''}>
      <div class="name">${p.name}${pct ? ` <span class="count">${pct}</span>` : ''}</div>
      ${meta ? `<div class="meta">${meta}</div>` : ''}
    </div>`;
  }).join('');
}

function showInvestigation(node, data) {
  const details = document.getElementById('details');
  const d = data.deposit;

  if (!d) {
    details.innerHTML =
      `<div class="source-tag">${data.source}</div>
       <div class="detail-title">${caption(node)}</div>
       <p class="empty">${data.note || 'No filing found.'}</p>`;
    return;
  }

  const filed = String(d.period_end || '').slice(0, 10);
  details.innerHTML = `
    <div class="source-tag">${data.source} · ${d.model_id} · year end ${filed}</div>
    <div class="detail-title">${caption(node)}</div>
    <h3 class="occupants-heading">Shareholders
      <span class="count">${(data.shareholders || []).length}</span></h3>
    ${partyRows(data.shareholders, 'The filing names no shareholders.')}
    <h3 class="occupants-heading">Directors
      <span class="count">${(data.directors || []).length}</span></h3>
    ${partyRows(data.directors, 'The filing names no directors.')}
    <h3 class="occupants-heading">Holds stakes in
      <span class="count">${(data.participations || []).length}</span></h3>
    ${partyRows(data.participations, 'No participations disclosed.')}
    <p class="menu-note">Percentages and share counts are as filed for the year
      ending ${filed} — not necessarily today's ownership.</p>`;

  wireOccupants(details);
}

async function expand(node) {
  try {
    const payload = await api('/api/graph/expand', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ element_id: node.id, limit: 60 }),
    });
    mergeGraph(payload);
  } catch (err) {
    console.warn('expand failed', err);
  }
}

/* ---------- sidebar ---------- */

async function showDetails(node) {
  const details = document.getElementById('details');
  const rows = Object.entries(node.props)
    .filter(([, v]) => v !== null && v !== '')
    .map(([k, v]) => `<dt>${k}</dt><dd>${truncate(v, 90)}</dd>`)
    .join('');
  details.innerHTML =
    `<div class="source-tag">${node.labels.join(' · ')}</div>
     <div class="detail-title">${caption(node)}</div>
     <dl class="props">${rows}</dl>
     <div id="occupants"></div>`;

  if (node.labels.includes('Address')) await showAddressOccupants(node);
  else if (node.labels.includes('City')) await showCityOccupants(node);
}

/* Render a clickable list of companies, each loading that company's graph. */
function occupantList(companies, emptyText) {
  if (!companies.length) return `<p class="empty">${emptyText}</p>`;
  return companies.map((c) => `
    <div class="result occupant" data-cbe="${c.cbe_number}">
      <div class="name">${c.denomination || c.cbe_number}</div>
      <div class="meta">${c.cbe_number}${
        c.establishment_number ? ` · est. ${c.establishment_number}` : ''
      }${c.address ? ' · ' + truncate(c.address, 34) : ''}</div>
    </div>`).join('');
}

function wireOccupants(box) {
  box.querySelectorAll('.occupant').forEach((el) => {
    el.addEventListener('click', () => loadCompany(el.dataset.cbe));
  });
}

async function showAddressOccupants(node) {
  const box = document.getElementById('occupants');
  box.innerHTML = '<p class="empty">Loading companies at this address…</p>';
  let data;
  try {
    data = await api(`/api/address/companies?key=${encodeURIComponent(node.props.key)}`);
  } catch (err) {
    box.innerHTML = `<p class="empty">${err.message}</p>`;
    return;
  }

  // Registered office and branch are kept apart: being registered at an
  // address is a different claim from merely operating a site there.
  box.innerHTML = `
    <h3 class="occupants-heading">Registered office here
      <span class="count">${data.registered.length}</span></h3>
    ${occupantList(data.registered, 'No company is registered at this address.')}
    <h3 class="occupants-heading">Establishment here
      <span class="count">${data.establishments.length}</span></h3>
    ${occupantList(data.establishments, 'No establishments recorded here.')}
    <button class="secondary wide" id="add-occupants">Add all to canvas</button>`;

  wireOccupants(box);
  document.getElementById('add-occupants').addEventListener('click', () => expand(node));
}

async function showCityOccupants(node) {
  const box = document.getElementById('occupants');
  box.innerHTML = '<p class="empty">Loading companies in this city…</p>';
  let data;
  try {
    data = await api(`/api/city/companies?key=${encodeURIComponent(node.props.key)}`);
  } catch (err) {
    box.innerHTML = `<p class="empty">${err.message}</p>`;
    return;
  }
  box.innerHTML = `
    <h3 class="occupants-heading">Registered in ${data.post_code || ''} ${data.city || ''}
      <span class="count">${data.companies.length}</span></h3>
    ${occupantList(data.companies, 'No companies here yet.')}`;
  wireOccupants(box);
}

function showResults(data, context) {
  const box = document.getElementById('results');
  const results = data.results || [];
  if (!results.length) {
    box.innerHTML =
      `<p class="empty">Nothing found${context ? ` for ${context}` : ''}. ` +
      `Tick <em>live</em> to query the API.</p>`;
    return;
  }
  const items = results.map((r) => {
    // Cache hits arrive wrapped as {company, address}; API hits are flat.
    const c = r.company || r;
    const props = c.props || c;
    const cbe = props.cbe_number;
    const city = r.city ? [r.city.post_code, r.city.name].filter(Boolean).join(' ') : '';
    const address = (r.address && r.address.full_address) ||
                    (props.address && props.address.full_address) || city;
    const extra = r.nace_code ? `${r.nace_code} — ${truncate(r.nace_description, 46)}` : '';
    return `<div class="result" data-cbe="${cbe}">
      <div class="name">${props.denomination || cbe}</div>
      <div class="meta">${props.cbe_number_formatted || cbe}${address ? ' · ' + truncate(address, 40) : ''}</div>
      ${extra ? `<div class="meta">${extra}</div>` : ''}
    </div>`;
  }).join('');

  // Say plainly when the upstream set was larger than what we fetched — a
  // truncated list otherwise reads as a complete answer.
  const p = data.pagination;
  const truncated = p && p.truncated
    ? `<p class="empty">Showing ${results.length} of ${p.total}. Raise
       <code>max_pages</code> to fetch more — each page is one API request.</p>`
    : '';

  box.innerHTML =
    `<div class="source-tag">${data.source} · ${results.length} result(s)` +
    `${p && p.total ? ` of ${p.total}` : ''}${context ? ` · ${context}` : ''}</div>` +
    truncated + items;
  box.querySelectorAll('.result').forEach((el) => {
    el.addEventListener('click', () => loadCompany(el.dataset.cbe));
  });
}

async function loadCompany(cbe) {
  clearGraph();
  try {
    mergeGraph(await api(`/api/graph/company/${cbe}`));
  } catch (err) {
    // Not in the graph yet — fetch it, which also ingests it.
    await api(`/api/company/${cbe}`);
    mergeGraph(await api(`/api/graph/company/${cbe}`));
  }
  refreshQuota();
}

/* ---------- search ---------- */

const PLACEHOLDERS = {
  auto:    'Company name, NACE code, address, or 0716.663.615',
  name:    'Company name, e.g. Colruyt',
  number:  'CBE / VAT number, e.g. 0716.663.615 or BE0716663615',
  nace:    'NACE code, e.g. 62 or 62010 (prefix matches sub-codes)',
  address: 'Edingensesteenweg 196, 1500 Halle',
};

const modeSelect = document.getElementById('mode');
const naceVersion = document.getElementById('nace-version');
const searchInput = document.getElementById('search-input');
const addressFields = document.getElementById('address-fields');

modeSelect.addEventListener('change', () => {
  const mode = modeSelect.value;
  const structured = mode === 'address';
  searchInput.placeholder = PLACEHOLDERS[mode];
  searchInput.hidden = structured;
  // A hidden field that is still `required` blocks form submission outright,
  // so the flag has to move with the visibility.
  searchInput.required = !structured;
  addressFields.hidden = !structured;
  naceVersion.hidden = mode !== 'nace';
  (structured ? document.getElementById('addr-street') : searchInput).focus();
});

/* Read the explicit address inputs, dropping the blanks. */
function addressFromFields() {
  const parts = {
    street: document.getElementById('addr-street').value.trim(),
    house_number: document.getElementById('addr-number').value.trim(),
    post_code: document.getElementById('addr-postcode').value.trim(),
    city: document.getElementById('addr-city').value.trim(),
  };
  return Object.fromEntries(Object.entries(parts).filter(([, v]) => v));
}

const cbeDigits = (term) => term.replace(/[.\s-]/g, '').replace(/^BE/i, '');

function detectMode(term) {
  const digits = cbeDigits(term);
  if (/^\d{9,10}$/.test(digits)) return 'number';
  // A short all-digit term is a NACE code. It is ambiguous with a postal
  // code, so Auto resolves it to NACE; pick Address explicitly for a postcode.
  if (/^\d{1,5}$/.test(digits)) return 'nace';
  if (/\d/.test(term) && /[a-z]/i.test(term)) return 'address';
  return 'name';
}

/* Split a free-text address into the components the API and cache expect.
 * "Edingensesteenweg 196, 1500 Halle" -> street/house_number/post_code/city */
function parseAddress(text) {
  const out = {};
  let rest = text.trim();

  const postcode = rest.match(/\b(\d{4})\b/);
  if (postcode) { out.post_code = postcode[1]; rest = rest.replace(postcode[0], ' '); }

  const [head, tail] = rest.split(',').map((s) => s.trim());
  rest = head || '';
  if (tail) out.city = tail;

  // First run of digits inside the street part is the house number; whatever
  // follows it is the city when no comma separated it out.
  const m = rest.match(/^(.+?)\s+(\d+\s?[a-zA-Z]?)\b\s*(.*)$/);
  if (m) {
    out.street = m[1].trim();
    out.house_number = m[2].replace(/\s/g, '');
    if (m[3] && !out.city) out.city = m[3].trim();
  } else if (rest) {
    out.street = rest;
  }
  return out;
}

document.getElementById('search-form').addEventListener('submit', async (evt) => {
  evt.preventDefault();
  const term = document.getElementById('search-input').value.trim();
  const live = document.getElementById('refresh').checked;
  const box = document.getElementById('results');
  const mode = modeSelect.value === 'auto' ? detectMode(term) : modeSelect.value;
  box.innerHTML = '<p class="empty">Searching…</p>';

  try {
    if (mode === 'number') {
      const digits = cbeDigits(term);
      await loadCompany(digits);
      box.innerHTML = `<div class="source-tag">looked up ${digits}</div>`;
    } else if (mode === 'nace') {
      const code = cbeDigits(term);
      const data = await api(
        `/api/nace/${encodeURIComponent(code)}/companies` +
        `?nace_version=${naceVersion.value}&refresh=${live}`);
      showResults(data, `NACE ${code} (${naceVersion.value})`);
    } else if (mode === 'address') {
      // Explicit fields when Address mode is picked; free-text parsing only
      // when Auto routed us here, where there is nothing better to go on.
      const parts = modeSelect.value === 'address' ? addressFromFields() : parseAddress(term);
      if (!Object.keys(parts).length) {
        throw new Error('Fill in at least one of street, number, postcode or city.');
      }
      const qs = new URLSearchParams({ ...parts, refresh: live });
      showResults(await api(`/api/address/search?${qs}`),
                  Object.entries(parts).map(([k, v]) => `${k}=${v}`).join(' · '));
    } else {
      showResults(await api(`/api/search?name=${encodeURIComponent(term)}&refresh=${live}`));
    }
  } catch (err) {
    box.innerHTML = `<p class="empty">${err.message}</p>`;
  }
  refreshQuota();
});

/* ---------- cypher console ---------- */

const consoleBody = document.getElementById('console-body');
document.getElementById('console-toggle').addEventListener('click', (evt) => {
  const open = consoleBody.hidden;
  consoleBody.hidden = !open;
  evt.target.setAttribute('aria-expanded', String(open));
});

const savedBox = document.getElementById('saved-queries');
for (const [name, query] of SAVED_QUERIES) {
  const btn = document.createElement('button');
  btn.textContent = name;
  btn.addEventListener('click', () => { document.getElementById('cypher').value = query; });
  savedBox.append(btn);
}

document.getElementById('run-cypher').addEventListener('click', runCypher);
document.getElementById('cypher').addEventListener('keydown', (evt) => {
  if ((evt.metaKey || evt.ctrlKey) && evt.key === 'Enter') runCypher();
});

async function runCypher() {
  const query = document.getElementById('cypher').value;
  const status = document.getElementById('cypher-status');
  const out = document.getElementById('cypher-output');
  status.className = '';
  status.textContent = 'running…';

  let data;
  try {
    data = await api('/api/cypher', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, params: {} }),
    });
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    out.replaceChildren();
    return;
  }

  const { records, graph } = data;
  const drawn = graph.nodes.length ? mergeGraph(graph) : 0;
  status.textContent =
    `${records.length} row(s)` + (graph.nodes.length ? ` · ${drawn} new node(s) on canvas` : '');

  if (!records.length) { out.innerHTML = '<p class="empty">No rows.</p>'; return; }

  const columns = [...new Set(records.flatMap((r) => Object.keys(r)))];
  const cell = (v) => {
    if (v && v._type === 'node') return `(${v._labels.join(':')}) ${truncate(caption({ labels: v._labels, props: v }), 40)}`;
    if (v && v._type === 'relationship') return `[:${v._rel_type}]`;
    if (v && typeof v === 'object') return truncate(JSON.stringify(v), 60);
    return truncate(v, 60);
  };
  out.innerHTML =
    `<table><thead><tr>${columns.map((c) => `<th>${c}</th>`).join('')}</tr></thead>
     <tbody>${records.slice(0, 200).map((r) =>
       `<tr>${columns.map((c) => `<td>${cell(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table>` +
    (records.length > 200 ? `<p class="empty">Showing first 200 of ${records.length}.</p>` : '');
}

/* ---------- quota ---------- */

async function refreshQuota() {
  try {
    const health = await api('/health');
    const rl = health.rate_limit || {};
    const counts = health.neo4j || {};
    const remaining = rl['x-ratelimit-remaining'];
    // Read the version from the server rather than hardcoding it here, so the
    // UI cannot drift from the running backend.
    document.getElementById('version').textContent = health.version ? `v${health.version}` : '';
    // Same rule for the licence: the footer states what the running code says.
    // The markup already carries the MIT terms, so a health call that fails
    // leaves a correct footer standing rather than blanking it.
    if (health.license) {
      const link = document.getElementById('license-link');
      link.textContent = `${health.license} licence`;
      if (health.license_url) link.href = health.license_url;
    }
    if (health.copyright) document.getElementById('copyright').textContent =
      `OpenABox — ${health.copyright}`;
    if (health.disclaimer) document.getElementById('disclaimer').textContent = health.disclaimer;
    document.getElementById('quota').textContent =
      `${counts.companies ?? 0} companies · ${counts.addresses ?? 0} addresses` +
      (remaining ? ` · API ${remaining}/${rl['x-ratelimit-limit']}` : '');
  } catch { /* health is advisory only */ }
}

// Render once on load so the legend starts hidden rather than as an empty box.
render();
refreshQuota();
requestAnimationFrame(loop);
