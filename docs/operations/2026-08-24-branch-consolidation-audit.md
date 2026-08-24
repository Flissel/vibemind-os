# Branch consolidation audit — 2026-08-24

## Scope and safety

This is a Git/workspace audit and local branch-cleanup record. It did not deploy, start a model, contact Proxmox, mutate a database, force-push, delete a remote branch, or remove a live worktree. The VibeMind invariant remains: no model may run on Proxmox.

## Remote baselines

- Outer `origin/master`: `db0dc2b0544c2672f30b0129880bfd892cc4e1d3`
- vibemind-os `origin/master`: `b1c03cf86608b1a0d31db82a0c1f5ee57ad7ad2c`
- OpenFang `origin/main`: `8a4904b3ff622d7ffda1e8cb01a509d2db430904`
- Voice `origin/main`: `a8c8d9f172a768850ad4cf999c6ebbb68bc6f015`

## Inventory after fetch and worktree prune

- Local branches across the four repositories after cleanup: **162**
- Already contained in the respective remote base after cleanup: **121**
- Contained and not assigned to a worktree after cleanup: **0**
- Contained but still assigned to a live worktree: **121** (preserved)
- Not contained in the respective remote base: **41**
- Removed stale worktree registrations: **128** (Outer 74, Child 49, OpenFang 5)
- Remaining prune dry-runs are empty.

Deletion execution result: **140/140** approved local branches were removed with safe `git branch -d`. Two stale upstream assignments were first corrected to the current `origin/master`, allowing the same non-force deletion. All listed remote refs remain untouched.

## Integrated and verified batch 1

Integration branch: `codex/integration/branch-consolidation-2026-08-24` at `7f840bd5d4a8017421c7cf278610e0e9c312fab9`.
Remote: `origin/codex/integration/branch-consolidation-2026-08-24`.

Merged source branches:

- `codex/fix/openfang-external-agent-generation` at `73932c9d99271d4c7814b0ffa96ef2d1a57873f8`
- `codex/rowboat/openai-plugin-runtime-v1` at `1c0f7c2cf7466340f39161de5765a8a69affde35`

Verification on the merged checkout:

- `python -m pytest scripts/tests/test_sync_openfang_agents.py -q`: 19 passed
- Rowboat Vitest: 3 files, 36 tests passed
- Rowboat `tsc --noEmit`: passed
- `git diff --check origin/master...HEAD`: passed
- Worktree status: clean

The branch is pushed, but this audit does not claim that a pull request or a merge into `master` exists.

## Contained local branches deleted after explicit approval

All branches listed below were revalidated as ancestors of the current remote base, confirmed unassigned to any worktree, and then deleted locally. No force deletion was used.

### outer (84)

- `chore/remove-clawcode`
- `claude/blissful-jackson-63bf0f`
- `claude/nostalgic-brahmagupta-b06d0e`
- `claude/ops-docs-aug01-07-recovery`
- `claude/outer-bubble-promote-pin-v1`
- `claude/outer-captain-cook-pin-v1`
- `claude/outer-rowboat-chat-pin-v1`
- `claude/relaxed-hamilton-c7f456`
- `codex/agentfarm/task-agentfarm-0001-team-contract-v1`
- `codex/agentfarm/task-agentfarm-0002-chat-boundary-spec-v2`
- `codex/brain/task-brain-0001-plan-contract`
- `codex/brain/task-brain-0002-plan-admission-bundle-v1`
- `codex/brain/task-brain-0003-plan-lifecycle-contract-v1`
- `codex/brain/task-brain-0017-openfang-handoff-contract-v1`
- `codex/bubbles/task-bubbles-0002-parent-pin`
- `codex/channels/task-channels-0001-intent-contract`
- `codex/channels/task-channels-0002-result-route-contract`
- `codex/coding/task-coding-0001-code-job-contract-v1`
- `codex/deployment/task-deployment-0001-readiness-preflight`
- `codex/deployment/task-deployment-0002-contract-readiness-gate`
- `codex/deployment/task-deployment-0006-postmerge-static-gate-fix-v1`
- `codex/deployment/task-deployment-0006-static-golden-path-predeploy-gate-v1`
- `codex/deployment/task-deployment-0007-proxmox-operations-runbook-bundle-v1`
- `codex/deployment/task-deployment-0008-authority-chain-static-pin-alignment-v1`
- `codex/deployment/task-deployment-0009-agentfarm-pin-static-predeploy-regate-v1`
- `codex/desktop/task-desktop-0001-action-contract-v1`
- `codex/flowzen/task-flowzen-0001-recommendation-contract-v1`
- `codex/git/task-git-0003-submodule-readiness`
- `codex/git/task-git-0004-vibemind-os-integration-parent-pin-v1`
- `codex/git/task-git-0014-repository-fork-reconciliation-plan-v1`
- `codex/git/task-git-0016-reconciliation-plan-remote-basis-v1`
- `codex/git/task-git-0018-manifest-gitlink-pin-reconciliation-v1`
- `codex/git/task-git-0019-vibemind-os-parent-gitlink-pin-v2`
- `codex/git/task-git-0020-vibemind-os-authority-chain-parent-pin-v1`
- `codex/git/task-git-0021-vibemind-os-agentfarm-pin-v1`
- `codex/governance/task-governance-0007-gitlink-provenance-alignment-v2`
- `codex/governance/task-governance-0008-session-portfolio-registry-v1`
- `codex/governance/task-governance-0013-openfang-mvp-scope-v1`
- `codex/governance/task-governance-0026-external-cockpit-reference-reconciliation-v1`
- `codex/governance/task-governance-0027-cockpit-approval-activation-reconciliation-v1`
- `codex/governance/task-governance-0028-cockpit-portfolio-status-check-v1`
- `codex/ideas/task-ideas-0002-parent-pin`
- `codex/integration/task-integration-0001-mvp-scope-precedence`
- `codex/integration/task-integration-0002-openfang-llm-space-sot-v1`
- `codex/integration/task-integration-0018-brain-openfang-contract-package-parity-v1`
- `codex/integration/task-integration-fungus-os-shield-pin-v1`
- `codex/marketing/task-marketing-0001-cockpit-contract-drift-guard`
- `codex/marketing/task-marketing-0002-renderer-pytest-return-contract`
- `codex/minibook/task-minibook-0001-projection-contract-v1`
- `codex/mirofish/task-mirofish-0001-simulation-contract-v1`
- `codex/n8n/task-cockpit-n8n-0001-operation-authority-contract`
- `codex/n8n/task-n8n-0001-workflow-contract-v1`
- `codex/observability/task-observability-0001-evidence-governance-v2`
- `codex/observability/task-observability-0002-cockpit-ledger-reconciliation-v1`
- `codex/observability/task-observability-0003-post-pin-ledger-reconciliation-v1`
- `codex/observability/task-observability-0004-post-pr112-ledger-reconciliation-v1`
- `codex/observability/task-observability-0005-post-authority-pin-ledger-reconciliation-v1`
- `codex/observability/task-observability-0006-agentfarm-post-pin-ledger-reconciliation-v1`
- `codex/ops/task-ops-0016-operational-line-port-sanitized-v1`
- `codex/platform/task-platform-0001-governance-contracts`
- `codex/platform/task-platform-0002-git-worktree-gate`
- `codex/platform/task-platform-0003-pr-readiness`
- `codex/platform/task-platform-0004-session-routines`
- `codex/platform/task-platform-0006-governance-provenance-v2`
- `codex/research/task-research-0001-evidence-contract-v1`
- `codex/research/task-research-0003-chat-status-provenance-v1`
- `codex/rowboat/task-rowboat-0001-knowledge-contract-v1`
- `codex/schedule/task-schedule-0001-contract-v1`
- `codex/schedule/task-schedule-0003-chat-boundary-contract-v1`
- `codex/shared/task-shared-0001-contract-core`
- `codex/shared/task-shared-0002-space-execution-contract`
- `codex/shared/task-shared-0002-space-execution-contract-recovery`
- `codex/shared/task-shared-0005-rowboat-outer-identity-alignment-v1`
- `codex/shared/task-shared-0006-brain-lifecycle-space-identity-alignment-v1`
- `codex/v1-cleanroom-submodule-base`
- `codex/video/task-video-0001-job-contract-v1`
- `feat/cleanup-stages-1-4-20260528`
- `feat/mcp-docker`
- `feat/mcp-tool-hub`
- `feat/phase-11-capability-router`
- `feat/pitch-deck-2026`
- `feat/som-resume-loop`
- `lab-backup/chore/remove-clawcode`
- `lab-backup/feat/mcp-tool-hub`

### child (43)

- `chore/remove-clawcode`
- `claude/brain-d1-error-semantics-v1`
- `claude/bridge-map-registry-alignment-v1`
- `claude/captain-cook-agentfarm-runtime`
- `claude/captain-cook-mcp-server-v1`
- `claude/generator-mcp-tools-allowlist-v1`
- `claude/openfang-child-rowboat-chat-pin-v1`
- `claude/voice-bubble-promote-pin-v1`
- `codex/brain/deterministic-rowboat-gateway-v1`
- `codex/brain/task-brain-0004-subagent-openfang-client-v1`
- `codex/brain/task-brain-0004-subagent-openfang-client-v2`
- `codex/brain/task-brain-0005-multi-llm-router-openfang-v1`
- `codex/brain/task-brain-0006-runtime-openfang-config-v1`
- `codex/brain/task-brain-0007-openfang-mcp-executor-v1`
- `codex/brain/task-brain-0008-registered-openfang-fail-closed-v1`
- `codex/brain/task-brain-0010-disable-implicit-ollama-v1`
- `codex/brain/task-brain-0013-shared-client-port-v1`
- `codex/brain/task-brain-0014-supabase-ideas-openfang-boundary-v1`
- `codex/bubbles/task-bubbles-0009-error-semantics-v1`
- `codex/embedding-service/task-embedding-service-0001-openfang-gateway-v1`
- `codex/fungus-parent-pin-pr11`
- `codex/governance/task-governance-0013-moire-gitmodule-registration-v1`
- `codex/integration/task-git-0015-child-fork-reconciliation-v1`
- `codex/integration/task-git-0016-space-phase11-baseline-v1`
- `codex/integration/task-integration-0004-openfang-config-sot-v1`
- `codex/openfang/task-openfang-0005-mcp-authority-gitlink-v1`
- `codex/openfang/task-openfang-0006-embedding-config-gitlink-v1`
- `codex/ops/task-ops-0007-poc-os-shield-openfang-v1`
- `codex/shared/task-shared-0006-base-url-env-gitlink-v1`
- `codex/shared/task-shared-0006-openfang-llm-gateway-v1`
- `codex/shared/task-shared-0009-embedding-gitlink-v1`
- `codex/spaces/task-spaces-ideas-mcp-server-v1`
- `feat/agentic-os-bausteine`
- `feat/canvas-bidi-reformat`
- `feat/faceswap-gpu-offload-prep`
- `feat/som-resume-loop`
- `fix/fungus-http-persistent`
- `main`
- `org-backup/feat/faceswap-gpu-offload-prep`
- `org-backup/feat/phase-11-capability-router`
- `org-backup/main`
- `org-backup/master`
- `refactor/ops-to-subdir`

### openfang (8)

- `claude/openfang-rowboat-chat-manifest-v1`
- `codex/feat-embedding-driver-bounded-retry-v1`
- `codex/feat-openai-embeddings-v1`
- `codex/integration/task-openfang-embedding-compat-v1`
- `codex/openfang/laura-mcp-registration-v1`
- `codex/openfang/task-openfang-0005-embedding-example-config-v1`
- `feat/phase-11-vibemind-integration`
- `main`

### voice (5)

- `chore/langgraph-pin`
- `claude/db-bubble-promote-mcp-v1`
- `feat/phase-2-yaml-classifier`
- `feat/phase-a-skip-brain-spawn`
- `main`

## Local branches not contained in the remote base

### outer (16)

- `codex/governance/task-governance-0009-local-ci-governance-gate-v2`
- `codex/governance/task-governance-0010-manifest-severity-v1`
- `codex/governance/task-governance-0012-private-submodule-checkout-authority-v1`
- `codex/governance/task-governance-0014-local-ci-gate-v3` — checked out at `C:/Users/User/.codex/worktrees/outer-governance-ci-gate-v3`
- `codex/governance/task-governance-0015-manifest-severity-v2` — checked out at `C:/Users/User/.codex/worktrees/outer-manifest-severity-v2`
- `codex/opencode-subscription-integration` — checked out at `C:/Users/User/.codex/worktrees/opencode-subscriptions`
- `codex/opencode-subscription-integration-v2` — checked out at `C:/Users/User/Desktop/Vibemind_Worktrees/opencode-subscriptions`
- `codex/openfang/runtime-admission-design-v1` — checked out at `E:/CodexWorktrees/openfang-runtime-admission-design`
- `codex/ops/task-ops-0016-operational-line-port-v1`
- `codex/v1-space-orchestration-design` — checked out at `C:/Users/User/.codex/worktrees/v1-space-orchestration-design`
- `codex/vibemind-ops-baseline` — checked out at `C:/Users/User/Desktop/Vibemind_V1`
- `feat/face-math-region-composite`
- `feat/faceswap-gpu-offload-prep`
- `feat/phase-coding-engine-p2ab`
- `lab-backup/master`
- `master`

### child (20)

- `codex/agentfarm-brain-contract`
- `codex/brain/task-brain-0009-shared-client-migration-v1`
- `codex/bubbles-brain-orchestration`
- `codex/coding-brain-execution`
- `codex/desktop-space-orchestration`
- `codex/fix/openfang-external-agent-generation` — checked out at `C:/Users/User/.codex/worktrees/vibemind-os-external-agent-generation`
- `codex/flowzen-brain-path`
- `codex/ideas-brain-orchestration`
- `codex/integration/branch-consolidation-2026-08-24` — checked out at `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.worktrees/branch-consolidation-2026-08-24`
- `codex/minibook-brain-execution`
- `codex/mirofish-brain-orchestration`
- `codex/n8n-brain-execution-target`
- `codex/research-brain-execution-target`
- `codex/roarboot-space-orchestration`
- `codex/rowboat/openai-plugin-runtime-v1` — checked out at `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.worktrees/rowboat-openai-plugin-runtime-v1`
- `codex/schedule-brain-execution`
- `codex/space-contract-consistency`
- `codex/video-brain-execution`
- `feat/mcp-tool-hub`
- `master` — checked out at `C:/Users/User/Desktop/Vibemind_V1/vibemind-os`

### openfang (4)

- `claude/codex-exec-subscription-driver-v1`
- `claude/codex-exec-tool-modes-v1` — checked out at `C:/Users/User/ClaudeWork/wt-codex-toolmodes`
- `feat/mcp-tool-hub` — checked out at `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.git/modules/openfang`
- `refactor/openfang-disk-cleanup`

### voice (1)

- `feat/canvas-bidi-reformat` — checked out at `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.git/modules/voice`

## Next controlled sequence

1. Review and merge `codex/integration/branch-consolidation-2026-08-24` into vibemind-os `master` through the normal protected integration path.
2. Update and verify the Outer vibemind-os Gitlink in a separate clean branch.
3. Port or close the old divergent Outer/Child/OpenFang/Voice lines one bounded batch at a time; do not merge the dirty legacy checkouts wholesale.
4. Completed: removed the 140 approved contained, unassigned local branches with safe `git branch -d`.
5. Treat live or dirty worktrees separately; never delete them as part of bulk cleanup.
