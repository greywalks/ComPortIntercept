const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, value: '', textContent: '', disabled: false, hidden: false,
      className: '', events: {}, addEventListener(name, fn) { this.events[name] = fn; },
      replaceChildren() {}, appendChild() {}});
    return elements.get(id);
  }
  let delayedSelection, resolveSelection, delayedPrint, resolvePrint, printCount = 0;
  const job = id => ({id, captured_at: '2026-01-01T00:00:00Z', printer_name:'Printer', windows_port:'USB001',
    byte_count: 4, boundary_reason:'idle', sha256:'abc', text:'test'});
  const response = obj => ({ok:true, json:async()=>obj});
  const fetch = async path => {
    if (path === '/api/jobs/1') return response(job(1));
    if (path === '/api/jobs/2') return delayedSelection;
    if (path === '/api/jobs/2/reprint') { printCount++; return delayedPrint; }
    if (path.startsWith('/api/jobs?q=')) return response([]);
    throw new Error('Unexpected request ' + path);
  };
  const context = vm.createContext({document:{getElementById:element, createElement:()=>element('created')}, fetch,
    confirm:()=>true, setInterval:()=>0, setTimeout, clearTimeout, URL, Date, console});
  vm.runInContext(fs.readFileSync('web/app.js','utf8'), context);
  return {context, element, get printCount(){return printCount;},
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
