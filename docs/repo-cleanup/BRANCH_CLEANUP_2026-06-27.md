# Branch Cleanup Plan — 2026-06-27

Purpose: clean Arbiter repo before sending/pushing the new Alex feature plan.

## Current branch state

Checked from local repo on 2026-06-27 around 01:10 UK.

Current branch:

- `main` at `1c15098` — clean, tracking `origin/main`.

Local branches:

- `main`
- `feat/f1-nemotron3-default` at `b27121d`
- `chore/repo-cleanup-docs-20260627` — current cleanup/docs branch

Remote branches:

- `origin/main` at `1c15098`
- `origin/alex/dashboard-build` at `ababeea`
- `origin/feat/f1-nemotron3-default` at `b27121d`
- `origin/feat/hermes-native-mcp-governance` at `80a0c17`

## Branch assessment

### `origin/alex/dashboard-build`

Status:

- Old dashboard branch.
- Merge-base is the branch tip `ababeea`, so `main` contains it and has moved on.
- Diffing branch against current main shows it is missing many later backend/operator/procurement/test files.

Recommendation:

- Safe to delete after Alex confirms he has no unpushed/local-only work depending on this branch.

Delete command, once confirmed:

```powershell
git push origin --delete alex/dashboard-build
```

### `origin/feat/f1-nemotron3-default`

Status:

- F1/B0/F4/F5/F3 branch.
- Remote tree is identical to current `main` (`git diff --quiet main..origin/feat/f1-nemotron3-default` returns clean), but history is divergent because `main` has integration commit `1c15098`.
- The local branch points to the same remote branch.

Recommendation:

- Safe to delete local branch now if desired.
- Safe to delete remote branch after confirming `main` tests pass, because the actual file tree is already on `main`.

Local delete command:

```powershell
git branch -D feat/f1-nemotron3-default
```

Remote delete command, once confirmed:

```powershell
git push origin --delete feat/f1-nemotron3-default
```

### `origin/feat/hermes-native-mcp-governance`

Status:

- Old governance/policy-as-config branch.
- Not safe to blindly delete yet.
- Contains useful salvage candidates:
  - `policy.example.yaml`
  - `tests/test_policy_config.py`
  - stricter policy-config validation ideas
- It is otherwise old and missing many current main files.

Recommendation:

- Do not delete until one of these happens:
  1. cherry-pick/copy the useful policy example/tests into a new current branch, or
  2. explicitly decide those ideas are not needed for Owner Policy Setup.

Likely best action:

- Salvage `policy.example.yaml` and the validation-test ideas into the current feature branch when implementing Owner Policy Setup.
- Then delete the remote branch.

Inspect commands:

```powershell
git show origin/feat/hermes-native-mcp-governance:policy.example.yaml
git show origin/feat/hermes-native-mcp-governance:tests/test_policy_config.py
```

## Repo structure recommendation

Current structure is mostly acceptable:

```text
arbiter/       Python source package
dashboard/     static UI
docs/          docs/plans/handoffs/specs/diagrams
integration/   Hermes integration config
scenarios/     JSON fixtures
scripts/       helper scripts
tests/         pytest suite
```

Do **not** move `arbiter/` to `src/` right before submission. It would require `pyproject.toml` changes, import/package verification, and could break a clean project at the wrong time.

Instead, clean by moving new planning docs into `docs/` and leaving source layout stable.

## Docs added by cleanup branch

- `PROJECT.md` — repo recovery/context file for future work.
- `docs/plans/ARBITER_FEATURE_NOTES_2026-06-27.md` — full feature pool and product notes.
- `docs/handoffs/ARBITER_ALEX_FEATURE_PLAN_2026-06-27.md` — Alex-specific implementation/UI plan.
- `docs/repo-cleanup/BRANCH_CLEANUP_2026-06-27.md` — this branch cleanup plan.

## Proposed next cleanup steps

1. Run tests on cleanup/docs branch.
2. Commit docs only.
3. Push `chore/repo-cleanup-docs-20260627`.
4. Share `docs/handoffs/ARBITER_ALEX_FEATURE_PLAN_2026-06-27.md` with Alex.
5. Delete local `feat/f1-nemotron3-default` after docs branch is safe.
6. Ask Ben/Alex before deleting remote branches.
7. Salvage or discard governance branch policy-config material before deleting it.
