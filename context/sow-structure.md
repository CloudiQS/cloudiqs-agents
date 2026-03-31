# SOW Structure Reference

Template file: `context/templates/CloudiQS_SOW_Template.docx`
Architecture diagrams: `context/sow-architectures.md`

The `ace-sow` agent uses this file to select the correct sections based on the deal campaign field, then generates a populated SOW draft for human review.

---

## Standard sections (all service types)

### 1. Document Control
| Field | Value |
|-------|-------|
| Version | 1.0 |
| Date | {TODAY} |
| Prepared by | CloudiQS Ltd |
| Reviewed by | Sita (Solutions Architect) |
| Status | DRAFT — not for customer distribution |

### 2. Company Introduction
CloudiQS is an AWS Advanced Consulting Partner based in Harpenden, UK. We hold AWS competencies in GenAI, Migration, Microsoft Workloads, and Education, and are recognised as the Fastest Growing AWS Advanced Partner in the UK. Our team of 18 technical staff, the majority ex-AWS, delivers cloud migrations, managed services, AI agent systems, and security assessments for UK mid-market companies.

### 3. Executive Summary
Agent populates from: deal `recommended_play`, `pain_summary`, ACE opportunity summary.
Format: why this project exists, what CloudiQS will deliver, and the expected business outcome. 3–5 sentences. No jargon.

### 4. Customer Requirements
Agent populates from: HubSpot `pain_summary`, `signal`, company research.
List the customer's stated and inferred requirements as bullet points. Include compliance requirements if present (SOC2, FCA, ISO27001).

### 5. Scope of Work
See service-specific sections below.

---

## Service-specific scope sections

### GenAI / Agentic Bakery (`campaign: agentbakery`)

**5.1 AI Use Case Assessment**
Identify the top 3 automation or AI candidates within the customer's operations. Define success criteria for each. Recommend build order based on ROI and data readiness.

**5.2 Data Readiness Review**
Audit available data sources (databases, documents, APIs, event streams). Assess data quality, access controls, and PII classification. Identify gaps that must be resolved before agent deployment.

**5.3 Agent Architecture Design**
Design agent system using Amazon Bedrock, AWS AgentCore, and Strands framework. Define agent roles, tool integrations, memory and knowledge graph requirements, and event bus topology.
*[TBC — Sita to validate architecture for this customer]*

**5.4 Agent Development and Testing**
Build, test, and iterate agents in a sandbox environment. Acceptance testing against defined success criteria. Load and regression testing before production promotion.

**5.5 Production Deployment**
Deploy via CDK into the customer's AWS account. Configure IAM roles, CloudWatch alarms, and cost budgets. Handover runbook and on-call escalation path.

**5.6 Knowledge Transfer and Documentation**
Agent SOUL.md documentation for each agent. Architecture decision records. Runbook for adding new agents. 2-hour walkthrough session with customer technical team.

---

### Migration — VMware / Greenfield / Storage (`campaign: vmware | greenfield | storage | msp`)

**5.1 Current State Assessment**
Infrastructure audit: server inventory, dependencies, network topology, storage volumes, licensing. Identify lift-and-shift vs replatform vs refactor candidates per workload.

**5.2 Target Architecture Design**
AWS Well-Architected target state. Right-sizing recommendations per workload. Network design (VPC, subnets, Transit Gateway if multi-account). Security and IAM design.
*[TBC — Sita to produce architecture diagram]*

**5.3 Migration Planning**
Wave plan: sequence of workloads with dependencies mapped. Rollback strategy per wave. Cutover window selection. Communication plan for affected users.

**5.4 Migration Execution**
Rehost / replatform / refactor execution per agreed wave plan. AWS MGN or native tooling as appropriate. Daily status updates to customer project lead.

**5.5 Testing and Validation**
Functional testing: application smoke tests post-migration. Performance testing: baseline vs post-migration comparison. Security testing: IAM, network ACL, encryption at rest and in transit.

**5.6 Cutover and Go-Live**
Cutover runbook with go/no-go criteria. DNS cutover. Rollback criteria and decision point. Hypercare period: {30 days} of heightened monitoring post-cutover.

**5.7 Post-Migration Support (30-day hypercare)**
Daily check-ins for first week. Weekly reviews for remainder of hypercare. Cost optimisation recommendations at Day 30.

---

### Managed Services — MSP (`campaign: msp`)

**5.1 Onboarding and Assessment**
AWS account audit: IAM, security posture, cost baseline, tagging compliance. Establish monitoring baseline. Define escalation matrix and on-call contacts.

**5.2 Monitoring and Alerting Setup**
CloudWatch dashboards, alarms, and log aggregation. Security Hub, GuardDuty, and Config rules. PagerDuty / Teams integration for alert routing.

**5.3 Ongoing Operations**
24/7 infrastructure monitoring. Incident response per agreed SLA. Monthly patch management window. Change management process.

**5.4 Cost Optimisation**
Monthly AWS cost review. Reserved Instance and Savings Plan recommendations. Right-sizing analysis quarterly. Waste identification (unattached EBS, unused EIPs, idle resources).

**5.5 Security Management**
Monthly GuardDuty and Security Hub findings review. Vulnerability scanning. IAM access review quarterly. Compliance monitoring against agreed framework.

**5.6 Monthly Reporting**
Executive dashboard: cost trends, incident summary, open recommendations, capacity outlook.

**5.7 Service Level Agreement**
| Priority | Response | Resolution Target |
|----------|----------|------------------|
| P1 — Production down | 15 minutes | 4 hours |
| P2 — Degraded | 1 hour | 8 hours |
| P3 — Non-urgent | 4 hours | 3 business days |

---

### Security Assessment (`campaign: security`)

**5.1 Security Posture Assessment**
IAM review: users, roles, policies, access keys, MFA. Network review: security groups, NACLs, public exposure. Data review: S3 bucket policies, encryption, public access blocks. Logging review: CloudTrail, VPC flow logs, S3 access logs.

**5.2 Compliance Gap Analysis**
Map current state against target framework: SOC2 Type II / ISO27001 / Cyber Essentials Plus / FCA SYSC. Produce gap register with finding severity and effort estimate.

**5.3 Threat Detection Review**
Review 90 days of GuardDuty, Security Hub, and Config findings. Identify patterns and unresolved high/critical findings. Assess incident response readiness.

**5.4 Remediation Roadmap**
Prioritised findings: Critical, High, Medium, Low. Effort estimates per finding. Recommended remediation order. Quick wins (resolvable in < 1 day) highlighted.

**5.5 Implementation Support**
Fix all Critical and High findings within scope. Medium findings addressed where time permits. Provide evidence pack for audit purposes.

---

## Common closing sections (all service types)

### 6. Timeline and Milestones
Agent populates from campaign type and deal size. Use standard timelines from `context/pricing.md`. Add milestone table: Phase | Start | End | Deliverable.

### 7. Team and Responsibilities

**CloudiQS team:**
| Name | Role | Responsibility |
|------|------|---------------|
| Steve | CEO / Account Lead | Commercial, escalation, executive relationship |
| Oliver | Alliance Lead | AWS funding, ACE opportunity management |
| Sita | Solutions Architect | Technical design, architecture review, delivery lead |

**Customer stakeholders:**
| Name | Role |
|------|------|
| {TBC} | Project Sponsor |
| {TBC} | Technical Lead |

### 8. Commercial Terms
Agent populates from pricing in `context/pricing.md`.
- Fixed price or T&M (specify)
- Payment schedule (standard: 50% on SOW sign, 50% on delivery for projects; monthly in advance for MSP)
- Expenses: travel to customer site billed at cost, pre-approved
- Governing law: England and Wales

### 9. Assumptions and Dependencies
Standard assumptions to include:
- Customer provides AWS account access with AdministratorAccess or equivalent
- Customer assigns a named project lead who can make decisions
- Existing infrastructure is documented or time is allocated for discovery
- Any third-party systems (on-prem, SaaS) required for integration are accessible during the project
- [Agent adds deal-specific assumptions here]

### 10. AWS Funding
If MAP, WAR, or POC credits apply, include:
- Programme name and estimated credit value
- CloudiQS handles the application — customer does not need to engage AWS directly
- Credits applied to AWS invoice, not CloudiQS invoice
- Reference ACE opportunity ID: {ACE_OPPORTUNITY_ID}

### 11. Acceptance Criteria
For each major deliverable, define: what done looks like, who signs off, and the process for raising defects. Customer sign-off required within 5 business days of delivery or deliverable is deemed accepted.

---

## Agent instructions

When generating a SOW:
1. Identify the campaign field from HubSpot deal
2. Include standard sections 1–5 (common) + the matching service-specific scope
3. Include closing sections 6–11
4. Replace all `{PLACEHOLDER}` values with data from HubSpot + ACE
5. Mark anything uncertain as `[TBC]`
6. Flag all architecture sections with `*[TBC — Sita to review]*`
7. Never include pricing you are not confident about — use `[TBC — confirm with Steve]`
8. Post to Teams #ace-pipeline with a summary of [TBC] fields requiring human input
