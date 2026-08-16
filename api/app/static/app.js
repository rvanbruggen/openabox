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
};
const DEFAULT_STYLE = { color: '#9c8b76', r: 12 };

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
  ['Ownership graph (once ingested)',
   'MATCH p = (:Company)-[:HOLDS_PARTICIPATION|FOUNDED|OFFICER_OF]-()\nRETURN p LIMIT 100'],
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

const styleFor = (n) => LABEL_STYLE[n.labels[0]] || DEFAULT_STYLE;

function caption(n) {
  const p = n.props;
  switch (n.labels[0]) {
    case 'Company':            return p.denomination || p.cbe_number || 'Company';
    case 'Address':            return p.full_address || p.key || 'Address';
    case 'City':               return [p.post_code, p.name].filter(Boolean).join(' ') || p.key;
    case 'Establishment':      return p.establishment_number || 'Establishment';
    case 'NaceCode':           return `${p.code} (${p.version || '?'})`;
    case 'JuridicalForm':      return p.short_label || p.label || p.code;
    case 'JuridicalSituation': return p.label || p.code;
    case 'Person':             return p.name || 'Person';
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
    state.links.set(raw._id, {
      id: raw._id, type: raw._rel_type, from: raw._start, to: raw._end,
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

function render() {
  linkLayer.replaceChildren();
  nodeLayer.replaceChildren();

  for (const link of state.links.values()) {
    if (!state.nodes.has(link.from) || !state.nodes.has(link.to)) continue;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('class', 'link');
    line.dataset.id = link.id;
    linkLayer.append(line);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('class', 'link-label');
    label.dataset.id = link.id;
    label.textContent = link.type;
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
      el.setAttribute('x1', a.x); el.setAttribute('y1', a.y);
      el.setAttribute('x2', b.x); el.setAttribute('y2', b.y);
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
  for (const n of state.nodes.values()) present.add(n.labels[0]);
  const legend = document.getElementById('legend');
  legend.replaceChildren();
  legend.hidden = present.size === 0;
  for (const label of [...present].sort()) {
    const style = LABEL_STYLE[label] || DEFAULT_STYLE;
    const row = document.createElement('div');
    row.innerHTML = `<span class="dot" style="background:${style.color}"></span>${label}`;
    legend.append(row);
  }
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
    showDetails(drag.node);
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

function showDetails(node) {
  const details = document.getElementById('details');
  const rows = Object.entries(node.props)
    .filter(([, v]) => v !== null && v !== '')
    .map(([k, v]) => `<dt>${k}</dt><dd>${truncate(v, 90)}</dd>`)
    .join('');
  details.innerHTML =
    `<div class="source-tag">${node.labels.join(' · ')}</div>
     <div style="font-weight:600;margin-bottom:8px">${caption(node)}</div>
     <dl class="props">${rows}</dl>`;
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

  box.innerHTML =
    `<div class="source-tag">${data.source} · ${results.length} result(s)` +
    `${context ? ` · ${context}` : ''}</div>${items}`;
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
    document.getElementById('quota').textContent =
      `${counts.companies ?? 0} companies · ${counts.addresses ?? 0} addresses` +
      (remaining ? ` · API ${remaining}/${rl['x-ratelimit-limit']}` : '');
  } catch { /* health is advisory only */ }
}

// Render once on load so the legend starts hidden rather than as an empty box.
render();
refreshQuota();
requestAnimationFrame(loop);
