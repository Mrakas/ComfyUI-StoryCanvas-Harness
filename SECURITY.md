# Security policy

## Supported versions

Security fixes are applied to the latest released minor version. This project is currently alpha; pin exact versions for research runs.

## Reporting a vulnerability

Please use GitHub’s **Report a vulnerability** / private security advisory flow for this repository. Do not open a public issue for credential exposure, arbitrary file access, SSRF, command execution, cost-gate bypass, or duplicate paid-task creation.

Include:

- affected version/commit;
- reproduction steps with mock credentials and fictional data;
- expected and observed behavior;
- impact;
- suggested mitigation, if known.

We will acknowledge a valid report as soon as practical, coordinate a fix, and credit the reporter unless anonymity is requested.

## Immediate credential response

If a key has appeared in a workflow, log, screenshot, issue, chat, or commit, rotate/revoke it with the provider immediately. Removing it from Git history does not make the exposed credential safe again.

For the threat model and deployment recommendations, see [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).
