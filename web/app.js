'use strict';
let token = null, selected = null, selectionReady = false, reprintPending = false, previewProvider = 'disabled', previewUrl = null;
const $ = id => document.getElementById(id);
async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, {Authorization: 'Bearer ' + token});
  const response = await fetch(path, {...options, headers, cache: 'no-store'});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return response;
}
function cell(row, value) { const td = document.createElement('td'); td.textContent = value ?? ''; row.appendChild(td); }
async function status() {
  const s = await (await api('/api/status')).json();
  $('status').textContent = s.connected ? 'Capture online' : 'Capture offline';
  $('status').className = s.connected ? 'ok' : 'bad';
  $('status').title = s.last_error || '';
  previewProvider = s.preview_provider;
  const warnings = [];
  if (s.last_error) warnings.push(s.last_error);
  if (s.dropped_chunks) warnings.push(`Driver reported ${s.dropped_chunks} dropped chunks (${s.dropped_bytes} bytes) in this session.`);
  if (s.loss_events && s.loss_events.length) warnings.push('Capture-loss history exists. Review the API status before relying on the archive.');
  if (s.driver_restarts && s.driver_restarts.length) warnings.push('The driver restarted; unacknowledged in-memory data may have been lost. Review the API status.');
  $('warning').textContent = warnings.join(' ');
  $('warning').hidden = warnings.length === 0;
  $('preview-button').disabled = !selectionReady || previewProvider !== 'labelary';
}
async function jobs() {
  const q = encodeURIComponent($('q').value.trim());
  const list = await (await api('/api/jobs?q=' + q)).json();
  const rows = $('rows'); rows.replaceChildren();
  for (const job of list) {
    const tr = document.createElement('tr');
    cell(tr, new Date(job.captured_at).toLocaleString());
    cell(tr, job.printer_name + ' / ' + job.windows_port);
    cell(tr, job.origin); cell(tr, job.byte_count); cell(tr, (job.text || '').slice(0, 120));
    tr.addEventListener('click', () => pick(job.id).catch(showError)); rows.appendChild(tr);
  }
}
function showError(error) { $('msg').textContent = error.message || String(error); }
async function pick(id) {
  selected = id; selectionReady = false;
  $('reprint').disabled = true; $('preview-button').disabled = true;
  $('title').textContent = 'Loading segment #' + id;
  const j = await (await api('/api/jobs/' + id)).json();
  if (selected !== id) return;
  $('title').textContent = 'Captured segment #' + j.id;
  $('meta').textContent = `${new Date(j.captured_at).toLocaleString()} • ${j.printer_name} • ${j.windows_port} • ${j.byte_count} bytes • ${j.boundary_reason} • SHA-256 ${j.sha256}`;
  $('raw').textContent = j.text || '';
  selectionReady = true;
  $('reprint').disabled = reprintPending;
  $('preview-button').disabled = previewProvider !== 'labelary';
  $('msg').textContent = '';
  $('preview').replaceChildren();
  if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
}
$('unlock').addEventListener('click', async () => {
  token = $('token').value.trim();
  try {
    await status(); await jobs(); $('token').value = ''; $('login').hidden = true; $('app').hidden = false;
  } catch (e) { token = null; $('login-error').textContent = e.message; }
});
$('token').addEventListener('keydown', e => { if (e.key === 'Enter') $('unlock').click(); });
$('reprint').addEventListener('click', async () => {
  if (!selectionReady || reprintPending || !confirm('Send this captured stream segment to the original printer queue? It may not contain a complete original job.')) return;
  const id = selected; reprintPending = true; $('reprint').disabled = true;
  try { const result = await (await api('/api/jobs/' + id + '/reprint', {method: 'POST'})).json(); $('msg').textContent = result.message; await jobs(); }
  catch (e) { showError(e); }
  finally { reprintPending = false; $('reprint').disabled = !selectionReady; }
});
$('preview-button').addEventListener('click', async () => {
  if (!selectionReady || !confirm('Send this segment, including its label contents, to Labelary for preview?')) return;
  try {
    const id = selected, response = await api('/api/jobs/' + id + '/preview', {method: 'POST'});
    const blob = await response.blob(); if (id !== selected) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob); const img = document.createElement('img');
    img.alt = 'Label preview'; img.src = previewUrl; $('preview').replaceChildren(img);
  } catch (e) { showError(e); }
});
let timer; $('q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => jobs().catch(showError), 250); });
setInterval(() => { if (token) { status().catch(showError); jobs().catch(showError); } }, 5000);
