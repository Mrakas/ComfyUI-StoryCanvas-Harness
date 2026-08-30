<div align="center">
  <img src="assets/storycanvas-mark.svg" width="104" alt="StoryCanvas 标志">
  <h1>StoryCanvas Harness</h1>
  <p><strong>面向 Agent 故事视频流水线的可组合、可检查运行时。</strong></p>
  <p>
    <a href="README.md">English</a> ·
    <a href="docs/ARCHITECTURE.md">架构</a> ·
    <a href="docs/PLUGIN_ARCHITECTURE.md">插件</a> ·
    <a href="docs/COMFYUI.md">ComfyUI</a>
  </p>
  <p>
    <a href="https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness/actions/workflows/ci.yml"><img src="https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache-2.0"></a>
    <img src="https://img.shields.io/badge/status-alpha-f59e0b" alt="Alpha">
  </p>
</div>

StoryCanvas 不是视频模型，而是把规划、参考图选择、生图、视频生成和评测方法组织成
**Typed Media DAG** 的 Harness。Agent 结束后，Prompt、视觉状态、依赖、产物、Receipt 和
SHA-256 仍然存在，不会消失在一次性脚本或黑盒 Trace 里。

<p align="center">
  <img src="figures/storycanvas-figure1.svg" width="100%" alt="Figure 1：StoryCanvas 把碎片化创作栈组织为可检查的故事 Harness">
</p>

<p align="center"><strong>Figure 1.</strong> 社区方法成为统一 Harness 中可替换的模块；中间媒体状态则沉淀为可编辑 Canvas、一致的多镜头视频和可复现评测。</p>

规范的 13 节点主图由确定性的 [FigureSpec](figures/specs/storycanvas-figure1.json)生成；
更底层的[实现拓扑](figures/storycanvas-topology.svg)放在架构文档中。

## 先看真实 Canvas

<p align="center">
  <a href="assets/demo/storycanvas-pipeline-demo-v3.mp4"><img src="assets/demo/storycanvas-pipeline-demo-v3-poster.webp" width="100%" alt="带 Director Agent 活动面板的 Moon Garden StoryCanvas 流水线演示"></a>
</p>

<p align="center"><a href="assets/demo/storycanvas-pipeline-demo-v3.mp4"><strong>观看 28 秒 MP4</strong></a> · <a href="examples/moon_garden_canvas/index.html">查看独立 Canvas 文件</a></p>

Moon Garden 是一个经过脱敏的真实运行：一个 Story Prompt、一套 Visual Bible、三个 Shot
Prompt、四张生成图、明确的前序帧/参考图依赖、三段 MiniMax-H3 视频和一个合并视频。
Viewer 完全本地、只读；`Reset / Next / Play` 只改变显现阶段，不调用 Provider，也不产生费用。

## StoryCanvas 的价值

- **可组合**：Director、Prompt 编译、参考图规划、生成器、评测器和 Host Adapter 都可通过
  类型化 Plugin 能力替换，而不需要重写整条流水线。
- **可检查**：Prompt、有序参考图、持久视觉状态、依赖、重试、媒体和 Receipt 可以在
  ComfyUI 或独立 Canvas Viewer 中查看。
- **可复现**：Profile 绑定精确 Plugin；组合、请求、参考图、Artifact 和输出 SHA 防止错误
  复用缓存，并支持重放。

## 快速开始

```bash
git clone https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness.git
cd ComfyUI-StoryCanvas-Harness
uv sync --extra dev

# 无密钥的类型化预览；不会搜索、生图或生成视频。
STORYCANVAS_PROVIDER_MODE=mock uv run storycanvas plan \
  --kind story --input examples/three_shot_story/input.json

# 运行本地组合兼容性 Demo。
uv run python scripts/run_plugin_demos.py

# 把任何已完成 Run 导出为独立媒体 DAG Viewer。
uv run storycanvas canvas-export \
  --run-dir /path/to/run-id --output-dir /path/to/canvas
```

通过本地静态服务器打开导出的 `index.html`。如需安装原生 ComfyUI 自定义节点，请看
[ComfyUI 指南](docs/COMFYUI.md)。

## 四个核心概念

| 概念 | 责任 |
|---|---|
| **Skill** | 说明、示例、模板和资产；本身没有执行权限。 |
| **Plugin** | 类型化能力，例如 `story.plan`、`reference.plan`、`media.image.generate`、`media.video.generate`、`evaluation.run` 或 `canvas.render`。 |
| **Profile** | 一次可复现运行所需的精确 Plugin、能力绑定、配置、权限与组合 SHA。 |
| **Media DAG** | 跨 Host 共享的持久图，保存 Prompt、视觉状态、依赖、Artifact、Receipt 与结果。 |

<p align="center">
  <img src="figures/storycanvas-plugin-architecture.svg" width="100%" alt="StoryCanvas Plugin 架构与扩展协议">
</p>

<p align="center"><strong>Plugin 架构。</strong> 社区方法只需封装一次，成为说明型 Skill 或类型化能力；Profile 显式选择组合，再由最小 Kernel 执行，无需 Fork 核心仓库。</p>

Kernel 统一管理生命周期、依赖、预算、校验、缓存和 Receipt；Host Adapter 则把同一套组合接到
Python、CLI、REST、Codex、ComfyUI，以及未来的 Pi / DeepSeek Harness / LibTV。详见
[Plugin 协议](specs/STORYCANVAS_PROTOCOL.md)、[第三方模板](plugins/template/)和
[兼容性 ADR](docs/adr/0001-plugin-kernel.md)。

## 示例

| 示例 | 展示内容 |
|---|---|
| [Moon Garden Canvas](examples/moon_garden_canvas/) | 经过脱敏的真实图片/视频、独立 Viewer、provenance 与 CC BY 4.0 媒体许可。 |
| [三 Shot Mock Story](examples/three_shot_story/) | 无密钥 Visual Bible、前序 Shot 链、Workflow、Prompt 与 Manifest。 |
| [单 Shot Mock](examples/single_shot/) | 最小 Schema、Compiler 与审计 Fixture。 |
| [Plugin Profiles](demos/plugin_profiles/) | Plan-only、连续性资产与完整 Mock Video 三种组合。 |

## 安全边界

默认模式是 `plan_only`。搜索、生图和付费视频必须显式开启并受 Execution Policy 限制；模糊的
付费任务创建响应不会盲目重试。密钥只从运行环境读取，不进入 Workflow、Manifest、HTML 或日志。
公共图片下载会拒绝私网、非图片响应和过大文件。

本仓库仍是 Alpha 研究预览。生产使用前请检查生成计划、模型条款、成本与许可证。更多内容见
[Provider](docs/PROVIDERS.md)、[API](docs/API.md)、[安全](SECURITY.md)、
[安全模型](docs/SECURITY_MODEL.md)和[限制](docs/LIMITATIONS.md)。

## 开发与许可

```bash
uv sync --extra dev --extra codex --extra demo
uv run ruff check .
uv run mypy --explicit-package-bases storycanvas_harness
uv run pytest
```

代码采用 [Apache-2.0](LICENSE)。Moon Garden 的图片、视频、图数据、封面和动画采用
[CC BY 4.0](examples/moon_garden_canvas/MEDIA_LICENSE.md)。
