# Third-Party Notices

This repository is MIT licensed. It also depends on third-party Python
packages for demos and tests. Their licenses are governed by their respective
projects.

| Package | Why It Is Used | Notes |
|---|---|---|
| `anthropic` | Anthropic-compatible teaching path and DeepSeek compatibility. | Imported lazily when a real provider is selected. |
| `openai` | OpenAI Responses API provider path. | Imported lazily when `--provider openai` is selected. |
| `python-dotenv` | Load local `.env` files for optional real-provider demos. | No secrets should be committed. |
| `pyyaml` | Parse chapter progression metadata and structured teaching files. | Used by tests and verification utilities. |
| `pytest` | Offline test suite. | Required for local and CI verification. |

The tutorial code does not vendor these packages. Install them with:

```sh
pip install -r requirements.txt
```

Before adding a dependency, prefer the Python standard library for teaching
clarity. If a dependency is necessary, document why it exists here and keep
provider SDK imports lazy so offline demos remain deterministic.
