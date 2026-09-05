# Rollout checks

Before merging Match Selection Intelligence:

- targeted intelligence regressions must pass;
- true 1xBet LineFeed probe must decode P1/X/P2 and paired totals;
- the full repository pytest workflow must pass on the pull request;
- the deployment bundle must be produced by the standard `tests` workflow.

After merge, deploy/restart is still a separate operational step; a merged GitHub commit alone does not prove the VPS has restarted on the new code.
