# Model Benchmark Sample Report

This is a sanitized example of the real-model benchmark evidence format. Raw
benchmark runs live under `benchmark-runs/` and are ignored by git because they
can contain provider output, local paths, timing data, and prompts.

## What The Benchmark Checks

```sh
python3 scripts/model_benchmark.py --providers deepseek openai-chat
```

The suite creates isolated temp homes and runs:

| Provider | Cases |
|---|---|
| `deepseek` | mini harness, full tour, and Anthropic-compatible lesson scripts. |
| `openai-chat` | mini harness and full tour through the provider adapter. |

The OpenAI-compatible chapter boundary is intentional: most chapter scripts are
Anthropic-compatible teaching files; GPT gateway parity is taught in
`mini_workbuddy.providers` and `examples/full_tour`.

## Required Pass Markers

| Case Type | Evidence Required |
|---|---|
| mini | `Audit verified: True` and `Transcript events:` |
| full | `RESULT: OK`, `provider_probe: true`, and audit verification |
| lesson | scripted completion marker such as `DONE`, or a chapter-specific success marker |

## Sanitized Example Summary

```text
total cases: 24
passed: 24
failed: 0
providers: deepseek, openai-chat
raw stdout: stored locally under benchmark-runs/<run>/stdout/
```

## Publication Rule

Do not commit raw benchmark output. If maintainers want to publish a result,
copy only the aggregate counts, provider families, case IDs, and remediation
notes. Remove:

- API keys or authorization headers
- model raw responses that may contain private prompts or paths
- local temp directories
- provider request IDs
- user-specific environment values

The benchmark is an engineering gate, not a claim about a proprietary product.
