# Public Research Material Review

This note summarizes how public-facing research drafts were converted into a
clean-room tutorial. It deliberately avoids machine-specific sample identifiers, package
internals, raw logs, private paths, exact implementation constants, and
unverified product-specific names.

## Review Principle

The source materials mixed three different kinds of claims:

| Claim Type | How The Tutorial Uses It |
|---|---|
| Public behavior | Safe to describe at product-behavior level. |
| Architecture interpretation | Safe when phrased as a transferable pattern. |
| Private or version-specific detail | Omitted or generalized before publication. |

The tutorial keeps the architecture lessons and discards the brittle
implementation evidence. That is why the code in this repository is written as
small Python teaching examples rather than extracted JavaScript, bundled
assets, or private prompt material.

## Concepts Kept

The following ideas are strong enough to teach as general harness patterns:

- sidecar/session runtime instead of running all agent work in the UI process
- local control plane for session lifecycle and reconnect
- tool discovery and deferred schema loading
- permission hooks before high-risk actions
- layered memory: workspace, user preference, transcript, and remote recall
- model routing across light, default, and stronger reasoning slots
- subagent isolation through summarized results rather than raw shared history
- output externalization for large tool results
- context compaction before the model window is exhausted
- append-only audit trails for command and tool actions

Each concept appears in the chapter map and is implemented with original code.

## Concepts Generalized

Some draft wording was useful as a mental model but too product-specific for a
public repository. These were rewritten as generic teaching terms:

| Draft Shape | Public Tutorial Shape |
|---|---|
| Exact agent counts | Role categories: main, subagent, auxiliary, summarizer. |
| Exact tool or connector counts | Extension surfaces and lazy discovery. |
| Concrete package or runtime file names | Desktop shell, sidecar, runtime, storage, connector proxy. |
| Local database/table observations | SQLite persistence and queryable session metadata. |
| Product-specific memory implementation | Memory manifest, selector, recall, and prompt injection points. |
| Private model-slot configuration | Cost-aware routing: lite/default/craft as a teaching abstraction. |

## Concepts Deferred

The tutorial avoids presenting these as facts unless they can be explained as
generic design options:

- exact internal names of private agents, tools, routes, files, or tables
- version-specific product constants
- service-side recall implementations
- private prompt templates
- claims about commercial roadmap or non-public behavior

## Current Tutorial Fit

The current structure is aligned with the public-safe lessons:

| Tutorial Area | Research Lesson |
|---|---|
| `s01`-`s04` | Agent loop, tool dispatch, deferred loading, permission gates. |
| `s05`-`s09` | Desktop shell, sidecar, session lifecycle, model routing, transcript. |
| `s10`-`s15` | Memory, recall, output externalization, compaction, prompt assembly. |
| `s16`-`s18` | Skills, MCP-style connectors, experts. |
| `s19`-`s24` | Visualization, presentation, SQLite, automation, audit, integration. |

## Publication Rule

Before adding new research notes, rewrite them into this format:

```text
private observation -> generic architecture problem -> original teaching implementation
```

Do not commit package excerpts, raw logs, local paths, hashes, secrets, user
data, or exact private implementation names. The repository should remain a
clean-room learning project that teaches the harness architecture without
depending on proprietary material.
