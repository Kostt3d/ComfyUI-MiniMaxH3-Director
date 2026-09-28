// The 12 GB example marks only the nodes this manager is allowed to operate.
// One button press updates the actual LiteGraph modes before the graph is queued.
const { app } = window.comfyAPI.app;
const MANAGER = "MiniMaxH3RunManagerCS";
const ROLES = {
  load_latent: "MiniMaxH3EasyLoadLatent_SatoDive",
  master: "MiniMaxH3DirectorMasterChainCS",
  refine: "MiniMaxH3EasySegmentRefine_SatoDive",
  upscale_decode: "MiniMaxH3EasySegmentDecode_SatoDive",
  upscale_save: "SaveVideo",
};

function widget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}

function applyManager(manager) {
  const graph = manager.graph;
  if (!graph) return;
  const simple = widget(manager, "generation_simple");
  const continuation = widget(manager, "generation_continue");
  const upscale = widget(manager, "upscale_latent");
  if (!simple || !continuation || !upscale) return;

  // Recover an older or manually edited workflow with both toggles equal.
  if (Boolean(simple.value) === Boolean(continuation.value)) {
    simple.value = !Boolean(continuation.value);
  }
  const continuing = Boolean(continuation.value);
  const upscaling = Boolean(upscale.value);
  for (const node of graph._nodes || []) {
    if (node === manager) continue;
    if (node.properties?.h3_manager_owner !== manager.id) continue;
    const role = node.properties?.h3_manager_role;
    if (ROLES[role] !== node.type) continue;
    if (role === "master") {
      const reset = widget(node, "reset_chain");
      if (reset) reset.value = !continuing;
    } else {
      node.mode = role === "load_latent" ? (continuing ? 0 : 4) : (upscaling ? 0 : 4);
    }
    node.setDirtyCanvas?.(true, true);
  }
  manager.setDirtyCanvas?.(true, true);
}

app.registerExtension({
  name: "MiniMaxH3.RunManager",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== MANAGER) return;
    const original = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = original?.apply(this, arguments);
      for (const name of ["generation_simple", "generation_continue", "upscale_latent"]) {
        const entry = widget(this, name);
        if (!entry) continue;
        const prior = entry.callback;
        entry.callback = (...args) => {
          prior?.apply(entry, args);
          if (name === "generation_simple" && entry.value) {
            widget(this, "generation_continue").value = false;
          }
          if (name === "generation_continue" && entry.value) {
            widget(this, "generation_simple").value = false;
          }
          // One of the two generation modes must remain on.
          if (!widget(this, "generation_simple").value &&
              !widget(this, "generation_continue").value) entry.value = true;
          applyManager(this);
        };
      }
      return result;
    };
  },
  async afterConfigureGraph() {
    for (const node of app.graph?._nodes || []) {
      if (node.type === MANAGER) applyManager(node);
    }
  },
});
