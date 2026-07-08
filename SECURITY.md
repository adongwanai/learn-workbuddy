# Security Policy

`learn-workbuddy` is an educational clean-room harness, not a production
sandbox. The code is designed to teach where safety boundaries belong:
permission gates, path checks, audit trails, tool-result externalization, and
explicit provider configuration.

## Supported Scope

Security reports are welcome for:

- accidental secret disclosure in the repository
- unsafe default commands in teaching demos
- path traversal or workspace escape bugs in `mini_workbuddy`
- misleading documentation that could cause readers to trust the teaching
  harness as a production sandbox
- clean-room boundary violations

This project does not accept reports that require proprietary WorkBuddy source
code, private package contents, private prompts, user data, or bypass details
for third-party products.

## Reporting

Open a private maintainer contact channel if available on the hosting platform,
or create a GitHub issue with a minimal public description and no sensitive
payload. Do not paste API keys, local logs, package excerpts, private prompts,
or machine-specific evidence into public issues.

## Secret Handling

Never commit real credentials. Use `.env.example` as the template and keep real
values in `.env`, which is ignored by git. Before publishing a branch, run:

```sh
python3 -m pytest -q
python3 scripts/verify.py
```

The project-specific scanner is useful, but it is not a replacement for a
general-purpose secret scanner such as gitleaks or trufflehog.

## Production Warning

The mini harness intentionally uses simple teaching policies. A real desktop
agent needs stronger boundaries: OS-level sandboxing, filtered subprocess
environments, network egress controls, signed extension distribution, and
human approval for high-risk actions.

See `docs/security-boundaries.md` for the detailed safety model.
