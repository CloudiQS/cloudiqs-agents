Create a new OpenClaw SDR agent for CloudiQS Engine.

Before creating any files, read these files to understand the current state:
1. `agents/sdr-vmware/SOUL.md` — canonical SOUL.md pattern
2. `scripts/generate-souls.py` — SDR_HUNT_AGENTS dict structure
3. `scripts/register-cron-jobs.sh` — existing schedule times to avoid clashes
4. `README.md` — SDR Agents table to update

Then ask the user for these five things if not already provided:
1. **Agent ID** — must start with `sdr-`, all lowercase, hyphens only (e.g. `sdr-healthcare`)
2. **Campaign name** — slug matching the agent ID suffix (e.g. `healthcare`)
3. **Target companies** — sector, size, technology signal. Be specific.
4. **Search signals** — 4-6 specific things to search for (job ads, news, tech stack, compliance)
5. **Schedule time** — HH:MM Mon-Fri, check register-cron-jobs.sh for gaps

Once all five are confirmed, create four files:

**File 1:** `agents/sdr-{campaign}/SOUL.md`
Follow sdr-vmware structure exactly: 9-step pipeline, GLOBAL EXCLUSIONS, HARD RULES, ICP section. Email style: no contractions, no em dashes, no "caught my eye".

**File 2:** `scripts/generate-souls.py`
Add new entry to SDR_HUNT_AGENTS dict. Do not change existing entries.

**File 3:** `scripts/register-cron-jobs.sh`
Add cron entry in `# ── SDR Hunt Agents ──` section in schedule order.

**File 4:** `README.md`
Add row to SDR Agents table, update count in heading and intro paragraph.

After creating files, report:
- Files written with line counts
- Next steps: create Instantly campaign, store UUID in Secrets Manager, run register-cron-jobs.sh, test manually

Rules:
- Always read sdr-vmware/SOUL.md before writing — do not write from memory
- Never create without a verified signal strategy (real searchable things)
- Never duplicate an existing campaign slug from campaign.py
- If requested campaign overlaps significantly with existing agent, flag it
