# Full-Spectrum MCP Tool Demo

This walkthrough exercises every MCP tool method (`describe`, `requirements`, `plan`, `execute`, `validate`) across three different packs:

1. **Growth:** `pack.funnel.activation-trial-to-paid`
2. **Revenue Operations:** `pack.b2b-saas-quote-to-cash`
3. **Reliability / Incidents:** `pack.devops.sre.sev1.triage.escalation`

Follow it end-to-end to both demonstrate and smoke-test the MCP server.

## 0. Prep

```bash
python -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"
python server.py --packs-dir ./packs
```

The commands below use the standalone CLI installed via `pip install "mcp[cli]"`. Each invocation follows the shape:

```bash
mcp call <registered-server-name> <tool-name> [--input @/path/to/context.json]
```

If you’re running inside the Codex TUI, use `/mcp call …` with the same arguments. The legacy `codex mcp` subcommands only register/list servers; they do not execute tools.

## 1. Funnel activation pack

### Describe / Requirements
```bash
mcp call pack-registry pack.funnel.activation-trial-to-paid.describe
mcp call pack-registry pack.funnel.activation-trial-to-paid.requirements
```

### Plan / Execute
```bash
cat <<'EOF' > /tmp/funnel-context.json
{
  "thread_colors": {
    "problem_type": "funnel",
    "business_model": "b2b_saas",
    "sales_motion": "plg",
    "customer_segment": "mid_market",
    "funnel_stage": "trial",
    "acv_range_usd": "10000-50000"
  },
  "product": {
    "name": "AtlasOps",
    "category": "Workflow automation",
    "trial_type": "time_limited",
    "primary_user_role": "implementation manager",
    "primary_buyer_role": "VP Operations",
    "time_to_value_minutes": 45,
    "aha_moment_description": "Automate the first onboarding playbook with live approvals"
  },
  "funnel": {
    "stages": ["visitor", "signup", "activation", "trial", "paid"],
    "current_activation_definition": "User creates first automation",
    "conversion_rates": {
      "visitor_to_signup": 0.07,
      "signup_to_activation": 0.35,
      "activation_to_trial_start": 0.55,
      "trial_start_to_paid": 0.12
    },
    "dropoff_notes": [
      "Procurement delays when SSO required",
      "Admins get stuck at integrations screen"
    ]
  },
  "instrumentation": {
    "analytics_stack": ["Snowflake", "dbt", "Amplitude"],
    "event_tracking_available": true,
    "warehouse_available": true,
    "feature_flags_available": true,
    "ab_testing_available": false,
    "known_event_gaps": ["Missing event for approval routing edits"]
  },
  "constraints": {
    "engineering_bandwidth_weeks": 3,
    "design_bandwidth_weeks": 1,
    "data_team_bandwidth_weeks": 1,
    "compliance_constraints": ["SOX change management"],
    "channels_in_scope": ["in-product guides", "lifecycle email"]
  },
  "signals": {
    "dropoff_detected": true,
    "user_request": "trial-to-paid playbook",
    "seasonality_or_launch_context": "Q3 procurement freeze for key logos"
  }
}
EOF

mcp call pack-registry pack.funnel.activation-trial-to-paid.plan --input @/tmp/funnel-context.json | tee /tmp/funnel-plan.json
mcp call pack-registry pack.funnel.activation-trial-to-paid.execute --input @/tmp/funnel-context.json | tee /tmp/funnel-execute.json
```

### Validate
```bash
cat <<'EOF' > /tmp/funnel-validate.json
{
  "context": $(cat /tmp/funnel-context.json),
  "outputs": $(cat /tmp/funnel-execute.json)
}
EOF
mcp call pack-registry pack.funnel.activation-trial-to-paid.validate --input @/tmp/funnel-validate.json
```

## 2. Quote-to-cash pack

### Describe / Requirements
```bash
mcp call pack-registry pack.b2b-saas-quote-to-cash.describe
mcp call pack-registry pack.b2b-saas-quote-to-cash.requirements
```

### Plan / Execute
```bash
cat <<'EOF' > /tmp/qtc-context.json
{
  "objective": "Cut order-to-cash cycle time from 21 to 10 days while keeping discount guardrails intact.",
  "current_state": "Mix of Salesforce CPQ, manually edited Google Docs order forms, and NetSuite billing. Regional deal desk queues take 3-4 days to respond. Collections is not looped into approvals.",
  "constraints": {
    "time": "6 sprint-weeks with 3 rev-ops FTE",
    "budget": "$15k for tooling changes",
    "compliance": "SOX + ASC606 evidence capture"
  },
  "stakeholders": [
    "VP Sales",
    "Deal Desk Lead",
    "Revenue Accounting Manager",
    "Collections Lead"
  ],
  "environment": {
    "systems": ["Salesforce CPQ", "NetSuite", "Workato", "DocuSign"],
    "data_sources": ["Snowflake ARR mart"]
  }
}
EOF

mcp call pack-registry pack.b2b-saas-quote-to-cash.plan --input @/tmp/qtc-context.json | tee /tmp/qtc-plan.json
mcp call pack-registry pack.b2b-saas-quote-to-cash.execute --input @/tmp/qtc-context.json | tee /tmp/qtc-execute.json
```

### Validate
```bash
cat <<'EOF' > /tmp/qtc-validate.json
{
  "context": $(cat /tmp/qtc-context.json),
  "outputs": $(cat /tmp/qtc-execute.json)
}
EOF
mcp call pack-registry pack.b2b-saas-quote-to-cash.validate --input @/tmp/qtc-validate.json
```

## 3. SEV1 triage & escalation pack

### Describe / Requirements
```bash
mcp call pack-registry pack.devops.sre.sev1.triage.escalation.describe
mcp call pack-registry pack.devops.sre.sev1.triage.escalation.requirements
```

### Plan / Execute
```bash
cat <<'EOF' > /tmp/sev1-context.json
{
  "request_id": "IR-2451",
  "domain": "payments platform",
  "category": "control-plane outage",
  "severity": "SEV1",
  "incident_start_utc": "2024-05-18T21:07:00Z",
  "current_status": "investigating",
  "services_affected": ["checkout-api", "settlement-worker"],
  "customer_impact": "30% of EU transactions failing with 5xx",
  "blast_radius": "eu-west + multi-tenant merchants",
  "symptoms": [
    "Increased pod restarts on checkout-api",
    "Kafka lag growing beyond 2M messages"
  ],
  "recent_changes": ["Istio control-plane upgrade 2 hours prior"],
  "environment": {
    "platform": "GKE",
    "regions": ["europe-west1"],
    "clusters": ["payments-edge"],
    "network_boundary_notes": "Dedicated service mesh for PCI workloads"
  },
  "telemetry": {
    "metrics_available": true,
    "logs_available": true,
    "traces_available": false,
    "dashboards_links": [
      "https://grafana/p/checkout-api",
      "https://grafana/p/kafka-lag"
    ],
    "known_gaps": ["No packet captures for envoy sidecars"]
  },
  "constraints": {
    "change_freeze": false,
    "compliance_notes": "PCI zone requires CAB approval for config drift",
    "risk_tolerance": "medium",
    "engineering_bandwidth": "limited"
  },
  "stakeholders": {
    "incident_commander": "sre-oncall@company.com",
    "communications_lead": "statuspage@company.com",
    "l3_oncall": "payments-core@company.com",
    "l4_platform": "platform-arch@company.com",
    "security_oncall": "psirt@company.com",
    "data_oncall": "analytics-l3@company.com"
  },
  "runbooks": [
    "https://runbooks/checkout-api-restarts",
    "https://runbooks/kafka-lag"
  ],
  "vendors": ["GCP Support", "Confluent"],
  "sla_slo": {
    "sla_name": "Checkout availability",
    "slo_targets": ["99.95% monthly availability"],
    "error_budget_status": "Consumed 35% this month"
  },
  "procurement_context": {
    "contracts_in_place": true,
    "preferred_vendors": ["Fastly", "Equinix"],
    "lead_time_days_estimate": 14
  }
}
EOF

mcp call pack-registry pack.devops.sre.sev1.triage.escalation.plan --input @/tmp/sev1-context.json | tee /tmp/sev1-plan.json
mcp call pack-registry pack.devops.sre.sev1.triage.escalation.execute --input @/tmp/sev1-context.json | tee /tmp/sev1-execute.json
```

### Validate
```bash
cat <<'EOF' > /tmp/sev1-validate.json
{
  "context": $(cat /tmp/sev1-context.json),
  "outputs": $(cat /tmp/sev1-execute.json)
}
EOF
mcp call pack-registry pack.devops.sre.sev1.triage.escalation.validate --input @/tmp/sev1-validate.json
```

## Wrapping up

By the end of these three slices you will have exercised every MCP method, validated that the deterministic templates run, and captured representative telemetry/plan artifacts for growth, revenue, and reliability scenarios. Adjust the sample JSON to mirror your own org contexts when running real smoke tests.
