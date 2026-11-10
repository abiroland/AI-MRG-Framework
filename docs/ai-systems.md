# Generative and agentic AI (the AI-system track)

SR 26-2, the 2026 interagency model risk guidance, explicitly places generative and
agentic AI **outside its scope** and tells institutions to cover such tools through their
own risk management and governance. AI-MRG routes these systems to the **AI-system
track** at classification time and governs them with controls built on the NIST AI Risk
Management Framework and its Generative AI Profile (NIST AI 600-1).

```bash
aimrg register "Customer Service Assistant" --owner cx --use customer_service \
  --category agentic --vendor "Example LLM Provider"
aimrg classify "Customer Service Assistant" --not-model --not-consequential --third-party \
  --jurisdiction US --rationale "Agentic LLM application"
aimrg plan "Customer Service Assistant"
```

When such a system touches a credit decision (for example, drafting adverse-action
letters), ECOA/Regulation B and fair-lending controls still apply (FIN-AIS-006).

## Controls

| Control | What it asks for |
|---|---|
| FIN-AIS-001 | Use-case risk assessment, including GenAI-specific risks |
| FIN-AIS-002 | Human-oversight design |
| FIN-AIS-003 | Pre-deployment evaluation: `genai.evaluation_suite`, `genai.red_team_log` |
| FIN-AIS-004 | Agent permissions and action logging: `genai.agent_permission_audit` (agentic only) |
| FIN-AIS-005 | Incident tracking (record incidents as findings against this control) |
| FIN-AIS-006 | AI-assisted credit communications keep consumer-protection duties |

## Checks assess your evaluations; they don't run them

AI-MRG does not call models. Your teams run evaluations, red-teaming and agent logging
with the tools they prefer; AI-MRG assesses the **results** consistently, against
thresholds, and records them as evidence.

### `genai.evaluation_suite`

Input: an `evaluations` table, one row per test case, with `category` and `passed` (1/0).

```bash
aimrg check run "Customer Service Assistant" FIN-AIS-003 \
  --data evaluations=evaluations.csv --data red_team=red_team.csv
```

- Pass rate per category, judged against `genai_pass_rate` or a category-specific
  threshold such as `genai_pass_rate.prompt_injection` (stricter by default).
- FIN-AIS-003 requires the categories `groundedness`, `harmful_content`,
  `privacy_leakage` and `prompt_injection`; a missing category fails the check.
- Fewer than `min_cases` (default 30) cases in a category produces a warning.

Default categories and the NIST AI 600-1 risks they mainly address:

| Category | NIST AI 600-1 risk |
|---|---|
| `groundedness`, `accuracy` | Confabulation |
| `harmful_content` | Dangerous, Violent, or Hateful Content |
| `privacy_leakage` | Data Privacy |
| `prompt_injection` | Information Security |
| `bias` | Harmful Bias and Homogenization |

### `genai.red_team_log`

Input: a `red_team` table with `severity` (low/medium/high/critical) and `status`
(open/fixed/accepted). Open high or critical findings fail; open medium findings warn; an
empty log warns (confirm red-teaming actually happened).

### `genai.agent_permission_audit`

Input: an `agent_actions` log with `tool` and, for tools that need sign-off,
`human_approved` (1/0). Parameters: `allowed_tools` and `human_approval_tools`.

```bash
aimrg check run "Customer Service Assistant" FIN-AIS-004 \
  --data agent_actions=agent_actions.csv \
  --param allowed_tools=kb_search,account_lookup,create_ticket,issue_refund \
  --param human_approval_tools=issue_refund
```

Fails on any use of a tool outside the allowed list, or any approval-required tool used
without approval. Notes permissions that were granted but never used.

## The AI-system governance record

```bash
aimrg report render ai_system_record "Customer Service Assistant" -o assistant.md
```

Covers use case and context, a NIST AI 600-1 risk profile (all twelve risks, for the
assessor to complete), human oversight, evaluation evidence, incidents, required controls
and the decision.
