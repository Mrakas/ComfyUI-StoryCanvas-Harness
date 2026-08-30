# StoryCanvas Protocol v1

StoryCanvas is a media-native Agent Harness protocol, not a video model and not
a ComfyUI-specific workflow format. The protocol lets planning methods, media
providers, evaluators, and Hosts exchange auditable work without importing each
other's implementation.

## Five stable records

1. **ArtifactRef** — immutable, content-addressed text, image, video, or JSON.
2. **TaskSpec** — requested capability, typed inputs, configuration, and DAG dependencies.
3. **TaskResult** — output Artifacts and an optional validated graph patch.
4. **Receipt** — actual provider/model, request identity, Prompt, ordered reference
   SHAs, attempts, timing, and provider task ID.
5. **RunEvent** — append-only durable fact used for progress, recovery, replay, and UI projection.

The current `storycanvas/v1` records in `storycanvas_harness.schemas` remain
wire-compatible while these records are separated into dedicated modules.

## Public composition concepts

- **Plugin:** executable code providing one or more named capabilities.
- **Skill:** Agent-facing instructions, templates, and examples; it has no direct
  execution authority.
- **Pack:** installable distribution containing Plugins, Skills, defaults, and tests.
- **Profile:** one selected composition of Plugin IDs, capability bindings, and configuration.
- **Host:** an environment such as CLI, REST, Pi, DeepSeek Harness, Codex, ComfyUI, or LibTV.

## Kernel invariants

- The kernel never imports a model/provider SDK or concrete Host.
- Plugins cannot mutate immutable historical Artifacts or Receipts.
- A capability with multiple providers requires an explicit Profile binding.
- Reference and Prompt processors run in the fixed order `reference.plan` →
  `prompt.compile.image` → `prompt.compile.video`, and each output is revalidated.
- Required capabilities must be active before a dependent Plugin starts.
- Plugin unload reverses its registrations and releases its resources.
- Network, filesystem, subprocess, paid-call, and secret permissions are declared.
- Ambiguous billable creation is never retried without a durable provider task ID.
- Run identity includes the Profile composition SHA, typed plan, and execution policy.
- Evaluation outputs require a matching evaluator receipt, a valid SHA, and a
  path inside the Run root; evaluator failure is represented as `partial`.

## Plugin API

Plugin manifests use `storycanvas/plugin/v1`. The initial Python API exposes a
small lifecycle (`start`, `stop`), named services, a dependency-aware registry,
and a conformance surface. It is intentionally smaller than the media protocol
so future subprocess/RPC plugins can implement the same contract.
