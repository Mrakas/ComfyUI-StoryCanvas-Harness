# Security and cost model

## Protected assets

- Provider/API credentials.
- Local reference images and their paths.
- Paid generation quota.
- The user’s existing ComfyUI workflow.
- Host filesystem and local/private network services.
- Provenance integrity of generated and searched assets.

## Trust boundaries

The following inputs are untrusted:

- free text and structured user input;
- LLM Director output;
- search results and remote image responses;
- provider status/error payloads;
- imported `CanvasPlan` and workflow files.

The deterministic compiler and execution policy are the enforcement boundary.

## Implemented controls

### Typed planning

The Director output must satisfy `DirectorDraft`, then `CanvasPlan`. Unknown fields, unsafe identifiers, unknown dependencies, duplicate shots, non-contiguous order, and invalid reference modes are rejected.

### No arbitrary node generation

The LLM never supplies a ComfyUI class name, link, node ID, path to execute, shell command, or Python source. The compiler emits only ten allowlisted classes.

### Spend gating

- `plan_only` is the default.
- Asset execution checks total predicted searches and images before the first provider call.
- Video execution requires `mode=full`, `allow_paid_video=true`, a positive video budget, and a configured provider.
- The ComfyUI builder previews exact predicted call counts and never queues automatically.

### Secret handling

Provider keys are read from environment variables. They are not inputs to graph nodes and are excluded from request hashes, receipts, manifests, HTML, examples, and logs. `.env` is ignored by Git.

### Filesystem boundaries

Agent-controlled asset, shot, plan, run, and job identifiers use a restricted safe-ID pattern. Generated filenames are derived only from validated identifiers. API lookup IDs are validated before path construction.

User reference paths remain an intentional local capability. Only files explicitly named in validated input are opened.

### Visual-search SSRF controls

Automatic visual search downloads:

- require `https`;
- resolve the hostname and reject any non-global address;
- validate every redirect target;
- reject non-image content types;
- cap response bytes at 20 MiB;
- decode through Pillow and re-encode pixels as PNG.

DNS rebinding and malicious image decoder vulnerabilities cannot be eliminated completely. Run custom nodes under an OS account with least privilege and keep dependencies patched.

### Paid task recovery

Task identity is written atomically. A task ID is reused across polling interruption. An ambiguous create response without a task ID requires manual reconciliation and is never automatically retried.

### Browser rendering

Dynamic titles and warnings are HTML-escaped before insertion into the StoryCanvas preview. Status and errors use `textContent`.

## Out of scope

- Provider-side retention, moderation, billing, availability, or account security.
- Safety of arbitrary third-party ComfyUI custom nodes installed beside StoryCanvas.
- Copyright or license status of user-selected search results.
- Isolation from a malicious local user with write access to the run directory.
- Model output correctness or identity fidelity.

## Recommended deployment

- Bind the standalone REST service to `127.0.0.1` unless protected by authentication and TLS.
- Store keys in a secret manager or process environment, not `.env` committed to source.
- Use separate provider projects/keys with spending limits.
- Review the `CanvasPlan` and call estimate before `assets` or `full` mode.
- Keep runs on a filesystem with appropriate access permissions.
- Pin provider revisions and this package release in research runs.
- Rotate any key that has been pasted into chat, an issue, a workflow, or a log.

Report vulnerabilities according to the root [SECURITY.md](../SECURITY.md).
