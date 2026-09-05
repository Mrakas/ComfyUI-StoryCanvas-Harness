# Getting started / 上手指南

## First run / 第一次运行

Install Python 3.10–3.13 and uv, then run `uv sync` in the checkout. An installed wheel also
provides the `storycanvas` command; omit the `uv run` prefix when using its activated environment.

```bash
uv run storycanvas doctor
uv run storycanvas demo --open
```

`doctor` only inspects local configuration, writable output paths, and dependencies. Warnings
such as an unset OpenAI key do not block the offline demo. Errors exit with code 1. Use
`--json` for a machine-readable report and `--mode assets` or `--mode full` to check the
requirements of that execution mode. A successful check does not verify credentials online.

`demo` 生成三个镜头的本地 Mock 图片并导出 Canvas，不需要密钥、网络或 GPU。运行结束后会
打印 Canvas、Manifest、Audit 的绝对路径；`--open` 会打开 Canvas。默认目录为 `output/demo`，
可通过 `--output-dir` 修改。重复运行会校验并复用缓存，保留目录里的其他 Run 和文件。

```bash
# Optional local video demo; install ffmpeg and ffprobe first.
uv run storycanvas demo --with-video --open

# Machine-readable paths and counters, without opening a browser.
uv run storycanvas demo --output-dir output/my-demo --json
```

The colored mock images/videos demonstrate the workflow and provenance, not model quality.
For published real media, inspect [Moon Garden](../examples/moon_garden_canvas/).

## Your own story / 自定义故事

The basic Profile uses deterministic local providers. Wrapped example JSON infers `input_kind`;
plain JSON and free text default to one shot unless `--kind story` is supplied.

```bash
uv run storycanvas plan --profile profiles/basic.json \
  --kind story --prompt $'A paper fox enters a garden.\nThe fox finds a blue lantern.' \
  --output output/my-plan.json

uv run storycanvas compile output/my-plan.json \
  --output output/workflow.json --api-output output/workflow-api.json

uv run storycanvas run --profile profiles/basic.json \
  --plan output/my-plan.json --mode assets --max-image-calls 4 --json
```

Inspect the plan before execution. Shot counts above `--max-shots` produce an error instead of
silently truncating the story. The runtime validates dependencies, reference order, and budgets
from the actual graph. `--plan` cannot be combined with `--prompt`, `--input`, or `--kind`.

命令输出中的 `run_dir` 就是本次运行目录；不要猜测 Run ID。把该路径代入下面的命令：

```bash
uv run storycanvas canvas-export --run-dir /path/from/run_dir \
  --output-dir output/my-canvas
```

Open the printed `index.html` locally. Use Reset/Next/Play to inspect the graph, click a node
for its details, or focus a node and press Enter/Space. Escape closes details. Narrow screens
show readable, scrollable node cards; desktop screens retain the full dependency graph.

## Real providers / 切换真实 Provider

The demo always stays local. For real execution, configure environment variables and omit the
mock `--profile`. See [Providers](PROVIDERS.md) for the endpoint contracts and Codex setup.
`.env.example` is a template: copying it to `.env` does **not** load it automatically.

```bash
cp .env.example .env
# Edit .env locally, then explicitly load it:
uv run --env-file .env storycanvas doctor --mode assets
uv run --env-file .env storycanvas plan --kind story --input your-story.json
```

A real Director can incur a planning call even in `plan_only`; that mode disables media and
evaluation calls. `doctor` only reports whether credentials/configuration are present.
Provider defaults remain configurable and their availability is not assumed from the model name.

Images require `--mode assets` (or `full`). Real video requires `--mode full`,
`--allow-paid-video`, an adequate `--max-video-calls` budget, and the configured video endpoint.
The graph preview's estimates do not override those limits.

## Continue and automate / 续跑与自动化

To add videos to a completed image Run, keep the Run directory and its verified artifacts:

```bash
uv run --env-file .env storycanvas complete-videos \
  /path/from/run_dir --allow-paid-video --max-video-calls 3 --json
```

For mock continuation, add `--profile profiles/basic.json`. Video-only continuation does not
need an image provider or an image budget. A persisted task ID is resumed; an interrupted paid
creation without a task ID requires manual reconciliation with the provider before retrying.
Do not delete task receipts to force another paid submission.

`run --json` 和 `complete-videos --json` 会输出状态、路径与错误。只有 `complete` 退出码为 0；
`partial` 和 `failed` 为 1。发生失败时先查看 Manifest 中的错误和 `receipts/`，不要把“目录存在”
当作运行成功。CLI 参数或配置错误输出到 stderr；不保证这些错误为 JSON。

Service requests are persisted and duplicate execution is locally locked. After a service crash,
reading an orphaned job marks it interrupted; explicitly resubmit to recover. Existing completed
or partial jobs remain idempotent. See [API](API.md) for exact behavior and counter semantics.
