# Multi-Repo Integration Index

Complete guide to all documentation and resources for migrating existing repositories to use the centralized AI provider service.

---

## 📋 Documentation Overview

This directory now contains comprehensive documentation for integrating all your repositories with the centralized AI provider service. Here's what's available and where to start:

### Quick Start Path
1. **Start here:** [MIGRATION.md](MIGRATION.md) — Overview and before/after patterns
2. **Copy templates from:** [INTEGRATION_TEMPLATES.md](INTEGRATION_TEMPLATES.md) — Ready-to-use code
3. **Verify setup with:** `python verify_integration.py` — Verification script
4. **Follow timeline in:** [ROLLOUT_PLAN.md](ROLLOUT_PLAN.md) — Detailed phase-by-phase plan

---

## 📚 Documentation Files

### Core Documentation

#### 1. **MIGRATION.md** — Complete Migration Guide
- **Purpose:** Step-by-step instructions for migrating any repo
- **Length:** ~2,500 lines
- **Includes:**
  - Architecture overview (before/after comparison)
  - Migration benefits (5 key advantages)
  - 8-step migration checklist
  - Before/after code examples for Python and TypeScript
  - Response format adapter patterns
  - Environment configuration guide
  - Testing procedures (4 test levels)
  - Troubleshooting FAQ
  - Example test code for Python and TypeScript
- **Best for:** Learning the full migration process

#### 2. **INTEGRATION_TEMPLATES.md** — Copy-Paste Templates
- **Purpose:** Ready-to-use code snippets for Python and TypeScript
- **Length:** ~1,500 lines
- **Includes:**
  - Complete Python AIProviderClient class (copy-paste ready)
  - Complete TypeScript AIProviderClient class (copy-paste ready)
  - Flask route integration examples
  - Express.js route integration examples
  - Python test template
  - TypeScript test template
  - Environment setup checklist
  - Quick conversion checklist
- **Best for:** Quickly copying code into your repo

#### 3. **ROLLOUT_PLAN.md** — Detailed Implementation Timeline
- **Purpose:** Phase-by-phase plan for integrating all repos
- **Length:** ~1,200 lines
- **Includes:**
  - Executive summary (priority matrix)
  - Phase 0: Service completion (done)
  - Phase 1: Bewerbungstracker migration (detailed 8-day plan)
  - Phase 2: Claude-KI-Usage-Tracker (optional)
  - Phase 3: Future repos
  - Testing strategy across all phases
  - Risk management and mitigation
  - Success criteria
  - Communication plan
  - Appendix with quick reference
- **Best for:** Project managers and team leads

#### 4. **verify_integration.py** — Verification Script
- **Purpose:** Check that your repo's setup is correct
- **Type:** Executable Python script
- **Checks:**
  1. Environment variables set (AI_PROVIDER_SERVICE_URL, AI_PROVIDER_SERVICE_TOKEN)
  2. Service connectivity (/health endpoint)
  3. Authentication (401 detection)
  4. Models endpoint working (/models/status)
  5. Chat endpoint working (/chat)
  6. Python client library importable
- **Usage:** `python verify_integration.py`
- **Output:** Color-coded success/error/warning messages

---

## 🏗️ Architecture Summary

### Before: Monolithic with Embedded SDKs
```
Each Repo                Each Repo
    ↓                        ↓
Direct SDK Calls        Direct SDK Calls
(Anthropic, OpenAI)    (Anthropic, OpenAI)
    ↓                        ↓
Own model loading       Own model loading
+ VRAM management       + VRAM management
(Duplicate, Inefficient)
```

### After: Hub-and-Spoke with Centralized Service
```
Repo A          Repo B          Repo C
(lightweight)   (lightweight)   (lightweight)
    ↓               ↓               ↓
    ← Centralized AI Provider Service →
    (Model lifecycle, VRAM pooling, Dispatch)
         ↓
    Ollama, Claude, OpenAI
    (Shared, Efficient)
```

---

## 🎯 Repos to Migrate

### Phase 1: Bewerbungstracker (PRIORITY #1)
**Status:** Not yet migrated  
**Type:** Python backend + TypeScript frontend  
**Complexity:** High  
**Current Integration Points:**
- `backend/claude_integration.py` — Direct Anthropic SDK
- `backend/routing_service.py` — Haiku/Sonnet/Opus selection
- `backend/jobs_cron.py` — Bulk Claude matching
- `frontend/lib/claudeModelRouter.ts` — Model routing logic
- `frontend/lib/batchProcessor.ts` — Batch API processor
- `database` — Per-user budgets, cost tracking

**Migration Effort:** 
- Backend Python: ~4-5 hours
- Frontend TypeScript: ~3-4 hours
- Testing: ~6-8 hours
- **Total: ~15 hours**

**Files to Create:**
- `backend/lib/ai_client.py` — AIProviderClient (copy from template)
- `frontend/lib/aiProviderClient.ts` — TypeScript wrapper (copy from template)
- `tests/test_ai_integration.py` — Integration tests (copy from template)

**Files to Update:**
- `backend/claude_integration.py`
- `backend/routing_service.py`
- `backend/jobs_cron.py`
- `backend/api/jobs_user.py`
- `frontend/lib/claudeModelRouter.ts`
- `frontend/lib/batchProcessor.ts`
- `.env` and `.env.example`
- `requirements.txt` (remove anthropic, openai)
- `package.json` (remove @anthropic-ai/sdk, openai)
- `README.md` (update setup instructions)

### Phase 2: Claude-KI-Usage-Tracker (PRIORITY #2)
**Status:** Not yet migrated  
**Type:** Node.js extension  
**Complexity:** Low  
**Current Integration:** DOM scraping (no direct API calls)  
**Migration Effort:** 
- No changes required: ~0 hours
- Optional enhancement (central cost tracking): ~2-3 hours
- **Total: 0-3 hours**

**Action:** Document that it remains functional as-is. Add optional enhancement later if value justifies effort.

### Phase 3: Futurepinballweb (PRIORITY #3)
**Status:** Not yet evaluated  
**Type:** TypeScript + Three.js (game engine)  
**Complexity:** N/A  
**Current Integration:** None  
**Migration Effort:** 0 hours (no AI integration)

### Phase 4: Loganonymize (DEFERRED)
**Status:** Not available locally  
**Type:** Unknown  
**Action:** Analyze once repo access available

---

## 🚀 Quick Start for Any Repo

### 1. Understand Current State
```bash
# Check where you call Claude/OpenAI
grep -r "from anthropic\|import Anthropic\|OpenAI" .
grep -r "@anthropic-ai/sdk\|openai" .
```

### 2. Review Documentation
- Read MIGRATION.md (15 minutes)
- Scan INTEGRATION_TEMPLATES.md (10 minutes)
- Review ROLLOUT_PLAN.md (20 minutes)

### 3. Copy Templates
- Copy `AIProviderClient` from INTEGRATION_TEMPLATES.md
- Create `lib/ai_client.py` (Python) or `lib/aiProviderClient.ts` (TypeScript)

### 4. Update Code
- Replace SDK initialization with `AIProviderClient()`
- Replace SDK method calls with `client.chat()`
- Update response access from object attributes to dict keys

### 5. Configure Environment
```bash
# In .env:
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
```

### 6. Clean Dependencies
```bash
# Python:
pip uninstall anthropic openai
# Or update requirements.txt and pip install -r requirements.txt

# JavaScript:
npm uninstall @anthropic-ai/sdk openai
# Or update package.json and npm install
```

### 7. Verify Setup
```bash
python verify_integration.py
```

### 8. Run Tests
```bash
pytest tests/ -v  # Python
npm test          # JavaScript
```

---

## 📊 Key Metrics

### Before Integration
- **Repos:** 4 (each with own AI integration)
- **SDK Imports:** ~8 direct (2 per repo avg)
- **Model Loading:** Distributed, inefficient
- **VRAM Usage:** Duplicated across repos
- **Dependencies:** Heavy (anthropic, openai in each)

### After Integration
- **Repos:** 4 (all using centralized service)
- **SDK Imports:** 0 (only in central service)
- **Model Loading:** Centralized, intelligent LRU eviction
- **VRAM Usage:** Pooled and optimized
- **Dependencies:** Light (only requests in clients)

### Benefits
- **Memory Savings:** ~30-50% reduction (no duplicate models in VRAM)
- **Startup Speed:** Faster (no SDK initialization in each repo)
- **Reliability:** Better (centralized error handling and fallbacks)
- **Maintainability:** Easier (single source of truth for AI logic)
- **Cost:** Optimized (batch API support, LRU eviction)

---

## 🔄 Workflow by Role

### **Developer (Contributing to a Repo)**
1. Read INTEGRATION_TEMPLATES.md for your language
2. Copy relevant template code
3. Update local `.env` with service URL and token
4. Run `python verify_integration.py`
5. Run tests: `pytest` or `npm test`

### **DevOps/Infrastructure**
1. Read ROLLOUT_PLAN.md for timeline and milestones
2. Ensure ai-provider-service is running and accessible
3. Set `SERVICE_TOKEN` in CI/CD or secrets manager
4. Coordinate deployment schedule across repos
5. Monitor service health and VRAM usage

### **Project Manager**
1. Review ROLLOUT_PLAN.md and create project timeline
2. Assign owners to each phase (1-3)
3. Track progress against Success Criteria section
4. Manage risks (see Risk Management section)
5. Coordinate communication with team

### **Architecture/Tech Lead**
1. Review MIGRATION.md and ROLLOUT_PLAN.md
2. Approve approach and timeline
3. Review code changes in each repo PR
4. Ensure integration aligns with standards
5. Plan Phase 3+ enhancements

---

## 🛠️ Maintenance & Troubleshooting

### Common Issues & Solutions

#### Service Won't Start
```bash
# Check if port 8767 is in use:
lsof -i :8767

# Try different port:
PORT=8768 python app.py
# Then update AI_PROVIDER_SERVICE_URL in client repos
```

#### 401 Unauthorized Error
```bash
# Verify token is set in client repo:
echo $AI_PROVIDER_SERVICE_TOKEN

# Check if token matches service's SERVICE_TOKEN:
curl -H "Authorization: Bearer $AI_PROVIDER_SERVICE_TOKEN" \
  http://localhost:8767/models/status
```

#### Model Not Found
```bash
# Check available models:
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8767/models/status | jq '.loaded'

# Verify model name is correct (e.g., 'claude-3-5-haiku-20241022')
```

#### VRAM Exhausted
```bash
# Check current usage:
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8767/models/status | jq '.utilization_pct'

# Unload a model:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8767/models/unload \
  -d '{"model_name": "some-model"}'
```

### Monitoring Checklist
- [ ] Service health: `curl http://localhost:8767/health`
- [ ] VRAM usage: `curl .../models/status | jq '.utilization_pct'`
- [ ] Loaded models: `curl .../models/status | jq '.loaded'`
- [ ] Service logs: Monitor for errors and warnings
- [ ] Client repo tests: Run integration tests weekly

---

## 📅 Timeline Summary

| Week | Phase | Target | Status |
|------|-------|--------|--------|
| Week 0 | Planning | Review docs, plan Phase 1 | ✅ Complete |
| Week 1 | Bewerbungstracker | Migrate backend + frontend, test | In Progress |
| Week 2 | Claude-KI-Tracker | Optional enhancement | Pending |
| Week 3 | Monitoring | Stability check, lessons learned | Pending |
| Week 4+ | Phase 3 | Futurepinballweb if needed | Future |

---

## 🎓 Learning Resources

### Understanding the Architecture
1. Start with ARCHITECTURE.md (high-level overview)
2. Review SETUP.md (how to run the service)
3. Check client_library/README.md (client API reference)

### Understanding Migration
1. Read MIGRATION.md (complete walkthrough)
2. Study INTEGRATION_TEMPLATES.md (code examples)
3. Reference ROLLOUT_PLAN.md (specific steps for your repo)

### Understanding Testing
1. Review test templates in INTEGRATION_TEMPLATES.md
2. Run verify_integration.py to validate setup
3. Use verify_integration.py output as a checklist

---

## ✅ Success Metrics

### Phase 1 Complete When:
- [ ] Bewerbungstracker migrated and deployed
- [ ] All tests passing (unit + integration)
- [ ] No regressions in functionality
- [ ] VRAM usage optimal (<80%)
- [ ] Documentation updated
- [ ] Zero breaking changes to API consumers

### Overall Success When:
- [ ] All 4 repos using centralized service
- [ ] Zero direct SDK imports in client repos
- [ ] Centralized service handling all AI requests
- [ ] Runbook created for team
- [ ] Team trained on new architecture

---

## 📞 Questions & Support

### Where to Find Answers
- **How do I integrate my repo?** → Read MIGRATION.md
- **What code should I copy?** → See INTEGRATION_TEMPLATES.md
- **What's the schedule?** → Check ROLLOUT_PLAN.md
- **Is my setup correct?** → Run verify_integration.py
- **How does it work internally?** → Read ARCHITECTURE.md
- **How do I set up the service?** → See SETUP.md

### Common Questions
**Q: Do I have to migrate?**  
A: Eventually yes, for consistency. Phase 1 is mandatory (Bewerbungstracker), Phase 2+ is recommended.

**Q: Can I use the old SDK and centralized service together?**  
A: Not recommended, but you can during transition. Complete migration recommended.

**Q: What if the centralized service is down?**  
A: Requests fail with clear error message. Implement retry logic in client repos.

**Q: How do I rotate the SERVICE_TOKEN?**  
A: Generate new token on service, update all client repos, redeploy.

---

## 🎯 Next Steps

1. **Review:** Read MIGRATION.md completely (~30 minutes)
2. **Plan:** Create Bewerbungstracker migration plan based on ROLLOUT_PLAN.md
3. **Prepare:** Set up development environment with ai-provider-service running
4. **Execute:** Follow Phase 1 in ROLLOUT_PLAN.md
5. **Verify:** Run verify_integration.py and integration tests
6. **Deploy:** Follow deployment steps in ROLLOUT_PLAN.md
7. **Monitor:** Check success metrics after deployment
8. **Iterate:** Plan Phase 2+ based on Phase 1 learnings

---

## 📄 File Structure

```
ai-provider-service/
├── MIGRATION.md                          ← Start here
├── INTEGRATION_TEMPLATES.md              ← Copy templates from here
├── ROLLOUT_PLAN.md                       ← Phase-by-phase timeline
├── MULTI_REPO_INTEGRATION_INDEX.md       ← You are here
├── verify_integration.py                 ← Run this to verify setup
│
├── client_library/
│   ├── __init__.py
│   ├── python_client.py                  ← Python SDK
│   └── README.md                         ← Client API docs
│
├── docs/
│   ├── ARCHITECTURE.md                   ← Technical deep-dive
│   └── ...
│
├── SETUP.md                              ← Service setup
├── README.md                             ← Project overview
└── ...
```

---

## 🚀 You're Ready!

All documentation is in place. The path forward is clear:

1. ✅ **Architecture planned** — Hub-and-spoke with centralized service
2. ✅ **Service implemented** — All Phases 1-7 complete
3. ✅ **Documentation created** — Complete migration guides
4. ✅ **Templates provided** — Ready-to-copy code
5. ✅ **Timeline defined** — Phase-by-phase rollout plan
6. ⏳ **Execution** — You're ready to begin Phase 1

Start with MIGRATION.md. Ask questions. Execute the rollout plan. Success!
