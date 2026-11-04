const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(options = {}) {
  const elements = new Map(), confirmations = [], requests = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, value: '', textContent: '', disabled: false, hidden: false,
      className: '', events: {}, addEventListener(name, fn) { this.events[name] = fn; },
      replaceChildren() {}, appendChild() {}});
    return elements.get(id);
  }
  let delayedSelection, resolveSelection, delayedPrint, resolvePrint, printCount = 0, online = true;
  const job = id => ({id, captured_at: '2026-01-01T00:00:00Z', printer_name:'Printer', windows_port:'USB001',
    byte_count: 4, boundary_reason:'idle', sha256:'abc', text:'test', integrity:options.integrity || 'unverified-stream'});
  const response = obj => ({ok:true, json:async()=>obj});
  const fetch = async (path, requestOptions = {}) => {
    requests.push({path, options:requestOptions});
    if (path === '/api/status') {
      if (!online) throw new Error('network unavailable');
      return response({connected:true,last_error:null,preview_provider:'labelary',loss_events:[],driver_restarts:[]});
    }
    if (path === '/api/jobs/1') return response(job(1));
    if (path === '/api/jobs/2') return delayedSelection;
    if (path === '/api/jobs/2/reprint' || path === '/api/jobs/1/reprint') {
      printCount++; return delayedPrint || response({message:'submitted'});
    }
    if (path.startsWith('/api/jobs/1/preview?index='))
      return {ok:true, headers:{get:()=> options.noPreviewCount ? null : '2'}, blob:async()=>({})};
    if (path.startsWith('/api/jobs?q=')) return response([]);
    throw new Error('Unexpected request ' + path);
  };
  const fakeURL = {createObjectURL:()=> 'blob:preview',revokeObjectURL:()=>{}};
  const context = vm.createContext({document:{getElementById:element, createElement:()=>element('created')}, fetch,
    confirm:message=>{confirmations.push(message);return true;}, setInterval:()=>0, setTimeout, clearTimeout, AbortController, URL:fakeURL, Date, console});
  vm.runInContext(fs.readFileSync('web/app.js','utf8'), context);
  return {context, element, requests, confirmations, get printCount(){return printCount;},
    disconnect() { online = false; },
    delaySelection() { delayedSelection = new Promise(resolve => { resolveSelection = resolve; }); },
    finishSelection() { resolveSelection(response(job(2))); },
    delayPrint() { delayedPrint = new Promise(resolve => { resolvePrint = resolve; }); },
    finishPrint() { resolvePrint(response({message:'submitted'})); }};
}

test('a changing selection cannot reprint stale UI and double-click sends one request', async () => {
  const ui = setup();
  await vm.runInContext('pick(1)', ui.context);
  assert.equal(ui.element('reprint').disabled, false);
  ui.delaySelection();
  const selecting = vm.runInContext('pick(2)', ui.context);
  assert.equal(ui.element('reprint').disabled, true);
  await ui.element('reprint').events.click();
  assert.equal(ui.printCount, 0);
  ui.finishSelection(); await selecting;
  assert.equal(ui.element('reprint').disabled, false);
  ui.delayPrint();
  const first = ui.element('reprint').events.click();
  const second = ui.element('reprint').events.click();
  assert.equal(ui.printCount, 1);
  ui.finishPrint(); await Promise.all([first, second]);
  assert.equal(ui.element('reprint').disabled, false);
});

test('status changes from green when the archive is unreachable', async () => {
  const ui = setup(); await vm.runInContext('status()', ui.context);
  assert.equal(ui.element('status').textContent, 'Capture online');
  ui.disconnect(); await vm.runInContext('status().catch(showError)', ui.context);
  assert.equal(ui.element('status').textContent, 'Archive offline or unavailable');
  assert.equal(ui.element('status').className, 'bad');
});

test('multiple previews show count and permit navigation', async () => {
  const ui = setup(); await vm.runInContext('pick(1)', ui.context);
  await vm.runInContext('loadPreview(0)', ui.context);
  assert.match(ui.element('preview-meta').textContent, /Label 1 of 2/);
  assert.equal(ui.element('preview-next').disabled, false);
  await vm.runInContext('loadPreview(1)', ui.context);
  assert.match(ui.element('preview-meta').textContent, /Label 2 of 2/);
  assert.equal(ui.element('preview-next').disabled, true);
});

test('preview navigation remains available when the provider omits a count', async () => {
  const ui = setup({noPreviewCount:true}); await vm.runInContext('pick(1)', ui.context);
  await vm.runInContext('loadPreview(0)', ui.context);
  assert.match(ui.element('preview-meta').textContent, /total label count unavailable/);
  assert.equal(ui.element('preview-next').disabled, false);
  await ui.element('preview-next').events.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(ui.requests.some(r => r.path === '/api/jobs/1/preview?index=1'));
});

test('known capture loss requires a separate explicit acknowledgment', async () => {
  const ui = setup({integrity:'capture-loss-possible'}); await vm.runInContext('pick(1)', ui.context);
  await ui.element('reprint').events.click();
  assert.equal(ui.confirmations.length, 2);
  assert.match(ui.confirmations[0], /multiple labels/);
  assert.match(ui.confirmations[1], /Known capture loss/);
  const request = ui.requests.find(r => r.path.endsWith('/reprint'));
  assert.equal(JSON.parse(request.options.body).acknowledge_loss, true);
});
