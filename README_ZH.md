<div align="center">
  <img src="assets/storycanvas-mark.svg" width="116" alt="StoryCanvas Harness 标志">
  <h1>ComfyUI StoryCanvas Harness</h1>
  <p><strong>把故事变成可审计、可编辑的多参考图 ComfyUI 画布。</strong></p>
  <p><a href="README.md">English</a> · <a href="docs/ARCHITECTURE.md">架构</a> · <a href="docs/COMFYUI.md">ComfyUI 指南</a></p>
</div>

StoryCanvas Harness 不是一个视频模型。它是规划 Agent、视觉搜索/生图工具和视频后端之间的控制与审计层：Director 先把自然语言或结构化 Story 转成严格的 `CanvasPlan`，确定性编译器再把计划编译成原生 ComfyUI Workflow，每个 Shot 都是可展开的子图；运行时保留每张图的实际 Prompt、有序参考图、receipt、SHA-256、重试与最终输出。

最终得到的不是 Agent trace 截图，也不是 LLM 随意拼出的 Comfy JSON，而是一张真的可以检查、修改并手动 Queue 的 ComfyUI 画布。

> **Alpha / 研究预览。** 默认是 `plan_only`，不会调用搜索、生图或付费视频接口。只有显式切换模式并配置预算门禁后，执行器才会解锁对应操作。

## 它解决什么问题

传统 ComfyUI 很灵活，但复杂 Story 的 Visual Bible、跨镜头状态和参考图顺序通常需要手工维护；很多 Story Agent 会自动规划，却把依赖关系、成本和来源藏在黑盒里。StoryCanvas 把两者接起来：

- Agent 负责理解 Story、规划角色/地点/道具状态、决定哪些 Shot 真正需要前序帧。
- Typed Schema 约束 Agent 输出，Agent 不能直接生成任意工作流节点。
- Compiler 负责生成合法、可展开的 ComfyUI 子图。
- Harness 负责预算门禁、DAG 调度、缓存、恢复、SHA 和 provenance。
- 用户最终在 ComfyUI 里看懂并控制整个流程。

## 核心能力

- 单个约 10 秒 Shot 和多 Shot Story 两种输入。
- 自然语言与结构化 JSON 两种输入格式。
- Story 级 Visual Bible，以及角色、地点、道具和状态约束。
- Canvas 生图最多 5 张有序参考图；MiniMax-H3 兼容请求最多 9 张。
- OpenAI Responses 结构化 Director、事实 `web_search`、OpenAI 生图/编辑、可选 Serper 视觉搜索。
- MiniMax-H3 兼容异步 API：task ID 立即持久化；创建响应不明确时停止，不盲目重试付费任务。
- 原生 ComfyUI Subgraph：顶层是共享资产，每个 Shot 是一个可展开子图。
- UI Workflow 和 API-format Workflow 同时输出。
- `plan_only`、`assets`、`full` 三档权限；`full` 还必须显式设置 `allow_paid_video=true`。
- Python SDK、CLI、REST、ComfyUI 自定义节点和前端 Builder。

## 快速开始

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness.git
cd ComfyUI-StoryCanvas-Harness
python -m pip install -r requirements.txt
```

重启 ComfyUI，然后打开 **Extensions → StoryCanvas → Build StoryCanvas…**。先点击 Build 预览精确调用量与警告，再点击 Apply；它会打开一个新 Workflow Tab，不会覆盖当前画布，也不会自动 Queue。

Shot 子图需要 ComfyUI frontend 1.24.3 或以上。可参考 ComfyUI 官方的 [Subgraph 文档](https://docs.comfy.org/interface/features/subgraph)和[自定义节点安装文档](https://docs.comfy.org/installation/install_custom_node)。

无密钥体验：

```bash
uv sync --extra dev
STORYCANVAS_PROVIDER_MODE=mock uv run storycanvas plan \
  --kind shot --prompt "A fictional clockmaker repairs a tiny mechanical bird."

STORYCANVAS_PROVIDER_MODE=mock uv run storycanvas run \
  --kind story --input examples/three_shot_story/input.json \
  --mode assets --max-shots 3 --max-image-calls 4
```

真实 Provider 只从环境变量取密钥：

```bash
export OPENAI_API_KEY="..."
export OPENAI_TEXT_MODEL="gpt-5"
export OPENAI_IMAGE_MODEL="gpt-image-1"

export MINIMAX_H3_API_KEY="..."
export MINIMAX_H3_BASE_URL="https://your-compatible-service.example"
export MINIMAX_H3_MODEL="MiniMax-H3"
```

密钥不会写入 Workflow、Manifest、HTML 或 Git。MiniMax 适配器遵循 [`ComfyUI-MiniMaxH3-API`](https://github.com/meta-sota/ComfyUI-MiniMaxH3-API/tree/0d1c72b1d80a54237b40adb111ae74d7fe38f4b4) 的公开协议；服务可用性、计费和审核策略不属于本仓库保证范围。

## 两个开源示例

- [单 Shot：输入](examples/single_shot/input.json) · [Canvas Workflow](examples/single_shot/workflow.json) · [Prompt 审计](examples/single_shot/prompts.jsonl) · [Manifest](examples/single_shot/run_manifest.json)
- [三 Shot：输入](examples/three_shot_story/input.json) · [Canvas Workflow](examples/three_shot_story/workflow.json) · [Prompt 审计](examples/three_shot_story/prompts.jsonl) · [Manifest](examples/three_shot_story/run_manifest.json)

示例中的故事和图片全部是虚构的、确定性 Mock 产物，不包含私有 Benchmark 数据。三 Shot 示例清晰展示了图片依赖：Visual Bible 可以并行完成；Shot 2 因同场景连续性依赖 Shot 1；Shot 3 再依赖 Shot 2。

## 代码库到底是什么

它不只是“一堆告诉 Agent 怎么做的 Markdown”。Markdown 只是说明书，真正执行约束在代码里：

```text
schemas.py       Agent 可输出什么，以及什么计划会被拒绝
providers/       规划、搜索、生图、视频接口
engine.py        预算门禁、DAG 调度、缓存与断点恢复
workflow.py      CanvasPlan → ComfyUI UI/API Workflow
comfy_nodes.py   10 个可执行自定义节点
comfy_api.py     ComfyUI 内置 REST 路由
web/js/          Build → Preview → Apply 到新画布
```

Agent 每个 Story 默认只进行一次结构化规划，不会把一整晚的执行日志不断塞回同一个超长上下文。逐资产执行由确定性 Scheduler 管理，完成项通过 request SHA 和 receipt 复用。

## 安全与诚实边界

- 搜索图、用户图和生成图使用不同 provenance，不互相冒充。
- Visual Search 只允许公共 HTTPS，阻止私有/保留地址、非图片响应和超过 20 MiB 的下载，并重新解码后保存。
- 模糊的付费视频创建响应不会自动重试。
- 公开仓库不包含私有 Benchmark、内部 GPU/集群脚本、模型权重、密钥或研究输出。
- Mock 示例证明的是 Schema、画布与审计链路，不代表真实视觉质量。

更多内容见 [架构](docs/ARCHITECTURE.md)、[Provider](docs/PROVIDERS.md)、[REST API](docs/API.md)、[安全模型](docs/SECURITY_MODEL.md)和[局限](docs/LIMITATIONS.md)。

## 开发

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy -p storycanvas_harness
uv run pytest
```

Apache-2.0，见 [LICENSE](LICENSE)。
