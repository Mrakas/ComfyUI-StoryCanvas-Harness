---
name: continuity-planning
description: Plan explicit cross-shot visual dependencies without chaining unrelated shots.
capabilities:
  - story.plan
  - reference.plan
---

# Continuity Planning

Use this skill when a story contains recurring characters, locations, wardrobe,
props, or visible state changes.

## Rules

1. Define reusable identity and environment anchors before final shot keyframes.
2. Add `previous_shot` only when the place and visible state genuinely persist.
3. Break the previous-frame chain after a location or time change; reuse stable
   identity and style anchors instead.
4. Keep every ordered reference role explicit. Never claim an image was supplied
   unless its ArtifactRef and SHA are present.
5. Record state transitions as data, not only as prose in a generation Prompt.

This file guides an Agent. It does not execute providers or bypass StoryCanvas
policy, Plugin lifecycle, Artifact, or Receipt validation.
