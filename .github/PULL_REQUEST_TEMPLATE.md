## Pull Request

### Summary
<!-- 1-3 dòng mô tả thay đổi và lý do -->

### Type
- [ ] feat (new functionality)
- [ ] fix (bug fix)
- [ ] perf (performance)
- [ ] refactor (no behavior change)
- [ ] docs
- [ ] test
- [ ] chore (infra/CI)

### Checklist
- [ ] Tests added/updated (unit/integration)
- [ ] `make lint && make type` pass
- [ ] Migration (nếu có DDL) đã chạy `python scripts/check_migration_safety.py`
- [ ] Dry-run impact documented nếu touch Odoo write path
- [ ] Observability — metrics/logs/alerts updated nếu thêm code path mới
- [ ] Doc updated (`docs/*.md` hoặc `CLAUDE.md`)
- [ ] Rollback plan documented dưới đây (nếu user-facing)

### Rollback Plan
<!-- Nếu PR fail trên prod, làm gì để revert? -->

### Test Plan
- [ ] ...

🤖 Generated with [Claude Code](https://claude.com/claude-code)
