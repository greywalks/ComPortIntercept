'use strict';
let token = null, selected = null, selectionReady = false, selectedLoss = false, reprintPending = false;
let previewProvider = 'disabled', previewUrl = null, previewIndex = 0, previewTotal = null, previewPending = false;
const $ = id => document.getElementById(id);
async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, {Authorization: 'Bearer ' + token});
  const response = await fetch(path, {...options, headers, cache: 'no-store'});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.error || `Request failed (${response.status})`); error.code = data.code; throw error;
  }
  return response;
}
function cell(row, value) { const td = document.createElement('td'); td.textContent = value ?? ''; row.appendChild(td); }
async function status() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const s = await (await api('/api/status', {signal: controller.signal})).json();
    $('status').textContent = s.connected ? 'Capture online' : 'Capture offline';
    $('status').className = s.connected ? 'ok' : 'bad';
    $('status').title = s.last_error || '';
    previewProvider = s.preview_provider;
    const warnings = [];
    if (s.last_error) warnings.push(s.last_error);
    if (s.dropped_chunks) warnings.push(`Driver reported ${s.dropped_chunks} dropped chunks (${s.dropped_bytes} bytes) in this session.`);
    if (s.loss_events && s.loss_events.length) warnings.push('Capture-loss history exists. Records from affected sessions require a specific reprint acknowledgment.');
    if (s.driver_restarts && s.driver_restarts.length) warnings.push('The driver restarted; unacknowledged in-memory data may have been lost.');
    if (!s.connected && s.pending_chunks) warnings.push(`${s.pending_chunks} saved chunks are waiting for the driver to reconnect.`);
    $('warning').textContent = warnings.join(' ');
    $('warning').hidden = warnings.length === 0;
    $('preview-button').disabled = !selectionReady || previewProvider !== 'labelary';
  } catch (error) {
    $('status').textContent = 'Archive offline or unavailable';
    $('status').className = 'bad';
    $('status').title = error.message || String(error);
    throw error;
  } finally { clearTimeout(timeout); }
}
async function jobs() {
  const q = encodeURIComponent($('q').value.trim());
  const list = await (await api('/api/jobs?q=' + q)).json();
  const rows = $('rows'); rows.replaceChildren();
  for (const job of list) {
    const tr = document.createElement('tr');
    cell(tr, new Date(job.captured_at).toLocaleString());
    cell(tr, job.printer_name + ' / ' + job.windows_port);
    cell(tr, job.origin); cell(tr, job.integrity === 'capture-loss-possible' ? 'Possible loss' : 'Unverified'); cell(tr, job.byte_count); cell(tr, (job.text || '').slice(0, 120));
    tr.addEventListener('click', () => pick(job.id).catch(showError)); rows.appendChild(tr);
  }
}
function showError(error) { $('msg').textContent = error.message || String(error); }
function clearPreview() {
  $('preview').replaceChildren(); $('preview-meta').textContent = ''; $('preview-controls').hidden = true;
  if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
  previewIndex = 0; previewTotal = null;
}
async function pick(id) {
  selected = id; selectionReady = false; selectedLoss = false;
  $('reprint').disabled = true; $('preview-button').disabled = true;
  $('title').textContent = 'Loading segment #' + id; clearPreview();
  const j = await (await api('/api/jobs/' + id)).json();
  if (selected !== id) return;
  $('title').textContent = 'Captured segment #' + j.id;
  const timeLabel = j.time_quality === 'driver-utc' ? 'Observed' : 'Archived; original observation time unavailable';
  $('meta').textContent = `${timeLabel}: ${new Date(j.captured_at).toLocaleString()} • ${j.printer_name} • ${j.windows_port} • ${j.byte_count} bytes • ${j.boundary_reason} • SHA-256 ${j.sha256}`;
  $('raw').textContent = j.text || '';
  selectedLoss = j.integrity === 'capture-loss-possible';
  selectionReady = true;
  $('reprint').disabled = reprintPending;
  $('preview-button').disabled = previewProvider !== 'labelary';
  $('msg').textContent = selectedLoss ? 'Known capture loss may affect this segment. Its saved hash cannot prove missing bytes were captured.' : '';
}
$('unlock').addEventListener('click', async () => {
  token = $('token').value.trim();
  try {
    await status(); await jobs(); $('token').value = ''; $('login').hidden = true; $('app').hidden = false;
  } catch (e) { token = null; $('login-error').textContent = e.message; }
});
$('token').addEventListener('keydown', e => { if (e.key === 'Enter') $('unlock').click(); });
$('reprint').addEventListener('click', async () => {
  if (!selectionReady || reprintPending || !confirm('Send all bytes in this segment? It may contain multiple labels or partial printer commands.')) return;
  if (selectedLoss && !confirm('Known capture loss may have corrupted this segment. Check the bytes and printer; reprint despite that risk?')) return;
  const id = selected; reprintPending = true; $('reprint').disabled = true;
  try {
    const result = await (await api('/api/jobs/' + id + '/reprint', {method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({acknowledge_loss:selectedLoss})})).json();
    $('msg').textContent = result.message; await jobs();
  } catch (e) {
    if (e.code === 'capture_loss_risk') selectedLoss = true;
    showError(e);
  } finally { reprintPending = false; $('reprint').disabled = !selectionReady; }
});
async function loadPreview(index) {
  if (!selectionReady || previewPending) return;
  previewPending = true; $('preview-prev').disabled = true; $('preview-next').disabled = true;
  const id = selected;
  try {
    const response = await api('/api/jobs/' + id + '/preview?index=' + index, {method: 'POST'});
    const blob = await response.blob(); if (id !== selected) return;
    const count = Number(response.headers.get('X-Total-Count'));
    previewTotal = Number.isSafeInteger(count) && count > 0 ? count : null;
    previewIndex = index;
    $('preview-meta').textContent = previewTotal ? `Label ${index + 1} of ${previewTotal}. Reprinting sends the entire segment.` :
      `Label ${index + 1}; total label count unavailable. Reprinting sends the entire segment.`;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob); const img = document.createElement('img');
    img.alt = `Label ${index + 1} preview`; img.src = previewUrl; $('preview').replaceChildren(img);
    $('preview-controls').hidden = false;
  } catch (e) { showError(e); }
  finally {
    previewPending = false;
    $('preview-prev').disabled = !selectionReady || previewIndex <= 0;
    $('preview-next').disabled = !selectionReady || (previewTotal !== null && previewIndex + 1 >= previewTotal) || previewIndex >= 9999;
  }
}
$('preview-button').addEventListener('click', async () => {
  if (!selectionReady || !confirm('Send this entire segment, including label contents, to Labelary for previews?')) return;
  await loadPreview(0);
});
$('preview-prev').addEventListener('click', () => { if (previewIndex > 0) loadPreview(previewIndex - 1); });
$('preview-next').addEventListener('click', () => { if (previewIndex < 9999 && (previewTotal === null || previewIndex + 1 < previewTotal)) loadPreview(previewIndex + 1); });
let timer; $('q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => jobs().catch(showError), 250); });
setInterval(() => { if (token) { status().catch(showError); jobs().catch(showError); } }, 5000);
