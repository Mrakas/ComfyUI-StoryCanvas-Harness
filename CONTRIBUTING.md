# Contributing

Thank you for helping make agent-authored visual workflows more inspectable and reproducible.

## Before opening a pull request

1. Open an issue for schema changes, new paid providers, or changes to recovery semantics.
2. Keep the LLM/agent side typed. Do not add a path where a model emits arbitrary ComfyUI nodes or executable code.
3. Preserve exact reference order and provenance.
4. Keep `plan_only` as the default and put billable operations behind explicit policy gates.
5. Never commit credentials, private benchmark data, third-party model weights, or internal infrastructure details.

## Development setup

```bash
git clone https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness.git
cd ComfyUI-StoryCanvas-Harness
uv sync --extra dev
uv run pytest
```

Before submitting:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy -p storycanvas_harness
uv run pytest
uv run python scripts/verify_examples.py
```

## Provider checklist

A new provider must include:

- a small protocol-compatible class;
- environment/secret-manager credentials, never node fields;
- stable request identity and typed receipt;
- bounded HTTP and polling timeouts;
- failure and recovery tests;
- ordered reference SHA coverage;
- no blind retry of ambiguous billable creation;
- documentation of external pricing/licensing responsibility.

## Schema changes

`storycanvas/v1` is a public contract. Additive fields should have safe defaults. Breaking changes require a new schema version, migration notes, regenerated examples, and compatibility tests.

## Example policy

Checked-in examples must be fictional and redistributable. Use mock providers unless all external assets have explicit compatible licenses. Regenerate with:

```bash
uv run python scripts/build_examples.py
```

Review all diffs, then verify the embedded SHA manifests.

## Pull requests

Describe:

- the user-visible behavior;
- the trust/cost boundary affected;
- tests performed;
- provider calls, if any;
- backward compatibility;
- screenshots for ComfyUI frontend changes.

By contributing, you agree that your contribution is licensed under Apache-2.0.
