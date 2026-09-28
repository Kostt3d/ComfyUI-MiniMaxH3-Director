// Promax's independent shot editor. All authored content lives in ordinary node widgets.
import { app } from '../../scripts/app.js';

const FIELDS = ['global_prompt', 'summary', 'soundscape', 'music', 'shots_json',
  ...Array.from({ length: 9 }, (_, idx) => idx + 1).flatMap(i => [`ref${i}_description`, `ref${i}_retained`]),
  'reference_policy', 'auto_ref_limit', 'manual_refs',
  'video_description', 'video_retained', 'audio_description', 'audio_retained'];
const widget = (node, name) => node.widgets?.find(w => w.name === name);
const el = (tag, text, parent) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  parent?.append(item);
  return item;
};
function write(node, name, value) {
  const w = widget(node, name);
  if (!w) return;
  w.value = value;
  w.callback?.(value);
  node.graph?.change?.();
  app.graph?.setDirtyCanvas(true, true);
}
function field(parent, label, value, change, rows = 2) {
  const wrap = el('label', undefined, parent);
  wrap.style.cssText = 'display:block;margin:7px 0;color:#cbd5e1;font-size:12px';
  el('span', label, wrap);
  const input = el('textarea', undefined, wrap);
  input.value = value;
  input.rows = rows;
  input.style.cssText = 'display:block;box-sizing:border-box;width:100%;margin-top:4px;resize:vertical;background:#162237;color:#f1f5f9;border:1px solid #415775;border-radius:5px;padding:7px;font:12px sans-serif';
  input.addEventListener('input', () => change(input.value));
  return input;
}
function selectField(parent, label, value, options, change) {
  const wrap = el('label', undefined, parent);
  wrap.style.cssText = 'display:block;margin:7px 0;color:#cbd5e1;font-size:12px';
  el('span', label, wrap);
  const input = el('select', undefined, wrap);
  input.style.cssText = 'display:block;box-sizing:border-box;width:100%;margin-top:4px;background:#162237;color:#f1f5f9;border:1px solid #415775;border-radius:5px;padding:7px;font:12px sans-serif';
  for (const option of options) {
    const item = el('option', option, input);
    item.value = option;
  }
  input.value = value;
  input.addEventListener('change', () => change(input.value));
  return input;
}
function numberField(parent, label, value, min, max, change) {
  const wrap = el('label', undefined, parent);
  wrap.style.cssText = 'display:block;margin:7px 0;color:#cbd5e1;font-size:12px';
  el('span', label, wrap);
  const input = el('input', undefined, wrap);
  input.type = 'number'; input.min = String(min); input.max = String(max); input.step = '1'; input.value = value;
  input.style.cssText = 'display:block;box-sizing:border-box;width:100%;margin-top:4px;background:#162237;color:#f1f5f9;border:1px solid #415775;border-radius:5px;padding:7px;font:12px sans-serif';
  input.addEventListener('input', () => change(Number(input.value)));
  return input;
}
function button(parent, label, action) {
  const b = el('button', label, parent);
  b.type = 'button';
  b.style.cssText = 'background:#243c58;color:#f1f5f9;border:1px solid #526e8d;border-radius:5px;padding:6px 10px;margin:3px;cursor:pointer';
  b.addEventListener('click', action);
  return b;
}
function editor(node) {
  const root = document.createElement('div');
  root.style.cssText = 'box-sizing:border-box;background:#0b1423;color:#e2e8f0;padding:12px;border:1px solid #355778;border-radius:9px;width:100%;height:680px;overflow:auto;font-family:sans-serif';
  root.addEventListener('pointerdown', event => event.stopPropagation());
  root.addEventListener('keydown', event => event.stopPropagation());
  root.addEventListener('wheel', event => event.stopPropagation());
  let tab = 'Scénario';
  let lastReport = 'Cible : 12 Go VRAM. Durée : 4–15 s. Commencer par 5 s.';
  let lastPrompt = '';
  function draw() {
    root.replaceChildren();
    const title = el('div', 'MINIMAX H3 PROMAX', root);
    title.style.cssText = 'font-size:17px;font-weight:700;letter-spacing:2px;color:#71d4e9;margin-bottom:8px';
    const nav = el('div', undefined, root);
    for (const name of ['Scénario', 'Références', 'Contrôle']) {
      const b = button(nav, name, () => { tab = name; draw(); });
      if (name === tab) b.style.borderColor = '#71d4e9';
    }
    const form = el('div', undefined, root);
    const bound = (name, label, rows) => field(form, label, widget(node, name)?.value || '', value => write(node, name, value), rows);
    if (tab === 'Références') {
      el('p', 'BANQUE 9 REFS · Auto (12GB) encode seulement les références utiles. Dans les prompts, utilise @ref1 … @ref9. Promax compacte automatiquement les refs actives vers <Picture 1…N> pour H3.', form);
      selectField(form, 'Politique de références', widget(node, 'reference_policy')?.value || 'Auto (12GB)',
        ['Auto (12GB)', 'Manual', 'Force all loaded'], value => write(node, 'reference_policy', value));
      numberField(form, 'Limite Auto si aucun @refN explicite', widget(node, 'auto_ref_limit')?.value ?? 4, 1, 9,
        value => write(node, 'auto_ref_limit', value));
      bound('manual_refs', 'Manual · slots à encoder, ex. 1,4,7', 1);
      for (let i = 1; i <= 9; i++) {
        const card = el('div', undefined, form);
        card.style.cssText = 'background:#132134;border:1px solid #31475f;border-radius:6px;padding:8px;margin:8px 0';
        el('strong', `REF BANK ${i} · utiliser @ref${i} dans le scénario`, card);
        const local = (name, label, rows) => field(card, label, widget(node, name)?.value || '', value => write(node, name, value), rows);
        local(`ref${i}_description`, 'Identité / rôle', 2);
        local(`ref${i}_retained`, 'Éléments à conserver', 1);
      }
      bound('video_description', '<Video 1> — rôle', 1);
      bound('video_retained', 'Vidéo — éléments à conserver', 1);
      bound('audio_description', '<Audio 1> — rôle', 1);
      bound('audio_retained', 'Audio — éléments à conserver', 1);
      return;
    }
    if (tab === 'Contrôle') {
      el('p', lastReport, form);
      const preview = field(form, 'Prompt réellement encodé — disponible après exécution', lastPrompt, () => {}, 12);
      preview.readOnly = true;
      bound('shots_json', 'Storyboard JSON — sauvegardé dans le workflow', 12);
      el('p', 'Continuation : le latent précédent porte déjà la mémoire. Pour le nouveau Take, commence par une nouvelle vue caméra et une nouvelle action. La validation du Take se fait dans « Promax · COMMIT Take → Master ».', form);
      button(form, 'Recharger les plans depuis le JSON', () => { tab = 'Scénario'; draw(); });
      return;
    }
    bound('global_prompt', 'Direction globale : lieu, sujets, lumière, style', 3);
    bound('summary', 'Summary — Ref2V uniquement', 2);
    bound('soundscape', 'Ambiance sonore globale', 2);
    bound('music', 'Musique — laisser vide pour aucune musique', 1);
    let shots;
    try {
      shots = JSON.parse(widget(node, 'shots_json')?.value || '[]');
      if (!Array.isArray(shots)) throw new Error('Expected an array');
    } catch {
      el('p', 'JSON invalide : corrige-le dans Contrôle. Le contenu a été conservé.', form);
      return;
    }
    const status = el('p', '', form);
    const timeline = el('div', undefined, form);
    timeline.style.cssText = 'display:flex;gap:3px;height:24px;margin-bottom:10px';
    function refreshTime() {
      const total = shots.reduce((sum, shot) => sum + (Number(shot.seconds) || 0), 0);
      let frames = Math.ceil(total * 24 - 1e-8);
      frames += ((5 - frames) % 17 + 17) % 17;
      status.textContent = `${total.toFixed(2)} s demandées → ${(frames / 24).toFixed(3)} s / ${frames} frames à 24 fps`;
      status.style.color = total >= 4 && total <= 15 ? '#90e3c0' : '#ffb88c';
      timeline.replaceChildren();
      shots.forEach((shot, i) => {
        const part = el('div', String(i + 1), timeline);
        part.style.cssText = `flex:${Math.max(0.1, Number(shot.seconds) || 0)};background:#256078;text-align:center;border-radius:3px;padding-top:4px;font-size:12px`;
      });
    }
    const save = () => { write(node, 'shots_json', JSON.stringify(shots)); refreshTime(); };
    refreshTime();
    shots.forEach((shot, index) => {
      const card = el('div', undefined, form);
      card.style.cssText = 'background:#132134;border:1px solid #31475f;border-radius:6px;padding:9px;margin-bottom:10px';
      const row = el('div', undefined, card);
      el('strong', `PLAN ${index + 1} · `, row);
      const duration = el('input', undefined, row);
      duration.type = 'number'; duration.min = '0.25'; duration.max = '15'; duration.step = '0.25';
      duration.value = shot.seconds; duration.style.width = '65px';
      duration.addEventListener('input', () => { shot.seconds = Number(duration.value); save(); });
      el('span', ' secondes', row);
      button(row, '↑', () => { if (index) { [shots[index - 1], shots[index]] = [shots[index], shots[index - 1]]; save(); draw(); } });
      button(row, '↓', () => { if (index < shots.length - 1) { [shots[index], shots[index + 1]] = [shots[index + 1], shots[index]]; save(); draw(); } });
      button(row, 'Supprimer', () => { shots.splice(index, 1); save(); draw(); });
      field(card, 'Caméra — première instruction du plan', shot.camera || '', value => { shot.camera = value; save(); }, 1);
      field(card, 'Action visible', shot.action || '', value => { shot.action = value; save(); }, 3);
      field(card, 'Son / dialogue du plan', shot.audio || '', value => { shot.audio = value; save(); }, 1);
    });
    button(form, '+ Ajouter un plan', () => {
      if (shots.length >= 12) return;
      shots.push({ seconds: 2, camera: '', action: '', audio: '' }); save(); draw();
    });
  }
  draw();
  return { root, draw, executed(data) {
    lastReport = data?.promax_report?.[0] || lastReport;
    lastPrompt = data?.promax_prompt?.[0] || lastPrompt;
    if (tab === 'Contrôle') draw();
  }};
}
app.registerExtension({
  name: 'MiniMaxH3.Promax',
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== 'MiniMaxH3Promax') return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      for (const name of FIELDS) {
        const w = widget(this, name);
        if (!w) continue;
        w.hidden = true;
        w.options = { ...w.options, hidden: true };
        w.computeSize = () => [0, -4];
        w.draw = () => {};
        if (w.element) w.element.style.display = 'none';
      }
      this.promaxEditor = editor(this);
      this.addDOMWidget('promax_editor', 'promax-editor', this.promaxEditor.root,
        { serialize: false, getMinHeight: () => 680, getMaxHeight: () => 680 });
      this.setSize([680, Math.max(this.size[1], 1000)]);
      return result;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = configured?.apply(this, arguments);
      this.promaxEditor?.draw();
      return result;
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (data) {
      const result = executed?.apply(this, arguments);
      this.promaxEditor?.executed(data);
      return result;
    };
    const removed = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this.promaxEditor?.root.remove();
      return removed?.apply(this, arguments);
    };
  },
});
