Understanding
- You want the planner/query flow to behave like an IDE: INFO requests should immediately summarize the project and how to start it (no “which file/module?” loop), EDIT should enter the pending‑patch workflow, COMMAND must require explicit confirmation, and MCP should be ungated.
- You want KiloCode’s “good parts” integrated where feasible (mode/tool policy, small explicit protocol, session config), but without a full architectural rewrite yet.

Discrepancies / Conflicts vs current repo
- /query currently only returns plan/questions; it does not return an INFO answer. This conflicts with fix4’s required INFO pipeline output.
- Intent routing misses “summarise/summarising” and “how it starts”, which causes INFO to be misclassified as AMBIGUOUS and triggers the “explanation or code change?” loop.
- Planner FSM has no INFO execution path; it only returns a plan. fix4 expects actual summary + start commands.
- MCP is currently ungated (already OK), COMMAND gating exists, and EDIT pipeline exists. These align with fix4 but need tweaks.
- fix4 mentions “make changes in small commits”; this environment does not require commits unless you want them.

What needs to change
- Expand intent routing patterns to capture INFO requests like “summarise/summarizing” and “how it starts”.
- Add an INFO execution path (server-side) that gathers README/scripts/configs and returns a structured summary + start commands.
- Update /query response schema to optionally include “answer” for INFO (while keeping backward compatibility for plan/questions).
- Add/adjust tests for intent routing and INFO pipeline behavior.

Plan
1. Update intent router rules to correctly classify INFO for “summarise/summarizing” and “how it starts” variants, and ensure INFO never falls into AMBIGUOUS.
2. Implement an INFO pipeline in server/app.py that:
   - ensures repo_root is set
   - ensures repo map/index is available
   - inspects README*, package.json scripts, pyproject.toml, requirements.txt, Makefile, docker-compose.yml, .env.example, vite/next configs
   - returns a structured answer: project summary, start commands, prerequisites/ports
3. Extend /query response to include `answer` for INFO, without breaking existing clients.
4. Add tests for planner routing + INFO answer (no “which file/module?” loop).

