# ADR 0001: Plugin kernel and compatibility-first migration

Status: accepted

## Decision

StoryCanvas will expose a media-domain Protocol, a dependency-aware Plugin
Registry, Skills, Packs, Profiles, and Host adapters. Existing `StoryCanvas`,
`storycanvas/v1`, CLI, REST, and ComfyUI custom-node APIs remain compatible.

The first implementation stays in one Python distribution. Logical package
boundaries are enforced by imports and tests before publishing multiple wheels.

## Rationale

Immediately splitting every component into a separately released package would
make ComfyUI installation and compatibility harder before the Plugin ABI is
stable. Keeping a compatibility layer allows current Runs and workflows to load
while external methods begin targeting the new SDK.

## Consequences

- New provider/model integrations are Plugins, not branches in `from_environment`.
- ComfyUI is a Host/Renderer rather than the core data model.
- Skills can guide an Agent but cannot perform unrecorded side effects.
- The existing Provider protocols are compatibility services in Plugin API v1.
- Reference/Prompt methods use validated post-Director `PlanProcessor` hooks.
- Plugin composition participates in Run identity and is recorded in the Manifest.
- Evaluator artifacts are admitted through the same path/SHA/receipt boundary.
