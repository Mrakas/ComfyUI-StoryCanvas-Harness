## Summary

## Trust / cost boundary affected

## Tests

- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy -p storycanvas_harness`
- [ ] `pytest`
- [ ] ComfyUI/browser validation if UI or workflow serialization changed

## Checklist

- [ ] No credentials, private benchmark data, internal infrastructure, or third-party weights.
- [ ] Agent output remains typed; workflow serialization remains deterministic.
- [ ] Ordered references, provenance, receipts, and spend gates remain explicit.
- [ ] Documentation/examples are updated where needed.
