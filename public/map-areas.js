/* Optional local map downloads; no external lookup on load or while typing. */
(() => {
  'use strict';
  const anchor = document.querySelector('#mapStatus')?.closest('.opscard');
  if (!anchor) return;
  const card = document.createElement('section');
  card.className = 'opscard';
  card.innerHTML = `<h2>Download map areas</h2>
    <p class="msg">Keep cities, regions, or countries on this server. Downloads use rectangular bounds, so they may include neighboring places. No observations are uploaded.</p>
    <div class="sync-form">
      <label>Find a city, region, or country<input id="areaQuery" type="search" placeholder="City, state, country" maxlength="160"></label>
      <p class="msg">Search sends this place name to your configured geocoder. Downloads contact Protomaps; requested tiles reveal the selected area. Nothing is sent until you click Search or Download.</p>
      <div class="sync-actions"><button class="btn" id="areaSearch" type="button">Search places</button><button class="btn" id="areaView" type="button">Use current map view</button></div>
      <div id="areaResults"></div>
      <label>Area name<input id="areaName" maxlength="160" placeholder="Name this map"></label>
      <label>Bounds: west, south, east, north<input id="areaBounds" placeholder="-118.6,33.8,-118.1,34.3"></label>
      <label>Maximum detail<select id="areaZoom"><option value="10">10 — regional overview</option><option value="12">12 — city overview</option><option value="14">14 — streets</option><option value="15" selected>15 — full detail (larger download)</option></select></label>
      <label>Protomaps build date<input id="areaBuild" type="date"></label>
      <p class="msg">Choose an available date from <a href="https://maps.protomaps.com/builds" target="_blank" rel="noopener noreferrer">Protomaps builds</a>. Older daily builds may have expired. Build availability is checked when downloading.</p>
      <p class="msg" id="areaStorage"></p>
      <label class="sync-toggle"><input id="areaConsent" type="checkbox"> Allow this external map download</label>
      <div class="sync-actions"><button class="btn primary" id="areaDownload" type="button">Download area</button><button class="btn" id="areaCancel" type="button" disabled>Cancel download</button><button class="btn" id="areaRefresh" type="button">Refresh</button></div>
      <div class="sync-status" id="areaMessage" role="status" aria-live="polite"></div>
      <progress id="areaProgress" hidden aria-label="Map download in progress"></progress>
      <div id="areaJob" class="msg" aria-live="polite"></div>
      <h3>Installed maps</h3><p class="msg">Use a map to make it active. Prepare update fills this form for a new download; the previous map stays available until you delete it.</p>
      <div id="areaInstalled"></div>
    </div>`;
  anchor.after(card);
  const $ = id => card.querySelector('#' + id);
  let token = '', timer = null, loading = false;
  const size = n => `${(Number(n || 0) / 1073741824).toFixed(2)} GiB`;
  function message(value) { $('areaMessage').textContent = value; }
  function select(name, bbox) {
    $('areaName').value = name;
    $('areaBounds').value = bbox.map(n => Number(n).toFixed(6)).join(',');
    $('areaConsent').checked = false;
    message('Review the bounds, detail level, and build date before downloading.');
  }
  async function request(action, body) {
    if (!token) await refresh();
    if (!token) throw new Error('Map-area service unavailable; refresh and try again');
    const r = await fetch('/api/map/areas/' + action, {method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-Map-Areas-Token': token
    }, body: JSON.stringify(body)});
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || 'Request failed');
    return data;
  }
  async function action(fn) {
    try { await fn(); } catch (e) { message(e.message); }
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const response = await fetch('/api/map/areas', {cache: 'no-store'}), data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Could not load map areas');
      token = data.token;
      $('areaStorage').textContent = `Free storage: ${size(data.free_bytes)} · Per-download limit: ${size(data.limit_bytes)} · 1 GiB kept free. Exact download size is unknown until extraction; larger areas and higher zoom use more space.`;
      const busy = ['downloading', 'verifying'].includes(data.job?.state);
      $('areaDownload').disabled = busy || !data.available;
      $('areaCancel').disabled = !busy;
      $('areaProgress').hidden = !busy;
      $('areaJob').textContent = data.job ? `${data.job.name}: ${data.job.state} · ${size(data.job.bytes)} written · ${data.job.message}` : 'No download running.';
      if (!data.available) message('Downloader unavailable. Rebuild Wardriver with the updated Dockerfile.');
      $('areaInstalled').replaceChildren();
      for (const row of data.areas) {
        const item = document.createElement('div'); item.className = 'mini-item';
        const label = document.createElement('span'); label.className = 'mini-item-main';
        const active = typeof mapCfg !== 'undefined' && mapCfg.provider === 'selfhosted' && mapCfg.tile_url_template === row.url;
        label.textContent = `${row.name}${active ? ' · Active' : ''} · ${size(row.bytes)} · zoom ${row.maxzoom} · ${row.build}`;
        const buttons = document.createElement('span'); buttons.className = 'mini-item-actions';
        function button(text, fn, disabled = false) { const b = document.createElement('button'); b.className = 'btn'; b.type = 'button'; b.textContent = text; b.disabled = disabled; b.onclick = () => action(fn); buttons.append(b); }
        button('Use map', async () => {
          const r = await fetch('/api/map/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider:'selfhosted',tile_url_template:row.url,attribution:'© OpenStreetMap contributors · Protomaps',cache_allowed:false})});
          const data = await r.json(); if (!r.ok || !data.ok) throw new Error(data.error || 'Could not select map');
          applyMapSettings(data);
          if (typeof mlMap !== 'undefined' && mlMap) mlMap.fitBounds([[row.bbox[0],row.bbox[1]],[row.bbox[2],row.bbox[3]]],{padding:40});
          message('Active map: ' + row.name); await refresh();
        }, active);
        button('Prepare update', async () => { select(row.name, row.bbox); $('areaZoom').value = row.maxzoom; $('areaBuild').value = ''; message('Choose a new available build date, then download. Your existing map is preserved.'); });
        button('Delete', async () => { if (!confirm(`Delete downloaded map “${row.name}”?`)) return; await request('delete',{id:row.id}); await refresh(); }, active);
        item.append(label,buttons); $('areaInstalled').append(item);
      }
      if (!data.areas.length) $('areaInstalled').textContent = 'No downloaded areas yet. Your existing bundled map is kept separately.';
      clearTimeout(timer); timer = busy ? setTimeout(refresh, 2000) : null;
    } catch (e) { message(e.message); } finally { loading = false; }
  }
  $('areaSearch').onclick = () => action(async () => {
    $('areaSearch').disabled = true;
    try {
      const data = await request('search', {query: $('areaQuery').value});
      $('areaResults').replaceChildren();
      for (const row of data.results) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn'; b.style.whiteSpace = 'normal'; b.textContent = row.name;
        b.onclick = () => select(row.name, row.bbox); $('areaResults').append(b);
      }
      message(data.results.length ? 'Select a result and review its bounds.' : 'No matching area found. Try another name or enter bounds.');
    } finally { $('areaSearch').disabled = false; }
  });
  $('areaView').onclick = () => action(async () => {
    const b = v29Bounds(); select('Current map area',[b.west,b.south,b.east,b.north]);
  });
  $('areaDownload').onclick = () => action(async () => {
    const raw = $('areaBounds').value.split(',');
    if (raw.length !== 4 || raw.some(v => !v.trim())) throw new Error('Enter all four bounds');
    if (!$('areaConsent').checked) throw new Error('Confirm the external map download first');
    $('areaDownload').disabled = true;
    try {
      await request('download',{name:$('areaName').value,bbox:raw.map(Number),maxzoom:Number($('areaZoom').value),build:$('areaBuild').value,consent:true});
      $('areaConsent').checked = false; message('Download started. You may leave this page while it runs.');
    } finally { await refresh(); }
  });
  $('areaCancel').onclick = () => action(async () => { await request('cancel',{}); message('Cancelling…'); await refresh(); });
  $('areaRefresh').onclick = refresh;
  refresh();
})();
