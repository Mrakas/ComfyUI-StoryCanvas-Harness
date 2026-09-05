# Changelog

All notable changes are documented here. The project follows semantic versioning once a stable release is published.

## [Unreleased]

### Added

- Offline `doctor` diagnostics and a packaged three-shot `demo`, with optional local video and browser opening.
- Machine-readable Run/continuation output, nonzero failure exit codes, and actionable input errors.
- Semantic plan/execution fingerprints, local cross-process locks, persisted service requests, and interrupted-job detection.
- Browser checks for Canvas media, keyboard/mobile controls, and stale ComfyUI previews; isolated wheel-install checks and Python 3.13 CI.

### Fixed

- Invalid DAGs, duplicate asset IDs, forward previous-shot references, silently truncated shot inputs, and budgets derived from untrusted estimates.
- Cache identity, relative/relocated Run paths, image attempt accounting, terminal failure manifests, interrupted JSONL writes, and provider cleanup.
- Duplicate paid task creation after interrupted submission and unverified video cache reuse.
- Plugin dependency binding/startup cleanup, eager provider initialization during CLI help/API health, and leaked or unescaped Viewer titles.
- Preview/apply races, Canvas Reset/Next state, and destructive demo output cleanup.

### Documentation

- Simplified bilingual quick starts, explicit environment loading, Run recovery/accounting boundaries, and a prioritized feature roadmap.
- Corrected the distinction between published real Moon Garden media and local mock fixtures.

## [0.1.0] - 2026-08-21

### Added

- Typed `storycanvas/v1` planning and audit schemas.
- Deterministic ComfyUI UI/API workflow compiler with native per-shot subgraphs.
- OpenAI Director, factual web search, image generation/editing, optional Serper visual search, and MiniMax-H3-compatible video adapters.
- Three execution modes with explicit paid-video gate and call budgets.
- DAG execution, request/SHA cache recovery, task-ID resume, manifests, receipts, Prompt JSONL, and audit HTML.
- Python SDK, CLI, REST service, ComfyUI nodes, and browser Builder.
- Fictional single-shot and three-shot examples.
