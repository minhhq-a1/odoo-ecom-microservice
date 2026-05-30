# Contributing Guide
## Git Workflow & Code Shipping Process

> **Mục đích:** Đảm bảo code quality, traceability, và collaboration hiệu quả trong team.

---

## 🔀 Git Workflow

### Branch Strategy

```
main (protected)
  ├── feat/feature-name
  ├── fix/bug-description
  ├── docs/documentation-update
  ├── refactor/code-improvement
  └── chore/maintenance-task
```

**Branch Naming Convention:**
```
<type>/<short-description>

Types:
- feat/     : New feature
- fix/      : Bug fix
- docs/     : Documentation only
- refactor/ : Code refactoring (no behavior change)
- test/     : Adding or updating tests
- chore/    : Maintenance (deps, config, tooling)
- perf/     : Performance improvement
- ci/       : CI/CD changes
```

**Examples:**
- `feat/shopee-price-sync`
- `fix/redis-connection-timeout`
- `docs/add-reconciliation-guide`
- `refactor/extract-odoo-client`

---

## 📝 Code Shipping Process (MANDATORY)

### Step 1: Create Feature Branch

```bash
# ALWAYS start from latest main
git checkout main
git pull origin main

# Create feature branch
git checkout -b <type>/<description>
```

**❌ NEVER:**
- Work directly on `main` branch
- Push directly to `main` branch
- Force push to `main` branch

### Step 2: Make Changes & Commit

```bash
# Make your changes
# ...

# Stage changes
git add <files>

# Commit with Conventional Commits format
git commit -m "<type>(<scope>): <description>

<optional body>

<optional footer>"
```

**Commit Message Format:**
```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Type:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code refactoring
- `test`: Tests
- `chore`: Maintenance
- `perf`: Performance
- `ci`: CI/CD
- `build`: Build system
- `revert`: Revert previous commit

**Scope:** Module/component affected (e.g., `api`, `workers`, `odoo`, `shopee`, `docs`)

**Examples:**
```bash
git commit -m "feat(shopee): add price sync worker"

git commit -m "fix(odoo): handle connection timeout in XML-RPC client

- Add retry logic with exponential backoff
- Log connection errors with context
- Update circuit breaker threshold"

git commit -m "docs(deployment): add blue-green deployment guide"
```

**Pre-commit Hooks:**
Pre-commit hooks will automatically run:
- `ruff` (linting)
- `ruff format` (formatting)
- `mypy` (type checking)
- `detect-secrets` (secret scanning)
- Trailing whitespace check
- YAML validation

If hooks fail, fix the issues and commit again.

### Step 3: Push Feature Branch

```bash
# Push to remote
git push -u origin <branch-name>
```

**❌ NEVER:**
```bash
git push origin main  # ❌ NO! Never push directly to main
```

### Step 4: Create Pull Request

```bash
# Using GitHub CLI (recommended)
gh pr create \
  --base main \
  --head <branch-name> \
  --title "<type>(<scope>): <description>" \
  --body "<PR description>"

# Or use the GitHub web UI
```

**PR Title:** Must follow Conventional Commits format
```
<type>(<scope>): <description>

Examples:
- feat(shopee): add price sync worker
- fix(api): handle webhook replay attacks
- docs(deployment): add security hardening guide
```

**PR Description Template:**
```markdown
## Description

<Concise summary of what changed and why>

## Related Issue

Closes #<issue-number>
<!-- or: Part of #<issue-number> -->
<!-- or: N/A if no issue -->

## Potential Risk & Impact

<List risks, performance implications, technical debt>
<!-- Use "N/A" only if truly no risk -->

## How Has This Been Tested?

<Describe testing performed: unit tests, manual testing, typecheck, lint>

## Checklist

- [ ] Code follows project conventions (see CLAUDE.md)
- [ ] Pre-commit hooks passed
- [ ] Tests added/updated (if applicable)
- [ ] Documentation updated (if applicable)
- [ ] No secrets or sensitive data committed
```

### Step 5: Wait for CI & Review

**CI Checks (must pass):**
- ✅ Linting (ruff)
- ✅ Type checking (mypy)
- ✅ Unit tests (pytest)
- ✅ Security scan (detect-secrets)
- ✅ Build (Docker image)

**Code Review:**
- At least 1 approval required
- Address review comments
- Push additional commits to the same branch (no force push unless requested)

### Step 6: Merge PR

**After approval + CI pass:**

```bash
# Merge via GitHub UI (preferred)
# - Use "Squash and merge" for clean history
# - Or "Merge commit" if preserving commit history is important

# Delete branch after merge (automatic or manual)
git branch -d <branch-name>
git push origin --delete <branch-name>
```

---

## 🔄 Common Workflows

### Updating Your Branch with Latest Main

```bash
# Option 1: Rebase (cleaner history)
git checkout main
git pull origin main
git checkout <your-branch>
git rebase main

# If conflicts, resolve and continue
git rebase --continue

# Force push (safe for feature branches)
git push --force-with-lease

# Option 2: Merge (preserves history)
git checkout <your-branch>
git merge main
git push
```

### Fixing Mistakes

**Uncommitted changes:**
```bash
# Discard all changes
git restore .

# Discard specific file
git restore <file>
```

**Last commit (not pushed yet):**
```bash
# Amend commit message
git commit --amend -m "new message"

# Add forgotten files to last commit
git add <file>
git commit --amend --no-edit
```

**Already pushed to feature branch:**
```bash
# Reset to previous commit (keep changes)
git reset --soft HEAD~1

# Make corrections
git add <files>
git commit -m "corrected message"

# Force push (safe for feature branches)
git push --force-with-lease
```

**Accidentally pushed to main:**
```bash
# 1. Reset local main
git checkout main
git reset --hard HEAD~1

# 2. Force push to reset remote (DANGEROUS - coordinate with team)
git push origin main --force

# 3. Create proper feature branch
git checkout -b fix/<description>
git cherry-pick <commit-hash>
git push -u origin fix/<description>

# 4. Create PR
gh pr create --base main --head fix/<description>
```

---

## 🚫 What NOT to Do

### ❌ Never Push Directly to Main

```bash
# ❌ WRONG
git checkout main
git add .
git commit -m "fix something"
git push origin main  # ❌ NO!

# ✅ CORRECT
git checkout -b fix/something
git add .
git commit -m "fix(scope): something"
git push -u origin fix/something
gh pr create --base main
```

### ❌ Never Force Push to Main

```bash
# ❌ WRONG
git push origin main --force  # ❌ NEVER!

# ✅ CORRECT
# If you made a mistake on main, revert instead
git revert <commit-hash>
git push origin main
```

### ❌ Never Commit Secrets

```bash
# Pre-commit hook will catch this, but be careful:
# - API keys, passwords, tokens
# - .env files with real credentials
# - Private keys, certificates
# - Database connection strings with passwords

# Use placeholders in committed files:
SECRET_KEY=actual-secret-value-here  # ❌ Real secret
SECRET_KEY=your-secret-here  # ✅ Placeholder
SECRET_KEY=${SECRET_KEY}  # ✅ Environment variable
```

### ❌ Never Skip Pre-commit Hooks

```bash
# ❌ WRONG
git commit --no-verify  # ❌ Bypasses safety checks

# ✅ CORRECT
# Fix the issues flagged by hooks
git commit  # Let hooks run
```

---

## 🔍 Code Review Guidelines

### For Authors

**Before requesting review:**
- [ ] Self-review your own diff
- [ ] All CI checks pass
- [ ] PR description is complete
- [ ] No debug code, console.logs, or commented code
- [ ] No secrets or sensitive data

**During review:**
- Respond to all comments
- Ask for clarification if feedback is unclear
- Push fixes as new commits (don't force push unless requested)
- Mark conversations as resolved after addressing

### For Reviewers

**What to check:**
- [ ] Code follows project conventions (CLAUDE.md)
- [ ] Logic is correct and handles edge cases
- [ ] Error handling is appropriate
- [ ] No security vulnerabilities
- [ ] Performance implications considered
- [ ] Tests cover new/changed code
- [ ] Documentation updated if needed

**Review etiquette:**
- Be constructive and specific
- Explain the "why" behind suggestions
- Distinguish between blocking issues and nits
- Approve when ready, request changes if needed

---

## 📚 References

- **Project Context:** `CLAUDE.md`
- **Architecture:** `docs/02_ARCHITECTURE.md`
- **Testing:** `docs/09_TESTING.md`
- **Deployment:** `docs/15_DEPLOYMENT.md`
- **Conventional Commits:** https://www.conventionalcommits.org/

---

## ❓ FAQ

**Q: Tôi đã push nhầm lên main, làm sao?**
A: Xem section "Accidentally pushed to main" ở trên. Reset local, force push để revert, rồi tạo feature branch + PR đúng quy trình.

**Q: PR của tôi conflict với main, xử lý thế nào?**
A: Rebase hoặc merge main vào branch của bạn, resolve conflicts, rồi push lại.

**Q: Khi nào dùng "Squash and merge" vs "Merge commit"?**
A: Default dùng "Squash and merge" để giữ history sạch. Dùng "Merge commit" nếu muốn preserve tất cả commits trong PR.

**Q: Tôi có thể force push lên feature branch không?**
A: Có, nhưng dùng `--force-with-lease` để an toàn hơn. Không bao giờ force push lên main.

**Q: Pre-commit hook chạy lâu quá, có thể skip không?**
A: Không. Hooks đảm bảo code quality. Nếu quá chậm, báo team để optimize hooks.
