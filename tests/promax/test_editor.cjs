// Lightweight DOM/Comfy lifecycle contract test, not a browser rendering test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.events = {}; this.value = ''; }
  append(item) { this.children.push(item); }
  addEventListener(name, fn) { this.events[name] = fn; }
  replaceChildren() { this.children = []; }
  remove() { this.removed = true; }
}
let extension;
const app = { registerExtension(x) { extension = x; }, graph: { setDirtyCanvas() {} } };
const document = { createElement(tag) { return new Element(tag); } };
const source = fs.readFileSync(require('node:path').join(__dirname, '../../js/minimax_promax.js'), 'utf8').replace(/^import .*;$/m, '');
vm.runInNewContext(source, { app, document });
const flow = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../../example_workflows/Promax-12GB-Base.api.json')));
class Node {
  constructor() {
    this.widgets = Object.entries(flow['6'].inputs).filter(([,v])=>!Array.isArray(v)).map(([name,value]) => ({name,value,options:{}}));
    this.size = [300,300]; this.graph = { change() {} };
  }
  addDOMWidget(name,type,element,options) { this.dom={name,type,element,options}; }
  setSize(value) { this.size = value; }
}
function descendants(root) { return [root,...root.children.flatMap(descendants)]; }
function click(node,text) {
  const b=descendants(node.promaxEditor.root).find(e=>e.tag==='button' && e.textContent===text);
  assert.ok(b, text); b.events.click();
}
(async () => {
  await extension.beforeRegisterNodeDef(Node,{name:'MiniMaxH3Promax'});
  const node = new Node(); node.onNodeCreated();
  const shots = node.widgets.find(w=>w.name==='shots_json');
  assert.equal(JSON.parse(shots.value).length,2);
  click(node,'+ Ajouter un plan'); assert.equal(JSON.parse(shots.value).length,3);
  click(node,'Supprimer'); assert.equal(JSON.parse(shots.value).length,2);
  const saved = shots.value;
  node.onConfigure(); assert.equal(shots.value,saved);
  const json='[{"seconds":5,"camera":"Close shot.","action":"A wave.","audio":"Splash."}]';
  shots.value=json; node.onConfigure(); assert.equal(shots.value,json);
  assert.ok(descendants(node.promaxEditor.root).some(e=>e.value==='Close shot.'));
  click(node,'Références'); assert.ok(descendants(node.promaxEditor.root).some(e=>e.textContent==='<Picture 1> — identité / rôle'));
  click(node,'Contrôle'); node.onExecuted({promax_report:['Report from backend'],promax_prompt:['Actual prompt']});
  assert.ok(descendants(node.promaxEditor.root).some(e=>e.value==='Actual prompt'));
  shots.value='{broken'; node.onConfigure(); click(node,'Scénario');
  assert.equal(shots.value,'{broken'); assert.ok(descendants(node.promaxEditor.root).some(e=>e.textContent?.startsWith('JSON invalide')));
  node.onRemoved(); assert.equal(node.promaxEditor.root.removed,true);
  console.log('Promax editor: add/delete, reload, references, execution output, invalid JSON preservation, cleanup passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
