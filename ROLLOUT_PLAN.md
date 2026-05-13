# Multi-Repo Rollout Plan: Integration Timeline & Checklist

This document outlines the detailed plan for integrating all existing repositories with the centralized AI provider service, including timeline, dependencies, testing strategy, and rollback procedures.

---

## Executive Summary

| Phase | Repos | Timeline | Status |
|-------|-------|----------|--------|
| **Phase 0** | ai-provider-service | ✅ Complete | Core service ready |
| **Phase 1** | Bewerbungstracker | Week 1 | Highest priority |
| **Phase 2** | Claude-KI-Usage-Tracker | Week 2 | Optional, low risk |
| **Phase 3** | Futurepinballweb | Week 3+ | Lowest priority, no AI |
| **Phase 4** | Loganonymize | TBD | Deferred, unavailable |

**Total Estimated Time:** 3-4 weeks (Phase 1-2), with Phase 3+ as optional enhancements.

---

## Phase 0: AI Provider Service (Complete)

**Status:** ✅ Ready

**What's Done:**
- Core model lifecycle management (load/unload/LRU)
- REST API endpoints (/chat, /models/*)
- Python client library (AIProviderClient)
- Lazy initialization with 3 startup modes
- Hardware detection (GPU/RAM/CPU)
- SQLite database with OllamaLoadedModels tracking
- Comprehensive tests (unit + integration)
- Documentation (SETUP.md, ARCHITECTURE.md, client_library/README.md)

**How to Start Using:**
```bash
cd ~/projects/ai-provider-service
STARTUP_MODE=lazy python app.py
# Service runs on http://localhost:8767
# Share SERVICE_TOKEN with all client repos
```

---

## Phase 1: Bewerbungstracker Migration (Priority #1)

**Timeline:** Week 1 (5 business days)  
**Effort:** High (Complex AI integration, model routing, cost tracking)  
**Risk:** High (Active production service, multiple integration points)  
**Rollback:** Medium (Clear git history, feature flags available)

### 1a: Planning & Setup (Day 1, ~2-3 hours)

**Checklist:**
- [ ] Clone Bewerbungstracker if not already local: `git clone <repo-url> ~/projects/bewerbungstracker`
- [ ] Review current Claude integration:
  - [ ] Examine `backend/claude_integration.py` → direct Anthropic SDK usage
  - [ ] Examine `backend/routing_service.py` → model selection logic (Haiku/Sonnet/Opus)
  - [ ] Examine `backend/jobs_cron.py` → bulk Claude matching
  - [ ] Examine `frontend/lib/claudeModelRouter.ts` → TS routing logic
  - [ ] Examine `frontend/lib/batchProcessor.ts` → batch API processor
- [ ] Document current cost tracking approach (ApiCall table, per-user budgets)
- [ ] Create feature branch: `git checkout -b feature/centralized-ai-service`

**Files to Review:**
```
Bewerbungstracker/
├── backend/
│   ├── claude_integration.py     ← REPLACE with AIProviderClient
│   ├── routing_service.py        ← UPDATE to query central service
│   ├── jobs_cron.py              ← UPDATE to use centralized client
│   └── api/
│       └── jobs_user.py          ← UPDATE response mapping
├── frontend/
│   └── lib/
│       ├── claudeModelRouter.ts  ← CREATE TypeScript wrapper
│       └── batchProcessor.ts     ← UPDATE to forward to service
├── .env                          ← UPDATE with AI_PROVIDER_SERVICE_*
└── requirements.txt              ← REMOVE anthropic, openai
```

### 1b: Backend Python Migration (Day 1-2, ~4-5 hours)

**Checklist:**

1. **Update claude_integration.py:**
   - [ ] Copy AIProviderClient template from INTEGRATION_TEMPLATES.md
   - [ ] Create `backend/lib/ai_client.py` with the template
   - [ ] Replace `from anthropic import Anthropic` with `from lib.ai_client import AIProviderClient`
   - [ ] Update `__init__` to use AIProviderClient instead of Anthropic
   - [ ] Update response handling: `response['result']['content'][0]['text']` instead of `response.content[0].text`
   - [ ] Test basic chat endpoint locally

2. **Update routing_service.py:**
   - [ ] Keep existing logic (Haiku for simple, Sonnet for complex, Opus for reasoning)
   - [ ] Add function to query service status: `client.get_status()` to check available VRAM
   - [ ] Update to pass selected model to `client.chat(model=selected_model, ...)`
   - [ ] Add logging for model selection decisions (useful for analysis)

3. **Update jobs_cron.py:**
   - [ ] Replace direct Claude SDK calls with `client.chat()`
   - [ ] For bulk operations, add pre-warming: `client.load_model(model_name)` before loop
   - [ ] Handle response format normalization if needed (see ResponseAdapter in templates)
   - [ ] Test with small dataset first (10-20 items before full bulk)

4. **Update api/jobs_user.py:**
   - [ ] Verify endpoints return correct format with normalized responses
   - [ ] Add error handling for service unavailability
   - [ ] Log any response format mismatches

**Code Review Checklist:**
- [ ] All Anthropic SDK imports removed
- [ ] All `AIProviderClient` instances properly initialized with service_url and token
- [ ] All response formats updated to dict access instead of object attributes
- [ ] Error handling for service connection failures added
- [ ] No hardcoded model names (use config/env vars)

### 1c: Environment & Dependencies (Day 2, ~1-2 hours)

**Checklist:**

1. **Update .env:**
   ```bash
   # Remove:
   # ANTHROPIC_API_KEY=sk-ant-...
   
   # Add:
   AI_PROVIDER_SERVICE_URL=http://localhost:8767
   AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
   ```

2. **Update requirements.txt:**
   ```
   # Remove: anthropic>=0.39.0
   # Remove: openai>=1.54.0
   # Keep: requests>=2.31.0 (used by AIProviderClient)
   ```

3. **Test imports:**
   ```bash
   cd bewerbungstracker
   python -c "from lib.ai_client import AIProviderClient; print('✓ Imports work')"
   ```

### 1d: Frontend TypeScript Migration (Day 2-3, ~3-4 hours)

**Checklist:**

1. **Create TypeScript client wrapper:**
   - [ ] Copy AIProviderClient template from INTEGRATION_TEMPLATES.md
   - [ ] Create `frontend/lib/aiProviderClient.ts`
   - [ ] Ensure types match: ChatMessage, ChatOptions, ChatResponse
   - [ ] Test import: `import { AIProviderClient } from './aiProviderClient'`

2. **Update claudeModelRouter.ts:**
   - [ ] Keep existing routing logic intact
   - [ ] Update to call backend endpoint for model selection (if backend service added this)
   - [ ] Or: use local routing logic but forward to centralized service via backend

3. **Update batchProcessor.ts:**
   - [ ] Change endpoint from Anthropic API to backend API
   - [ ] Verify response format handling
   - [ ] Test batch request functionality

4. **Update package.json:**
   ```
   # Remove: @anthropic-ai/sdk, openai
   # Keep: fetch (built-in) or axios if already using
   ```

### 1e: Testing & QA (Day 3-4, ~6-8 hours)

**Unit Tests:**
```bash
cd backend
pytest tests/ -v
# Verify:
# - ✓ claude_integration tests pass
# - ✓ routing_service tests pass
# - ✓ jobs_cron tests pass
```

**Integration Tests:**
```bash
# Terminal 1: Start ai-provider-service
cd ~/projects/ai-provider-service
STARTUP_MODE=lazy python app.py

# Terminal 2: Run integration tests
cd bewerbungstracker
python tests/test_ai_integration.py
```

**Manual Testing Checklist:**
- [ ] **UI Functionality:**
  - [ ] Can submit a job for analysis → response appears
  - [ ] Model selection UI works (if exposed)
  - [ ] Error messages display correctly when service unavailable

- [ ] **API Testing:**
  ```bash
  # Test basic endpoint
  curl -X POST http://localhost:5000/api/jobs \
    -H "Content-Type: application/json" \
    -d '{"text": "Software engineer job description", "action": "analyze"}'
  
  # Expected: Analysis result from Claude via centralized service
  ```

- [ ] **Cron Jobs:**
  - [ ] Trigger manual job matching: `/api/jobs/match-bulk` or similar
  - [ ] Verify models load in service: check `/models/status` in separate terminal
  - [ ] Verify cost tracking still works
  - [ ] Check per-user budget enforcement (job_daily_budget_cents)

- [ ] **Edge Cases:**
  - [ ] Service unreachable → graceful error message
  - [ ] Token invalid → 401 error handled
  - [ ] Model unavailable → fallback or clear error
  - [ ] VRAM exhausted → log and continue or queue request

**Load Testing (Optional, if time permits):**
```python
# backend/tests/test_load.py
for i in range(20):
    response = client.chat(
        model='claude-3-5-haiku-20241022',
        messages=[{'role': 'user', 'content': f'Job {i}'}]
    )
    print(f"Job {i}: {len(response['result']['content'][0]['text'])} chars")
# Verify service handles parallel requests
```

### 1f: Code Review & Merge (Day 4-5, ~2-3 hours)

**Checklist:**
- [ ] All tests passing (unit + integration)
- [ ] Code review by team member (if available)
- [ ] Documentation updated:
  - [ ] README.md mentions new setup process
  - [ ] .env.example shows AI_PROVIDER_SERVICE_* variables
  - [ ] Any internal docs about Claude integration updated
- [ ] Git commit with clear message: `feat: integrate centralized AI provider service`
- [ ] Create PR for review (if using PR workflow)
- [ ] Merge to main branch

**Before Merging:**
- [ ] Verify no breaking changes to API consumers
- [ ] Ensure backward compatibility where possible
- [ ] Document any migration steps for users/developers

### 1g: Deployment (Day 5, ~1-2 hours)

**Pre-Deployment:**
- [ ] Ensure ai-provider-service is running in staging/prod
- [ ] Set SERVICE_TOKEN environment variable on staging
- [ ] Run migrations if DB schema changed (unlikely, but check)

**Deployment Steps:**
```bash
# On staging server:
cd bewerbungstracker
git pull origin feature/centralized-ai-service
pip install -r requirements.txt  # (no new deps, actually fewer)

# Set env vars (from CI/CD or secrets manager):
export AI_PROVIDER_SERVICE_URL=http://localhost:8767
export AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>

# Run tests:
pytest tests/ -v

# Deploy:
# (whatever your deployment process is - Docker, systemd, etc.)
```

**Post-Deployment Verification:**
- [ ] API endpoints responding
- [ ] Jobs can be analyzed (test endpoint)
- [ ] Cron jobs running
- [ ] Cost tracking working
- [ ] Monitor logs for errors

### 1h: Monitoring & Rollback Plan

**If Issues Occur:**
1. **Immediate Rollback:**
   ```bash
   # Revert to previous commit
   git revert <commit-hash>
   git push origin main
   # Redeploy with old version
   ```

2. **Partial Fallback:**
   - Keep ai-provider-service running (shared resource)
   - Use feature flag to disable centralized service in Bewerbungstracker
   - Fall back to direct SDK calls (if old code still available)

3. **Contact Points:**
   - Check ai-provider-service logs: `tail -f nohup.out`
   - Verify SERVICE_TOKEN is correct
   - Check network connectivity between Bewerbungstracker and service

**Metrics to Monitor (Week 1 after deployment):**
- Job analysis success rate (should be >95%)
- Response time (should be <2 seconds for simple queries)
- VRAM utilization (should be <80% on service)
- Error rate (should be <5%)

---

## Phase 2: Claude-KI-Usage-Tracker Migration (Priority #2)

**Timeline:** Week 2 (3-4 business days)  
**Effort:** Low (No direct API calls, cost aggregation only)  
**Risk:** Low (Non-critical tool, DOM-scraping based)  
**Rollback:** Easy (No API changes, backward compatible)

### 2a: Analysis & Planning (Day 1, ~1-2 hours)

**Checklist:**
- [ ] Review current architecture:
  - [ ] Extension reads Claude interface via DOM
  - [ ] Tracks cost via local storage or background script
  - [ ] No direct Anthropic API calls

**Decision:** This tool doesn't need immediate changes since it doesn't call Claude SDK directly. Options:
1. **No Changes Required:** Keep as-is, it still works (cost tracking via UI)
2. **Enhancement:** Add optional connection to centralized service for central cost tracking
3. **Documentation:** Just note that repos should use centralized service

**Recommendation:** Option 1 (no changes) + Option 3 (document). Add enhancement later if value justifies effort.

### 2b: Optional Enhancement (If time permits, Day 2-3, ~2-3 hours)

**If enhancing for centralized cost tracking:**

1. **Create service integration:**
   - [ ] Add endpoint to report usage: POST `/analytics/usage`
   - [ ] Track which repos made requests, cost per repo
   - [ ] Aggregate across all client repos

2. **Update extension:**
   - [ ] Connect to centralized service for cost reporting
   - [ ] Report: repo_name, model, tokens_used, timestamp
   - [ ] Read aggregated stats from central service

3. **Test:**
   - [ ] Verify cost is tracked correctly
   - [ ] Check Dashboard shows accurate totals

### 2c: Status (Week 2)

**Likely Outcome:** Documentation updated, tool remains functional, optional enhancement deferred.

---

## Phase 3: Other Repos (Priority #3)

### Futurepinballweb
- **Status:** No AI integration
- **Action:** No migration needed
- **Timeline:** Deferred (only if AI features added later)

### Loganonymize
- **Status:** Not available locally for analysis
- **Action:** Deferred pending access
- **Timeline:** TBD once repo is available

---

## Shared Artifact: SERVICE_TOKEN

**Critical:** All client repos use the same SERVICE_TOKEN as ai-provider-service.

**Generation (One Time):**
```bash
# On service startup:
SERVICE_TOKEN=$(openssl rand -hex 32)
echo $SERVICE_TOKEN  # Share with all repos
```

**Distribution:**
```bash
# In each repo's CI/CD or .env:
export AI_PROVIDER_SERVICE_TOKEN=<shared-value>
```

**Rotation (Annual or if compromised):**
1. Generate new token on service
2. Update all client repos
3. Redeploy all services
4. Monitor for connection errors

---

## Testing Strategy Across All Phases

### Unit Tests (Per-Repo)
```bash
cd <repo>
pytest tests/ -v
# or
npm test
```

### Integration Tests (Against Centralized Service)
```bash
# Terminal 1: Start service
cd ~/projects/ai-provider-service
STARTUP_MODE=lazy python app.py

# Terminal 2: Test repo connectivity
cd <repo>
python verify_integration.py
```

### System Tests (End-to-End)
- Submit request from client repo
- Verify model loads in service
- Verify response returns correctly
- Verify cost tracking updated

### Load Test (Optional)
- Simulate 20-50 concurrent requests from single repo
- Verify service doesn't crash
- Monitor VRAM usage

---

## Documentation Requirements

### Updated/Created By End of Rollout:

- [x] MIGRATION.md — How to integrate any repo
- [x] INTEGRATION_TEMPLATES.md — Copy-paste templates
- [x] verify_integration.py — Verification script
- [ ] README.md (ai-provider-service) — Add section on "Integrating Client Repos"
- [ ] SETUP.md — Add "Multi-Repo Setup" section
- [ ] Example PR — Link to Bewerbungstracker PR as reference
- [ ] Runbook — How to debug common issues

---

## Success Criteria

### Phase 1 (Bewerbungstracker)
- [ ] All unit tests passing
- [ ] Integration tests passing
- [ ] No regression in job analysis functionality
- [ ] Cost tracking still accurate
- [ ] Per-user budget enforcement still working
- [ ] Model selection logic working correctly
- [ ] Response times <3 seconds
- [ ] VRAM usage <80%
- [ ] Code review approved
- [ ] Deployed to staging successfully
- [ ] Deployed to production with no errors

### Phase 2 (Claude-KI-Usage-Tracker)
- [ ] Documentation updated
- [ ] Tool still functional
- [ ] No breaking changes

### Phase 3+ (Future)
- [ ] Template available for new repos
- [ ] Integration can be done in <1 hour per repo
- [ ] Centralized service stable under multi-client load

---

## Risk Management

### Risk: Service Downtime
- **Mitigation:** Implement retry logic in client repos (exponential backoff)
- **Fallback:** Queue requests if service unavailable, replay when recovered
- **Monitor:** Set up alerts if service unreachable for >5 minutes

### Risk: VRAM Exhaustion
- **Mitigation:** LRU eviction automatically unloads least-used models
- **Monitoring:** `/models/status` shows utilization_pct
- **Contingency:** Smaller models available as fallback (Haiku instead of Sonnet)

### Risk: Token Compromise
- **Mitigation:** SERVICE_TOKEN stored securely (env vars, not in git)
- **Rotation:** Annual rotation, plus emergency rotation if needed
- **Audit:** Log all requests with timestamp/source IP (future enhancement)

### Risk: Integration Failures
- **Mitigation:** Phased rollout (Bewerbungstracker first, others after validation)
- **Rollback:** Keep old code available, git revert if needed
- **Testing:** Run full integration tests before each deployment

---

## Communication Plan

### Week 0-1 (Before Bewerbungstracker Migration)
- [ ] Share this document with team
- [ ] Review architecture and benefits
- [ ] Assign owner to each phase

### Week 1 (During Bewerbungstracker)
- [ ] Daily standup on progress
- [ ] Notify team of any blockers
- [ ] Share PR for code review

### Week 2+ (After Phase 1)
- [ ] Retrospective on lessons learned
- [ ] Document any issues found
- [ ] Plan Phase 2 if Phase 1 successful

---

## Appendix: Quick Reference

### Start Service (Every Session)
```bash
cd ~/projects/ai-provider-service
STARTUP_MODE=lazy python app.py
```

### Verify Service is Running
```bash
curl http://localhost:8767/health
# Expected: {"status": "ok"}
```

### Check Models Status
```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:8767/models/status | jq .
```

### Verify Repo Integration
```bash
cd <repo>
AI_PROVIDER_SERVICE_URL=http://localhost:8767 \
AI_PROVIDER_SERVICE_TOKEN=test-token \
python verify_integration.py
```

### View Service Logs
```bash
cd ~/projects/ai-provider-service
tail -f app.log  # or check nohup.out if running with nohup
```

---

## Contacts & Escalation

- **AI Provider Service Owner:** Harald (creator)
- **Bewerbungstracker Lead:** [TBD]
- **Architecture Questions:** See docs/ARCHITECTURE.md

---

## Sign-off

- [ ] Plan reviewed by team
- [ ] Risks understood
- [ ] Timeline agreed
- [ ] Resources allocated
- [ ] Ready to begin Phase 1

**Approved:** _______________  
**Date:** _______________
