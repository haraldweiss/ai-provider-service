# Phase 1 Execution Checklist

**Status:** Ready to begin  
**Target Repos:** Bewerbungstracker (Python+TypeScript) + wolfini_de_web (PHP) in parallel  
**Estimated Duration:** 15-20 hours total  
**Timeline:** 8-10 calendar days with daily progress tracking  

---

## 📋 Quick Reference: Documentation Map

| Document | Purpose | When to Use |
|----------|---------|-----------|
| **[MIGRATION.md](MIGRATION.md)** | Complete step-by-step guide + architecture overview | Start here for understanding |
| **[INTEGRATION_TEMPLATES.md](INTEGRATION_TEMPLATES.md)** | Python + TypeScript copy-paste templates | When refactoring Bewerbungstracker |
| **[INTEGRATION_TEMPLATES_PHP.md](INTEGRATION_TEMPLATES_PHP.md)** | PHP copy-paste template + examples | When refactoring wolfini_de_web |
| **[ROLLOUT_PLAN.md](ROLLOUT_PLAN.md)** | Phase-by-phase timeline + milestones | For project tracking + detailed steps |
| **[MULTI_REPO_INTEGRATION_INDEX.md](MULTI_REPO_INTEGRATION_INDEX.md)** | Master index + role-based workflows | For understanding the full landscape |
| **verify_integration.py** | Validation script (run in each repo) | After setup to confirm integration |

---

## 🚀 Phase 1a: Planning & Setup (Today)

### Step 1: Verify Service is Running
```bash
# In ai-provider-service directory:
STARTUP_MODE=lazy python app.py &

# Verify it's up (in another terminal):
curl http://localhost:8767/health
# Should return: {"status": "ok"}
```

### Step 2: Verify Integration Script Works
```bash
# Test against the running service:
AI_PROVIDER_SERVICE_URL=http://localhost:8767 \
AI_PROVIDER_SERVICE_TOKEN=test-token \
python verify_integration.py
```

### Step 3: Set Up Parallel Work Tracking
Create a simple progress file:
```bash
# In ai-provider-service:
cat > PHASE_1_PROGRESS.md << 'EOF'
# Phase 1 Progress Tracking

## Bewerbungstracker (Python+TypeScript)
- [ ] Day 1-2: Backend Python refactor (claude_integration.py, routing_service.py, jobs_cron.py)
- [ ] Day 2-3: Frontend TypeScript migration (claudeModelRouter.ts, batchProcessor.ts)
- [ ] Day 3: Environment setup + dependencies cleanup
- [ ] Day 4: Testing (unit + integration)
- [ ] Day 5: Code review + merge
- [ ] Day 5: Staging deployment + monitoring

## wolfini_de_web (PHP)
- [ ] Day 1-2: Backend PHP refactor (CategoryService + post categorization)
- [ ] Day 2: Environment setup + composer.json migration
- [ ] Day 3: Integration testing
- [ ] Day 4: Staging deployment + verification
- [ ] Day 5: Production deployment

## Shared Tasks
- [ ] Daily: Run verify_integration.py in both repos
- [ ] Daily: Check service logs for errors
- [ ] Daily: Document blockers and resolutions

## Success Metrics
- [ ] Both repos successfully connect to centralized service
- [ ] Model loading happens on-demand (verified via service logs)
- [ ] Per-user budgets still enforced (Bewerbungstracker)
- [ ] Post categorization still works (wolfini_de_web)
- [ ] All tests passing
- [ ] Zero regressions in existing functionality
EOF
```

---

## 🔨 Phase 1b & 1c: Implementation (Days 1-4)

### For Bewerbungstracker (Python+TypeScript Backend)

**Copy these templates:**
1. Python client from [INTEGRATION_TEMPLATES.md](INTEGRATION_TEMPLATES.md) → `backend/lib/ai_client.py`
2. TypeScript client from [INTEGRATION_TEMPLATES.md](INTEGRATION_TEMPLATES.md) → `frontend/lib/aiProviderClient.ts`

**Files to refactor (in order):**
1. `backend/claude_integration.py`
   - Remove: `from anthropic import Anthropic`
   - Add: `from lib.ai_client import AIProviderClient`
   - Replace: `client.messages.create()` → `client.chat()`
   - Update: Response format mapping (see MIGRATION.md)

2. `backend/routing_service.py`
   - Update model selection logic to query `/models/status`
   - Keep routing algorithm, just use new client

3. `backend/jobs_cron.py`
   - Use `AIProviderClient` instead of direct Anthropic calls
   - Pre-warm models with `client.load_model()`

4. `backend/api/jobs_user.py`
   - Update endpoint responses to handle new dict-based format

5. `frontend/lib/claudeModelRouter.ts` + `frontend/lib/batchProcessor.ts`
   - Replace direct SDK calls with REST API calls
   - See INTEGRATION_TEMPLATES.md TypeScript examples

**Environment:**
```bash
# Remove from .env:
ANTHROPIC_API_KEY

# Add to .env:
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
```

**Dependencies:**
```bash
# Python:
pip uninstall anthropic openai
pip install requests

# JavaScript:
npm uninstall @anthropic-ai/sdk openai
```

---

### For wolfini_de_web (PHP Backend)

**Copy this template:**
PHP client from [INTEGRATION_TEMPLATES_PHP.md](INTEGRATION_TEMPLATES_PHP.md) → `app/Services/AIProviderClient.php`

**Files to refactor:**
1. `app/Services/CategoryService.php`
   - Remove: Direct Anthropic API calls
   - Add: Use `AIProviderClient::chat()`
   - Update: Response parsing (dict keys instead of object properties)

2. `.env` + `.env.example`
   - Remove: `ANTHROPIC_API_KEY`
   - Add: `AI_PROVIDER_SERVICE_URL=http://localhost:8767`
   - Add: `AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>`

3. `composer.json`
   - Remove: `"anthropic-ai/anthropic-sdk"` dependency
   - Run: `composer update`

**Quick test:**
```bash
# Test a single post categorization:
php artisan tinker
$client = new \App\Services\AIProviderClient();
$result = $client->chat(
    messages: [["role" => "user", "content" => "Test"]],
    model: "claude-3-5-haiku-20241022"
);
dd($result);
```

---

## ✅ Phase 1d & 1e: Testing & QA (Days 3-4)

### In Each Repo:

**Step 1: Run Integration Verification**
```bash
AI_PROVIDER_SERVICE_URL=http://localhost:8767 \
AI_PROVIDER_SERVICE_TOKEN=test-token \
python /path/to/ai-provider-service/verify_integration.py
```

**Step 2: Unit Tests**
```bash
# Bewerbungstracker (Python):
pytest tests/ -v -k "claude or routing or jobs"

# wolfini_de_web (PHP):
php artisan test --filter=CategoryService
```

**Step 3: Integration Tests**
- **Bewerbungstracker:**
  - Test `/api/claude/analyze-job` endpoint
  - Test job cron run
  - Check cost tracking
  - Verify per-user budgets enforced

- **wolfini_de_web:**
  - Test post categorization flow
  - Verify category accuracy
  - Check performance

**Step 4: Manual Testing**
- Create test job/post through UI
- Verify AI response appears
- Check service logs for model loading

**Step 5: Load Testing** (optional)
```bash
# Use: Apache Bench, wrk, or similar
```

---

## 🚀 Phase 1f & 1g: Review, Merge & Deploy (Days 5+)

### Code Review Checklist
- [ ] All imports updated (no direct SDK usage)
- [ ] Error handling covers service downtime
- [ ] Response format correctly mapped
- [ ] Environment variables documented
- [ ] Dependencies cleaned up
- [ ] Tests passing (unit + integration)
- [ ] No regressions vs. pre-migration

### Deployment Steps

**Staging (Day 5 morning):**
```bash
git pull
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://localhost:8767/models/status
pytest tests/ -v --tb=short
# Deploy to staging
python verify_integration.py
# Manual QA testing
```

**Production (Day 5 afternoon):**
```bash
# Same as staging, but production environment
# Monitor logs for first hour after deployment
curl http://localhost:8767/health  # Continuous monitoring
```

---

## 📊 Daily Standup Checklist

```
Daily Standup - Phase 1 - [Date]

✓ Service healthy (curl /health)
✓ No errors in logs overnight
✓ Today's target: [specific task]
✓ Blockers: [none / list any]
✓ Completion %: [e.g., 30%]

Bewerbungstracker progress:
  Backend: [% complete]
  Frontend: [% complete]
  Testing: [% complete]

wolfini_de_web progress:
  Backend: [% complete]
  Testing: [% complete]
```

---

## 🆘 Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| "401 Unauthorized" | Verify `AI_PROVIDER_SERVICE_TOKEN` matches service token |
| Service won't start | `lsof -i :8767` → port in use? Try `PORT=8768` |
| Response format mismatch | See MIGRATION.md (Response Format Adapter Pattern) |
| Model not found | Run `curl .../models/status` → check available models |
| Import errors | Run `python verify_integration.py` → diagnostics |
| Tests fail with 401 | Ensure env vars set in test environment |

See MIGRATION.md for complete FAQ.

---

## 🎯 Success Criteria - End of Phase 1

- [ ] Bewerbungstracker
  - Backend fully migrated + tested
  - Frontend fully migrated + tested
  - Per-user budgets still enforced
  - Cost tracking accurate
  - Zero regressions

- [ ] wolfini_de_web
  - Post categorization fully migrated + tested
  - Categorization accuracy maintained
  - Performance acceptable

- [ ] Both Repos
  - All tests passing (unit + integration + manual)
  - `verify_integration.py` passes completely
  - Deployed to staging + production
  - No errors in 24-hour monitoring window
  - Team trained on new approach

---

## 📞 When to Check Documentation

| Question | Document |
|----------|----------|
| "How do I start?" | This file (PHASE_1_EXECUTION_CHECKLIST.md) |
| "What's the full migration process?" | MIGRATION.md |
| "Show me code examples for my language" | INTEGRATION_TEMPLATES.md or INTEGRATION_TEMPLATES_PHP.md |
| "What's the timeline for all phases?" | ROLLOUT_PLAN.md |
| "How do I verify my setup?" | Run verify_integration.py |
| "What's the big picture?" | MULTI_REPO_INTEGRATION_INDEX.md |

---

## ✨ You're Ready

Everything is documented. The service is built. Templates are ready.

**Start with:**
1. ✅ Service running: `STARTUP_MODE=lazy python app.py`
2. ✅ Verify it works: `python verify_integration.py`
3. ✅ Pick a repo: Bewerbungstracker (more complex) or wolfini_de_web (faster win)
4. ✅ Copy template: Use INTEGRATION_TEMPLATES.md or INTEGRATION_TEMPLATES_PHP.md
5. ✅ Refactor: Follow the file-by-file steps above
6. ✅ Test: Run unit tests + integration tests
7. ✅ Deploy: Staging → Production

**Estimated Time: 15-20 hours over 8-10 calendar days**

Good luck! 🚀
