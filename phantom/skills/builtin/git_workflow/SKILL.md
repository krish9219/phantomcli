---
name: git_workflow
description: Phantom's house style for git commits and branches.
tags: git, workflow
trigger: commit, branch, pr, pull request, merge
---

# Git workflow

* Use trunk-based development. Feature branches live ≤ 3 days.
* Commit messages: imperative mood, ≤ 72 chars on the subject line.
* Squash-merge PRs by default; keep linear history.
* Never force-push to `main`.
