---
description: Post-PR workflow - address comments and fix conflicts
---

# Post-PR Workflow

When the user says "postPR", follow this workflow to address PR comments and fix conflicts.

## Steps

### 1. Fetch PR Status and Comments

```bash
# Get PR number from context or ask user
gh pr view <PR_NUMBER> --json state,reviews,comments,mergeable

# View detailed comments
gh pr view <PR_NUMBER> --comments
```

### 1.1. Check CI/CD Status

```bash
# Check the status of GitHub Actions/Checks
gh pr checks <PR_NUMBER>

# If failures exist, view logs for the failed run
# gh run view <RUN_ID> --log
```

### 2. Check for Merge Conflicts

```bash
# Fetch latest from base branch (usually dev)
git fetch origin dev

# Check if there are conflicts
git merge-base --is-ancestor origin/dev HEAD || echo "Conflicts may exist"

# Attempt merge to see conflicts
git merge origin/dev --no-commit --no-ff
```

If conflicts exist:
```bash
# Abort the merge attempt
git merge --abort

# Rebase onto dev
git rebase origin/dev

# Resolve conflicts as they appear
# For each conflict:
#   1. Edit the file to resolve
#   2. git add <file>
#   3. git rebase --continue
```

### 3. Address Review Comments

For each review comment:

1. **Read the comment carefully** - Understand what the reviewer is asking for
2. **Locate the code** - Find the file and line mentioned
3. **Make the fix** - Implement the suggested change or improvement
4. **Test the change** - Run relevant tests to ensure fix works
5. **Commit the fix** - Use descriptive commit message referencing the comment

Example commit messages:
```bash
git commit -m "fix: address review comment - improve error handling in session validation"
git commit -m "refactor: extract session validation logic per review feedback"
git commit -m "test: add missing test case for session completion edge case"
```

### 4. Run Tests

```bash
# Backend tests (from repo root)
python -m pytest -v

# Linting
ruff check .
cd apps/web && npm run lint
```

### 5. Push Changes

```bash
# Push all fixes
git push origin <branch-name>

# If rebased, force push (use with caution)
git push origin <branch-name> --force-with-lease
```

### 6. Respond to Comments

After addressing each comment:
```bash
# Reply to the comment on GitHub
gh pr comment <PR_NUMBER> --body "✅ Fixed in commit <commit-hash>"
```

Or respond in bulk:
```bash
gh pr comment <PR_NUMBER> --body "Addressed all review comments:
- Fixed error handling in session validation
- Added missing test case for edge case
- Extracted validation logic for reusability
All tests passing (120/120)"
```

### 7. Request Re-review

```bash
# Request review from the same reviewer
gh pr edit <PR_NUMBER> --add-reviewer <reviewer-username>
```

## Common Scenarios

### Scenario: Simple Comment Fixes (No Conflicts)
1. Checkout PR branch
2. Make fixes
3. Run tests
4. Commit and push
5. Respond to comments

### Scenario: Merge Conflicts
1. Fetch latest dev
2. Rebase onto dev
3. Resolve conflicts
4. Run tests
5. Force push with lease
6. Address any remaining comments

### Scenario: Major Refactor Requested
1. Create implementation plan for refactor
2. Get user approval
3. Implement changes
4. Run full test suite
5. Update PR description if needed
6. Push and respond

## Checklist

- [ ] Fetched latest PR status and comments
- [ ] Checked CI/CD status and fixed any failures
- [ ] Checked for merge conflicts
- [ ] Addressed all review comments
- [ ] Ran full test suite (all passing)
- [ ] Pushed changes to PR branch
- [ ] Responded to review comments
- [ ] Requested re-review if needed

## Notes

- Always use `--force-with-lease` instead of `--force` when force-pushing
- Keep commits focused - one fix per commit when possible
- Run tests before pushing to avoid breaking the build
- Be respectful and professional in comment responses
- If unsure about a comment, ask the user for clarification
