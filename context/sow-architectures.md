# SOW Reference Architectures

The `ace-sow` agent includes the relevant architecture pattern in section 5 of the SOW based on campaign type. All architecture sections are marked `[TBC — Sita to review]`. These are starting points, not final designs.

---

## GenAI / Agentic Bakery

```
Customer Data Sources
(S3, RDS, APIs, SharePoint)
         |
         v
    Amazon Bedrock
  (Nova / Claude model)
         |
    AWS AgentCore
  (agent orchestration)
         |
    Strands Framework
  (tool use, memory, routing)
         |
    +-----------+----------+
    |           |          |
 Knowledge   Tool      Event Bus
  Graph     Integrations  (SNS/SQS)
(Cognee)  (HubSpot,    (agent-to-agent)
          Salesforce,
          internal APIs)
         |
    CloudWatch + X-Ray
    (observability)
```

**Key AWS services:** Bedrock, Lambda, ECS Fargate, S3, Aurora Serverless, SNS, SQS, CloudWatch, X-Ray, Secrets Manager, CDK (IaC)

**IAM pattern:** Least-privilege per agent. Each agent has its own execution role. No shared admin credentials.

**Data residency:** eu-west-1 by default. All model invocations via Bedrock stay within the region.

---

## Migration — VMware Exit

```
On-Premises VMware
(vSphere / ESXi hosts)
         |
   AWS MGN (replication)
         |
         v
    AWS eu-west-1
  +-----------------+
  |   VPC           |
  |  +----------+   |
  |  | Public   |   |  <- ALB, NAT Gateway
  |  | Subnet   |   |
  |  +----------+   |
  |  | Private  |   |  <- EC2 / ECS workloads
  |  | Subnet   |   |
  |  +----------+   |
  |  | Data     |   |  <- RDS, ElastiCache, EFS
  |  | Subnet   |   |
  |  +----------+   |
  +-----------------+
         |
   CloudWatch + Security Hub + GuardDuty
```

**Key AWS services:** MGN, EC2, ECS Fargate, RDS Aurora, EFS, ALB, Route 53, CloudWatch, Security Hub, GuardDuty, Config, Backup, Transit Gateway (multi-site)

**Migration approach:** Rehost via MGN for lift-and-shift. Replatform to ECS for containerisable workloads. Refactor only where business case is clear.

**Cutover:** DNS cutover + 30-day parallel run minimum for critical workloads.

---

## Migration — Storage (NAS to FSx)

```
On-Premises NAS
(Windows File Server / NetApp)
         |
   AWS DataSync Agent
   (on-prem or EC2)
         |
         v
    FSx for Windows
    File Server (eu-west-1)
         |
   Active Directory
   (AWS Managed AD or
    customer on-prem AD via VPN)
         |
   CloudWatch + Backup
   (monitoring + 90-day retention)
```

**Key AWS services:** FSx for Windows, DataSync, AWS Managed AD (or AD Connector), Direct Connect or VPN, CloudWatch, AWS Backup, DFS Namespace (optional)

**Access pattern:** Users connect via DFS namespace — transparent cutover. No client reconfiguration required if DFS is used.

---

## Managed Services (MSP)

```
Customer AWS Account(s)
         |
   CloudiQS Management Plane
   (cross-account IAM roles)
         |
   +-----------+-----------+-----------+
   |           |           |           |
CloudWatch  Security    Config      Cost
Dashboards    Hub      Rules +    Explorer +
+ Alarms   GuardDuty  Conformance  Budgets
                        Packs
         |
   Incident → PagerDuty / Teams alert
   Response → CloudiQS on-call engineer
         |
   Monthly Report → PDF → Customer
```

**Key AWS services:** CloudWatch, Security Hub, GuardDuty, Config, Cost Explorer, Budgets, IAM (cross-account assume role), SSM (patch management), Trusted Advisor

**Access model:** CloudiQS assumes a read/write role in customer account for operations. Customer retains full root access. Quarterly access review.

---

## Security Assessment

```
Customer AWS Account
         |
   CloudiQS Assessment Tools
         |
   +-------+--------+--------+--------+
   |       |        |        |        |
  IAM   Network   Data    Logging  Threat
Review  Review  Review   Review  Detection
(users, (SGs,   (S3,    (Trail,  (GD, SH,
 roles, NACLs, RDS enc, VPC     Config
 keys,  VPC    EBS enc) Logs)   findings)
 MFA)   exposure)
         |
   Gap Register (Critical / High / Medium / Low)
         |
   Remediation Roadmap → CloudiQS fixes Critical + High
         |
   Evidence Pack → Customer audit team
```

**Key AWS services:** IAM Access Analyzer, Security Hub, GuardDuty, Config, CloudTrail, Macie (if PII assessment), Inspector, Trusted Advisor

**Frameworks assessed:** SOC2 Type II, ISO27001, Cyber Essentials Plus, FCA SYSC (on request), NCSC Cyber Assessment Framework

---

## Notes for Sita

- All architectures above are starting patterns. Validate against actual customer requirements before including in final SOW.
- Add customer-specific components (existing VPN, Direct Connect, AD topology, existing RDS instances) discovered during Step 2 data gathering.
- Replace generic service names with specific instance types, storage sizes, and capacity estimates where known.
- If the customer has multi-account (AWS Organizations), adapt the VPC/networking section accordingly.
- Flag any architectural decisions that carry significant cost implications — these need Steve to review before the SOW is sent.
