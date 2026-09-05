# Optimization notes and roadmap / 优化记录与后续建议

This document separates delivered foundation work from proposals. The proposals below are not
implemented features. Scope: the published StoryCanvas Harness repository and its existing hosts.

## Delivered in this pass / 本轮已完成

- **Correct execution:** validate DAG IDs, cycles, references, shot order, and graph-derived budgets; reject excess shots without truncation. Plans and executions use content fingerprints, including provider/Profile configuration.
- **Recoverable local runs:** lock duplicate execution/cache writes, persist service requests, detect interrupted jobs, finish failure manifests, and distinguish generation attempts from cache reuse. Resume known video tasks and validate cached video hashes/decoding.
- **Usable entry points:** lazy provider setup for help/health/compile, early input validation, `doctor`, a packaged offline `demo`, JSON Run summaries, and failure exit codes.
- **Host and export reliability:** common ComfyUI budget checks, stale preview rejection, exact plan snapshots on Apply, contained/relocatable media paths, sanitized/escaped Viewer text, and responsive playback controls.
- **Maintenance:** precise Plugin dependency bindings and startup cleanup; regression/browser/wheel checks; bilingual quick starts and corrected real/mock example descriptions.

These changes do not evaluate real-provider output quality. Published Moon Garden media remains
unchanged. Historical manifests remain readable; the new execution identity intentionally uses
separate Run directories instead of silently trusting old cache identities.

## Proposed next work / 下一阶段建议

| Priority | Feature | User benefit | Scope / prerequisites | Acceptance criteria |
|---|---|---|---|---|
| P1 | Run library and comparison / 运行记录与对比 | Find outputs, compare prompts, providers, errors, and counters without browsing directories | Medium; index existing manifests read-only, with thumbnails and filtering | Open any indexed Run, compare two Runs, and distinguish failed/missing artifacts from successful outputs |
| P1 | Selective DAG rerun / 按节点局部重跑 | Change one prompt or reference and regenerate only affected descendants | Medium–large; explicit invalidation, lineage, preview of affected nodes and allowed calls | Unaffected verified artifacts retain their SHA; changed descendants get a new Run lineage and bounded execution |
| P2 | Provider/Profile setup assistant / 配置向导 | Make the first real run as easy as the offline demo | Medium; supported provider catalog, redacted config editing, optional explicit connectivity check | Save a valid Profile and show an accurate graph/call preview before execution; credentials never enter exports |
| P2 | Evaluation comparison recipes / 评估对照模板 | Researchers can compare reference planners, prompts, and generators under the same inputs | Medium–large; stable evaluator contracts and dataset-level aggregation | Export per-example results, failures/coverage, configuration hashes, and comparable aggregate scores |
| P3 | Portable Run bundles / 可迁移运行包 | Share or move an experiment between machines with its provenance | Medium; relative paths, license metadata, bundle-size checks | Export/import verifies every declared artifact SHA and opens offline without original absolute paths |

Recommended order: Run library → selective rerun → setup assistant. These directly shorten the
find → inspect → edit → rerun loop; evaluation recipes should follow once the comparison inputs
and failure accounting are settled.

建议先做“运行记录与对比”，用户能立即找到和比较产物；再做“局部重跑”，降低每次修改的时间与
调用量；之后补“配置向导”，把 Mock 示例到真实 Provider 的上手过程连起来。评估模板需要先明确
评价指标与数据划分，避免把不完整运行混进质量对比。
