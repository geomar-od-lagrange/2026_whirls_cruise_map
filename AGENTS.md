# Agent guidelines for this project

(This file is `AGENTS.md` on disk and symlinked to `CLAUDE.md`.)

## Scope

Mapping drifter and other device positons during the 2026 Whirls Cruise of 
R/V Marion Dufresne and R/V Agulhas II.

## Principles

**This is pre-alpha research code.** No installed base, no users to migrate.
Internal API changes are free. Anything prefixed with `_` is internal.

**Greenfield mindset.** If the current shape is in the way of the right shape,
reshape it. Don't add workaround constraints when restructuring eliminates the
problem. Deletions, renames, and rewrites are the normal mode.

**Be ruthless about dropping dead code.** Patch sparingly; rewrite when the
abstraction is wrong. Prefer clean parameter plumbing over clever hacks — no
monkey-patches, global state swaps, or closure tricks when passing a parameter
through the call chain is cleaner.

**Be diligent about follow-through.** When you touch a name, signature, or
path, grep for every reference and update them in the same pass. Don't leave
stale imports, dead references, or half-updated docs for a later cleanup step.

**Don't be unnecessarily specific.** State the assumption-free version rather
than inventing cadences, numbers, or relationships that aren't established, and
don't draw definite conclusions from limited or pre-deployment data. When a
fact isn't known, hedge or omit instead of guessing.

**Memory lives in the repo, not in the agent.** Never rely on hidden or
internal agent memory. Every durable decision, preference, or piece of context
belongs in an explicit, version-controlled artifact — `AGENTS.md`, `docs/`, or
`plans/`. Read context from those, and write it back there.

## Agent workflow

**Planning before code:** Write plans to `plans/*.md` before touching source.
Don't skip planning for complex changes; don't let implementation agents make
architectural decisions unguided.

**Model choice:** Use a lighter model for mechanical and verification tasks.
Reserve more capable models for architecture, design decisions, and judgment
calls.

**Always review after implementation:** A separate review agent should examine
the result. This catches both conceptual mistakes and quality issues.

**Experimental validation:** Use `tmp_*/` directories to prove ideas before
committing to architecture changes. Once validated, clean up or move to
permanent locations.

**Adapt the environment, don't work around it:** When dev tooling is missing
(a pytest plugin, a linter, a profiler), `pixi add` it and commit the
manifest/lock change alongside the work that needed it. Don't build subprocess
or shell workarounds around an absent tool — the pixi env is part of the
workspace, not a fixed constraint.

**Debugging discipline:** Hand focused debugging down to a cheaper agent with
a dense handoff of the facts already established, so nothing gets re-derived
in an expensive context. Reproduce a failure **once** with a tight timeout,
dumping complete output to a temp file, and inspect the dump — don't rerun
long commands blind. For a stubborn bug, a second, independent *reasoning-only*
pass over the code (no profiling) is cheap and catches what instrumentation
was pointed away from. `pytest.ini` sets a hard per-test timeout so hangs fail
loudly; run tests in the foreground — never as a background task an agent then
waits on.

**No git worktrees:** Work in the single primary workdir and switch branches
there; branch in place for isolated work rather than spawning worktrees. Stray
worktrees checking out `main` block the primary checkout and pile up as stale
entries (`.claude/worktrees/wf_*`, a `scratchpad/preview-wt`). To reset the
workdir: `git worktree remove <path>` / `git worktree prune`, then
`git checkout main && git merge --ff-only origin/main`.

## Conventions

### Documentation

`docs/*.md` contains standalone documentation for the current state of the
code. Each doc should make sense on its own without referencing previous
implementations, changelogs, or development history. Explain design choices by
comparing alternatives and their trade-offs, not by narrating what changed.
Git history is the changelog; docs describe what *is*, not what *was*.

`plans/*.md` describe intent before implementation. When a plan is
implemented: write a corresponding `docs/` file, move the plan to
`plans/done/`, and add a one-liner at the top pointing to the doc. Plans have
no frontmatter or structured metadata — `ROADMAP.md` and `BACKLOG.md` in
`plans/` provide the index. Agents get context by reading `docs/*.md` (what
is) + open `plans/*.md` (what's next).