# Plugin architecture

StoryCanvas follows Pi Agent's clear package/resource organization and DeepSeek
Harness's capability, dependency, lifecycle, Profile, and Bundle principles.
The implementation remains intentionally small enough to install as one ComfyUI
custom node package during the v0.1 compatibility phase.

```text
Hosts: CLI · REST · Pi · DeepSeek Harness · Codex · ComfyUI · LibTV
                              │
                       Host adapters
                              │
Kernel: registry · resolver · policy · DAG · artifacts · event log
                              │
                    StoryCanvas Protocol
                              │
Plugins: Director · references · prompts · search · image · video · eval · renderer
```

## Import direction

```text
protocol  <-  sdk  <-  plugins
    ^          ^
 kernel  <-----
    ^
  hosts
```

The compatibility modules under `providers/`, `workflow.py`, and `engine.py`
remain public in v0.1. New integrations should target `protocol`, `sdk`, Plugin
manifests, and Profiles. Once the Plugin API stabilizes, these logical packages
can become separate Python distributions without changing their public names.

## Lifecycle

`DISCOVERED -> WAITING -> LOADING -> ACTIVE -> UNLOADING -> DISPOSED`

A failed startup enters `FAILED`. The registry starts a Plugin only after all
required capabilities are active. A Profile binding selects one provider when
multiple Plugins expose the same capability.

Profiles also contain an explicit `allow_permissions` list. A Plugin requesting
a permission outside that list is rejected before `start()`. This is an
admission-control contract, not an operating-system sandbox; Hosts that run
untrusted third-party code should add process/container isolation.

## Third-party discovery

External packages publish a zero-argument factory under the
`storycanvas.plugins` Python entry-point group. The entry-point name and the
returned `manifest.plugin_id` must match. Discovery is deliberately opt-in:
StoryCanvas imports only Plugins explicitly listed by the active Profile, then
checks the Plugin API version, declared capabilities, dependencies, and service
surface before lifecycle activation.

```toml
[project.entry-points."storycanvas.plugins"]
"community.video.example" = "community_video_plugin:create_plugin"
```

See `plugins/template/` for the minimal package contract.

## Planning hooks

The Director is not the only swappable planning stage. Plugins bound to
`reference.plan`, `prompt.compile.image`, or `prompt.compile.video` implement
the small `PlanProcessor` protocol and run after the Director in that fixed
order. Every processor receives a deep copy, must return a valid `CanvasPlan`,
and cannot change the immutable input SHA. This is the intended integration
point for community reference-selection, prompt-scaling, camera, or temporal
prompt methods without copying the execution engine.

Each Profile composition has a stable SHA over Plugin manifests, versions,
bindings, configuration, and admitted permissions. New Run identity includes
that composition SHA plus the typed plan and execution policy, so switching a
Plugin cannot silently reuse a Run created by another composition.

An optional `evaluation.run` Plugin executes after media generation. Evaluation
outputs are admitted only when their IDs are unique, their files remain inside
the Run root, their SHA-256 values match, and their receipts name the active
evaluator. A failed evaluator is recorded as a real Run error and changes the
Run to `partial`; it cannot silently publish a score.

## Built-in reference Profile

`profiles/basic.json` uses deterministic Director, search, image, video, and
ComfyUI compiler Plugins. It is credential-free and suitable for tests and
examples. It proves that the Harness can run before community Skills and Packs
are installed.

Inspect it with:

```bash
uv run storycanvas profile-inspect profiles/basic.json
```

Run through it with:

```bash
uv run storycanvas run --profile profiles/basic.json \
  --kind story --input examples/three_shot_story/input.json \
  --mode assets --max-shots 3 --max-image-calls 4
```
