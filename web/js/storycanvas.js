import { app } from "../../scripts/app.js";

const API_ROOT = "/storycanvas/v1";

function element(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function apiCall(path, payload) {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.detail?.error || `HTTP ${response.status}`);
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function policyFromDialog(dialog) {
  const mode = dialog.querySelector("[name=execution_mode]").value;
  const allowPaid = dialog.querySelector("[name=allow_paid]").checked;
  return {
    mode,
    allow_paid_video: mode === "full" && allowPaid,
    max_shots: Number(dialog.querySelector("[name=max_shots]").value),
    max_search_calls: Number(dialog.querySelector("[name=max_search]").value),
    max_image_calls: Number(dialog.querySelector("[name=max_images]").value),
    max_video_calls: mode === "full" && allowPaid ? Number(dialog.querySelector("[name=max_videos]").value) : 0,
    max_concurrency: Number(dialog.querySelector("[name=max_concurrency]").value),
    require_preview: true,
  };
}

function renderPreview(container, plan) {
  const counts = plan.call_estimate;
  container.innerHTML = `
    <div class="sc-preview-head"><strong>${escapeHtml(plan.title)}</strong><span>${plan.shots.length} shot(s)</span></div>
    <div class="sc-call-grid">
      <div><b>${counts.planning_calls}</b><span>planning</span></div>
      <div><b>${counts.fact_search_calls}</b><span>fact search</span></div>
      <div><b>${counts.image_generation_calls}</b><span>images</span></div>
      <div><b>${counts.video_generation_calls}</b><span>videos</span></div>
    </div>
    <div class="sc-paid ${counts.paid_video_locked ? "locked" : "unlocked"}">
      ${counts.paid_video_locked ? "Paid video is locked" : "Paid video is explicitly unlocked"}
    </div>
    <ul>${plan.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>
  `;
}

function openBuilder() {
  document.querySelector(".storycanvas-overlay")?.remove();
  const overlay = element("div", { class: "storycanvas-overlay" });
  const dialog = element("section", { class: "storycanvas-dialog" });
  dialog.innerHTML = `
    <header><div><small>AGENT → TYPED PLAN → COMFYUI</small><h2>Build StoryCanvas</h2></div><button data-close>×</button></header>
    <div class="sc-body">
      <label>Input type<select name="input_kind"><option value="shot">Single 10-second shot</option><option value="story">Full story</option></select></label>
      <label>Input format<select name="input_format"><option value="free">Free text</option><option value="json">Structured JSON</option></select></label>
      <label class="sc-wide">Story / shot input<textarea name="prompt" rows="8">A fictional clockmaker repairs a tiny mechanical bird. The bird wakes, tests its wings, then lands gently on the workbench.</textarea></label>
      <label>Execution preview<select name="execution_mode"><option value="plan_only">Plan only (safe default)</option><option value="assets">Search + images</option><option value="full">Full including video</option></select></label>
      <label class="sc-checkbox"><input type="checkbox" name="allow_paid"> Explicitly allow paid video calls</label>
      <label>Max shots<input name="max_shots" type="number" min="1" max="24" value="12"></label>
      <label>Max search calls<input name="max_search" type="number" min="0" value="8"></label>
      <label>Max image calls<input name="max_images" type="number" min="0" value="16"></label>
      <label>Max video calls<input name="max_videos" type="number" min="0" value="12"></label>
      <label>Max concurrency<input name="max_concurrency" type="number" min="1" max="16" value="4"></label>
    </div>
    <div class="sc-preview" data-preview><p>Build a typed plan to inspect exact call counts and warnings. No workflow is changed yet.</p></div>
    <footer><span data-status>Ready</span><button data-build>Build preview</button><button data-apply disabled>Apply to new workflow</button></footer>
  `;
  overlay.append(dialog);
  document.body.append(overlay);
  let currentPlan = null;
  let currentPolicy = null;
  const status = dialog.querySelector("[data-status]");
  const applyButton = dialog.querySelector("[data-apply]");
  dialog.querySelector("[data-close]").onclick = () => overlay.remove();
  overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); });
  dialog.querySelector("[data-build]").onclick = async () => {
    try {
      status.textContent = "Planning…";
      applyButton.disabled = true;
      const kind = dialog.querySelector("[name=input_kind]").value;
      const format = dialog.querySelector("[name=input_format]").value;
      const raw = dialog.querySelector("[name=prompt]").value;
      const payload = format === "json" ? JSON.parse(raw) : (kind === "shot" ? { prompt: raw } : { free_text: raw });
      currentPolicy = policyFromDialog(dialog);
      currentPlan = await apiCall("/plans", { input_kind: kind, payload, policy: currentPolicy });
      renderPreview(dialog.querySelector("[data-preview]"), currentPlan);
      applyButton.disabled = false;
      status.textContent = "Preview ready — nothing queued";
    } catch (error) {
      status.textContent = `Error: ${error.message}`;
    }
  };
  applyButton.onclick = async () => {
    try {
      status.textContent = "Compiling ComfyUI workflow…";
      const compiled = await apiCall("/workflows", { plan_id: currentPlan.plan_id, policy: currentPolicy });
      const filename = `StoryCanvas-${currentPlan.story_id}-${currentPlan.plan_id}.json`;
      await app.loadGraphData(compiled.workflow, true, true, filename);
      status.textContent = "Applied to a new workflow tab. Review it, then Queue manually.";
      setTimeout(() => overlay.remove(), 1300);
    } catch (error) {
      status.textContent = `Error: ${error.message}`;
    }
  };
}

const style = document.createElement("style");
style.textContent = `
.storycanvas-overlay{position:fixed;inset:0;z-index:100000;background:#000a;display:grid;place-items:center;padding:24px}
.storycanvas-dialog{width:min(900px,96vw);max-height:92vh;overflow:auto;background:#151817;color:#f3f4ef;border:1px solid #56605a;box-shadow:0 30px 100px #000b;font:14px/1.4 Inter,system-ui,sans-serif}
.storycanvas-dialog header,.storycanvas-dialog footer{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:18px 22px;border-bottom:1px solid #3c423f}
.storycanvas-dialog footer{border-top:1px solid #3c423f;border-bottom:0;position:sticky;bottom:0;background:#151817}.storycanvas-dialog h2{font-size:30px;margin:2px 0}.storycanvas-dialog small{font:10px ui-monospace,monospace;letter-spacing:.15em;color:#b8ff35}
.storycanvas-dialog button{background:#b8ff35;color:#111;border:0;padding:11px 16px;font-weight:750;cursor:pointer}.storycanvas-dialog button[disabled]{opacity:.3;cursor:not-allowed}.storycanvas-dialog header button{font-size:25px;background:transparent;color:#fff;padding:2px 8px}
.sc-body{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;padding:20px 22px}.sc-body label{display:grid;gap:6px;color:#bac2bd;font-size:12px}.sc-body input,.sc-body select,.sc-body textarea{background:#0d0f0e;color:#fff;border:1px solid #434b46;padding:10px;font:13px ui-monospace,monospace}.sc-wide{grid-column:1/-1}.sc-checkbox{display:flex!important;align-items:center;grid-template-columns:auto 1fr!important}.sc-checkbox input{width:18px;height:18px}
.sc-preview{margin:0 22px 20px;border:1px solid #3c423f;background:#0d0f0e;padding:16px}.sc-preview-head{display:flex;justify-content:space-between}.sc-call-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}.sc-call-grid div{background:#1c211e;padding:12px;display:grid}.sc-call-grid b{font-size:26px;color:#b8ff35}.sc-call-grid span{font-size:10px;text-transform:uppercase}.sc-paid{padding:9px 12px;border-left:4px solid #b8ff35}.sc-paid.locked{border-color:#ffcb4c}.sc-preview li{margin:6px 0;color:#c9cfcb}
@media(max-width:650px){.sc-body{grid-template-columns:1fr}.sc-wide{grid-column:auto}.sc-call-grid{grid-template-columns:repeat(2,1fr)}}`;
document.head.append(style);

app.registerExtension({
  name: "StoryCanvas.Harness",
  commands: [{ id: "storycanvas.build", label: "Build StoryCanvas…", function: openBuilder }],
  menuCommands: [{ path: ["Extensions", "StoryCanvas"], commands: ["storycanvas.build"] }],
  getCanvasMenuItems() {
    return [null, { content: "Build StoryCanvas…", callback: openBuilder }];
  },
});
