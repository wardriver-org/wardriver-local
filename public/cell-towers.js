/* Tower data stays separate from survey observations and XP. */
function initCellTowers() {
  const toggle = document.querySelector('#cellTowerToggle');
  const fetchButton = document.querySelector('#cellTowerFetch');
  const status = document.querySelector('#cellTowerStatus');
  let enabled = false, generation = 0, controller, timer, popup;
  let data = {type: 'FeatureCollection', features: []};
  function render() {
    if (!mlMap.isStyleLoaded()) return;
    if (!mlMap.getSource('cell-towers')) mlMap.addSource('cell-towers', {type: 'geojson', data, attribution: '© OpenStreetMap contributors (ODbL)'});
    if (!mlMap.getLayer('cell-towers')) mlMap.addLayer({id: 'cell-towers', type: 'circle', source: 'cell-towers', paint: {'circle-radius': 7, 'circle-color': '#ffab40', 'circle-stroke-color': '#111827', 'circle-stroke-width': 2}});
    mlMap.getSource('cell-towers').setData(data);
    mlMap.setLayoutProperty('cell-towers', 'visibility', enabled ? 'visible' : 'none');
  }
  function bbox() {
    const b = mlMap.getBounds();
    const coords = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    if (coords[2]-coords[0] > .5 || coords[3]-coords[1] > .5) throw Error('Zoom in to load towers (maximum 0.5° per side).');
    return coords;
  }
  async function load(remote = false) {
    if (!enabled) return;
    const current = ++generation;
    controller?.abort(); controller = new AbortController();
    try {
      const area = bbox();
      status.textContent = remote ? 'Fetching OSM towers…' : 'Loading cached towers…';
      const response = await fetch(remote ? '/api/cell-towers/fetch' : '/api/cell-towers?bbox='+area.join(','), {method: remote ? 'POST' : 'GET', headers: remote ? {'Content-Type': 'application/json'} : {}, body: remote ? JSON.stringify({bbox: area}) : undefined, signal: controller.signal});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Tower request failed');
      if (current !== generation || !enabled) return;
      data = result; render();
      const age = result.oldest_fetched ? ' · cached '+new Date(result.oldest_fetched*1000).toLocaleDateString() : '';
      status.textContent = `${result.features.length} OSM cell sites${result.truncated ? ' (limit reached; zoom in)' : ''}${age} · © OpenStreetMap (ODbL)`;
    } catch (e) {
      if (e.name !== 'AbortError' && current === generation) status.textContent = e.message;
    }
  }
  toggle.addEventListener('click', () => {
    enabled = !enabled; ++generation; controller?.abort(); popup?.remove();
    toggle.textContent = 'Cell towers: '+(enabled ? 'On' : 'Off');
    toggle.setAttribute('aria-pressed', String(enabled)); fetchButton.hidden = !enabled;
    status.textContent = ''; render(); if (enabled) load();
  });
  fetchButton.addEventListener('click', async () => {
    if (!confirm('Fetch mapped cell sites from OpenStreetMap? This sends the visible map bounds to Overpass. Your observations and API keys are not sent. Coverage may be incomplete.')) return;
    fetchButton.disabled = true;
    try { await load(true); } finally { fetchButton.disabled = false; }
  });
  mlMap.on('moveend', () => { clearTimeout(timer); timer = setTimeout(() => load(), 250); });
  mlMap.on('style.load', render);
  mlMap.on('click', 'cell-towers', event => {
    if (!enabled || !event.features?.length) return;
    const feature = event.features[0], p = feature.properties;
    const node = document.createElement('div'); node.style.cssText = 'color:#111;max-width:260px';
    for (const line of [p.name || 'Mapped cell site', 'Operator: '+(p.operator || p.network || 'Unknown'), 'Structure: '+(p.man_made || 'Unspecified'), p.height ? 'Height: '+p.height : '', 'OSM reference: '+p.osm_id, 'Mapped location; not a signal or coverage estimate.']) {
      if (line) { const row = document.createElement('div'); row.textContent = line; node.appendChild(row); }
    }
    const link = document.createElement('a'); link.textContent = 'View on OpenStreetMap';
    if (/^(node|way|relation)\/\d+$/.test(p.osm_id)) link.href = 'https://www.openstreetmap.org/'+p.osm_id;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; node.appendChild(link);
    popup?.remove(); popup = new maplibregl.Popup().setLngLat(feature.geometry.coordinates).setDOMContent(node).addTo(mlMap);
  });
}
