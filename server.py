#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions


def sanitize_tool_token(value: str) -> str:
    """
    Convert arbitrary identifiers to Codex-safe tool tokens:
    allowed chars: a-zA-Z0-9_- only.
    """
    # Replace dots with underscore (common in pack_ids)
    value = value.replace(".", "_")
    # Replace anything else not allowed with underscore
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value)
    # Collapse repeated underscores
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def make_tool_name(pack_id: str, method: str) -> str:
    return f"pack__{sanitize_tool_token(pack_id)}__{method}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def is_truthy_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def dotted_required_paths(schema: Dict[str, Any], prefix: str = "$") -> List[str]:
    """
    Extract a conservative list of required dotted paths from a JSON schema.
    This is not a full JSON Schema traversal; it is intentionally predictable.
    """
    required_paths: List[str] = []

    schema_type = schema.get("type")
    if schema_type != "object":
        return required_paths

    properties: Dict[str, Any] = schema.get("properties", {}) or {}
    required: List[str] = schema.get("required", []) or []

    for key in required:
        required_paths.append(f"{prefix}.{key}")
        subschema = properties.get(key)
        if isinstance(subschema, dict):
            required_paths.extend(dotted_required_paths(subschema, prefix=f"{prefix}.{key}"))

    return required_paths


def get_by_dotted_path(obj: Any, dotted: str) -> Tuple[bool, Any]:
    """
    dotted like "$.a.b.c"
    """
    if not dotted.startswith("$."):
        return False, None
    parts = dotted[2:].split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False, None
    return True, cur


def missing_required_fields(context: Dict[str, Any], required_paths: List[str]) -> List[str]:
    missing: List[str] = []
    for path in required_paths:
        ok, value = get_by_dotted_path(context, path)
        if not ok or value is None:
            missing.append(path)
    return missing


@dataclass(frozen=True)
class PackTool:
    pack_id: str
    method: str  # describe|requirements|plan|execute|validate
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]]


@dataclass
class Pack:
    root: Path
    pack_json: Dict[str, Any]
    context_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    events_schema: Dict[str, Any]
    plan_prompt: str
    execute_prompt: str
    validate_prompt: str

    @property
    def pack_id(self) -> str:
        return str(self.pack_json["pack_id"])

    @property
    def name(self) -> str:
        return str(self.pack_json.get("name", self.pack_id))

    @property
    def version(self) -> str:
        return str(self.pack_json.get("version", "0.0.0"))

    @property
    def summary(self) -> str:
        return str(self.pack_json.get("summary", ""))

    def required_paths(self) -> List[str]:
        return dotted_required_paths(self.context_schema)

    def recommended_paths(self) -> List[str]:
        # Heuristic: find common "recommended" in prompt or pack.json tool definitions if present.
        # For MVP, we keep it small and predictable.
        return sorted(
            {
                "$.funnel.current_activation_definition",
                "$.funnel.conversion_rates",
                "$.product.time_to_value_minutes",
                "$.instrumentation.analytics_stack",
                "$.constraints.engineering_bandwidth_weeks",
            }
        )
    def tool_name(self, method: str) -> str:
        return make_tool_name(self.pack_id, method)


class PackRegistry:
    def __init__(self, packs_dir: Path) -> None:
        self.packs_dir = packs_dir
        self.packs: Dict[str, Pack] = {}
        self.tools: Dict[str, PackTool] = {}

    def load(self) -> None:
        if not self.packs_dir.exists() or not self.packs_dir.is_dir():
            raise ValueError(f"packs-dir not found or not a directory: {self.packs_dir}")

        for pack_root in sorted(self.packs_dir.iterdir()):
            if not pack_root.is_dir():
                continue
            pack_json_path = pack_root / "pack.json"
            schemas_dir = pack_root / "schemas"
            prompts_dir = pack_root / "prompts"

            if not pack_json_path.exists():
                continue

            pack_json = load_json(pack_json_path)

            context_schema_path = schemas_dir / "context.schema.json"
            output_schema_path = schemas_dir / "output.schema.json"
            events_schema_path = schemas_dir / "events.schema.json"

            plan_prompt_path = prompts_dir / "plan.md"
            execute_prompt_path = prompts_dir / "execute.md"
            validate_prompt_path = prompts_dir / "validate.md"

            for p in [
                context_schema_path,
                output_schema_path,
                events_schema_path,
                plan_prompt_path,
                execute_prompt_path,
                validate_prompt_path,
            ]:
                if not p.exists():
                    raise ValueError(f"Missing required file for pack {pack_root.name}: {p}")

            pack = Pack(
                root=pack_root,
                pack_json=pack_json,
                context_schema=load_json(context_schema_path),
                output_schema=load_json(output_schema_path),
                events_schema=load_json(events_schema_path),
                plan_prompt=read_text(plan_prompt_path),
                execute_prompt=read_text(execute_prompt_path),
                validate_prompt=read_text(validate_prompt_path),
            )

            self.packs[pack.pack_id] = pack

        self._build_tools()

    def _build_tools(self) -> None:
        self.tools.clear()
        for pack_id, pack in self.packs.items():
            # describe
            self.tools[pack.tool_name("describe")] = PackTool(
                pack_id=pack_id,
                method="describe",
                name=pack.tool_name("describe"),
                description=f"Describe pack '{pack.name}' ({pack.version}).",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={
                    "type": "object",
                    "properties": {
                        "pack_id": {"type": "string"},
                        "name": {"type": "string"},
                        "version": {"type": "string"},
                        "summary": {"type": "string"},
                        "domain_tags": {"type": "array", "items": {"type": "string"}},
                        "primary_outcomes": {"type": "array", "items": {"type": "string"}},
                        "trigger_hints": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_id", "name", "version", "summary", "domain_tags", "primary_outcomes", "trigger_hints"],
                    "additionalProperties": False,
                },
            )

            # requirements
            self.tools[pack.tool_name("requirements")] = PackTool(
                pack_id=pack_id,
                method="requirements",
                name=pack.tool_name("requirements"),
                description=f"Return required and recommended context fields for pack '{pack.name}'.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={
                    "type": "object",
                    "properties": {
                        "required_fields": {"type": "array", "items": {"type": "string"}},
                        "recommended_fields": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["required_fields", "recommended_fields", "notes"],
                    "additionalProperties": False,
                },
            )

            # plan
            self.tools[pack.tool_name("plan")] = PackTool(
                pack_id=pack_id,
                method="plan",
                name=pack.tool_name("plan"),
                description=f"Create a funnel improvement plan for '{pack.name}'. Returns needs_context if required fields are missing.",
                input_schema=pack.context_schema,
                output_schema={
                    "type": "object",
                    "properties": {
                        "needs_context": {"type": "boolean"},
                        "missing_fields": {"type": "array", "items": {"type": "string"}},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "plan_steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step_id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "rationale": {"type": "string"},
                                    "success_check": {"type": "string"},
                                    "inputs_needed": {"type": "array", "items": {"type": "string"}},
                                    "outputs_produced": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["step_id", "title", "rationale", "success_check", "inputs_needed", "outputs_produced"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["needs_context", "missing_fields", "assumptions", "plan_steps"],
                    "additionalProperties": False,
                },
            )

            # execute
            self.tools[pack.tool_name("execute")] = PackTool(
                pack_id=pack_id,
                method="execute",
                name=pack.tool_name("execute"),
                description=f"Generate funnel artifacts (activation definition, instrumentation plan, hypotheses, experiments) for '{pack.name}'.",
                input_schema=pack.context_schema,
                output_schema=pack.output_schema,
            )

            # validate
            self.tools[pack.tool_name("validate")] = PackTool(
                pack_id=pack_id,
                method="validate",
                name=pack.tool_name("validate"),
                description=f"Validate outputs for '{pack.name}' against quality gates.",
                input_schema={
                    "type": "object",
                    "properties": {"context": pack.context_schema, "outputs": pack.output_schema},
                    "required": ["context", "outputs"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "passed": {"type": "boolean"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "fixes": {"type": "array", "items": {"type": "string"}},
                        "quality_gates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "gate": {"type": "string"},
                                    "status": {"type": "string", "enum": ["pass", "fail"]},
                                    "evidence": {"type": "string"},
                                },
                                "required": ["gate", "status", "evidence"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["passed", "issues", "fixes", "quality_gates"],
                    "additionalProperties": False,
                },
            )

    def list_tools(self) -> List[types.Tool]:
        tool_list: List[types.Tool] = []
        for t in sorted(self.tools.values(), key=lambda x: x.name):
            tool_list.append(
                types.Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                    outputSchema=t.output_schema,
                )
            )
        return tool_list


def build_trigger_hints(pack_json: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    sel = pack_json.get("selection", {}) or {}
    triggers = sel.get("triggers", []) or []
    for trig in triggers:
        tid = trig.get("id")
        if tid:
            hints.append(str(tid))
    return hints


def make_telemetry_event(pack: Pack, event_type: str, correlation_id: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "timestamp_iso": utc_now_iso(),
        "pack_id": pack.pack_id,
        "version": pack.version,
        "correlation_id": correlation_id,
        "details": details,
    }


def plan_steps_template(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    funnel_stage = (
        (context.get("thread_colors", {}) or {}).get("funnel_stage")
        if isinstance(context.get("thread_colors"), dict)
        else None
    )
    stage_note = f"Focus stage: {funnel_stage}" if funnel_stage else "Focus stage: unspecified"

    return [
        {
            "step_id": "1",
            "title": "Confirm activation definition and measurable proxy",
            "rationale": "Activation must represent delivered value and predict retention or paid conversion.",
            "success_check": "Activation event is defined with criteria and a time window; it is instrumentable.",
            "inputs_needed": ["$.product.trial_type", "$.product.aha_moment_description"],
            "outputs_produced": ["activation_definition"],
        },
        {
            "step_id": "2",
            "title": "Confirm funnel boundaries and conversion points",
            "rationale": f"Stage boundaries prevent measurement drift. {stage_note}",
            "success_check": "Stages are enumerated and map to trackable events.",
            "inputs_needed": ["$.funnel.stages", "$.funnel.conversion_rates"],
            "outputs_produced": ["event_taxonomy", "instrumentation_plan.gaps"],
        },
        {
            "step_id": "3",
            "title": "Define event taxonomy and instrumentation deltas",
            "rationale": "Without consistent events, improvements cannot be attributed or reproduced.",
            "success_check": "Minimal event set covers acquisition to purchase with required properties and QA checks.",
            "inputs_needed": ["$.instrumentation.event_tracking_available", "$.instrumentation.analytics_stack"],
            "outputs_produced": ["event_taxonomy", "instrumentation_plan"],
        },
        {
            "step_id": "4",
            "title": "Build hypotheses tied to likely drop-off causes",
            "rationale": "Hypotheses make interventions testable instead of random changes.",
            "success_check": "At least 3 hypotheses include evidence needed.",
            "inputs_needed": ["$.funnel.dropoff_notes", "$.signals.dropoff_detected"],
            "outputs_produced": ["hypotheses"],
        },
        {
            "step_id": "5",
            "title": "Generate experiment backlog with success thresholds",
            "rationale": "Experiments require explicit success criteria to avoid local maxima and false wins.",
            "success_check": "At least 5 experiments each include target metric, threshold, duration, and stopping rule.",
            "inputs_needed": ["$.constraints.engineering_bandwidth_weeks", "$.instrumentation.ab_testing_available"],
            "outputs_produced": ["experiment_backlog"],
        },
        {
            "step_id": "6",
            "title": "Prioritize actions for 2-week and 6-week horizons",
            "rationale": "Sequencing matters; prioritize measurement integrity and low-effort high-impact changes first.",
            "success_check": "Prioritized actions include owner and expected impact and respect constraints.",
            "inputs_needed": ["$.constraints.engineering_bandwidth_weeks", "$.constraints.channels_in_scope"],
            "outputs_produced": ["prioritized_actions"],
        },
    ]


def execute_artifacts(pack: Pack, context: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
    required = pack.required_paths()
    missing = missing_required_fields(context, required)
    if missing:
        return {
            "needs_context": True,
            "missing_fields": missing,
            "assumptions": [],
            "telemetry_events": [
                make_telemetry_event(pack, "pack_loaded", correlation_id, {"mode": "execute"}),
                make_telemetry_event(pack, "needs_context", correlation_id, {"missing_fields": missing}),
            ],
        }

    assumptions: List[str] = []

    product = context.get("product", {}) or {}
    funnel = context.get("funnel", {}) or {}
    instr = context.get("instrumentation", {}) or {}

    trial_type = product.get("trial_type", "unknown")
    product_name = product.get("name", "product")
    stages = funnel.get("stages", []) or []

    # Activation definition heuristic
    time_window_hours = 72.0
    if isinstance(product.get("time_to_value_minutes"), (int, float)):
        ttv = float(product["time_to_value_minutes"])
        if ttv <= 60:
            time_window_hours = 24.0
        elif ttv <= 240:
            time_window_hours = 48.0

    activation_event_name = "activation_completed"
    activation_criteria = [
        "User reaches first value screen or completes first successful core action",
        "User uses one core feature at least once",
    ]
    if trial_type in {"sales_assisted_poc", "feature_limited"}:
        activation_event_name = "activation_verified"
        activation_criteria.append("Account has at least one collaborator or admin configuration completed")

    activation_desc = (
        f"Represents the earliest measurable value moment for {product_name} within the trial."
    )

    activation_definition = {
        "activation_event_name": activation_event_name,
        "activation_event_description": activation_desc,
        "criteria": activation_criteria,
        "time_window_hours": time_window_hours,
        "reasoning": "Chosen to correlate with delivered value and to be instrumentable across segments.",
    }

    # Event taxonomy heuristic
    events: List[Dict[str, Any]] = [
        {"name": "identify_user", "type": "identify", "properties": ["user_id", "account_id", "role"], "required": True, "notes": "Bind user to account and role."},
        {"name": "track_session_started", "type": "track", "properties": ["source", "campaign", "device"], "required": True, "notes": "Attribution and segmentation."},
        {"name": "track_signup_completed", "type": "track", "properties": ["method", "email_domain", "account_size_guess"], "required": True, "notes": "Signup conversion anchor."},
        {"name": "track_onboarding_step_completed", "type": "track", "properties": ["step_id", "step_name"], "required": True, "notes": "Measure onboarding flow completion."},
        {"name": f"track_{activation_event_name}", "type": "track", "properties": ["criteria_met", "time_since_signup_minutes"], "required": True, "notes": "Primary activation event."},
        {"name": "track_trial_started", "type": "track", "properties": ["plan", "trial_days"], "required": True, "notes": "Trial start anchor."},
        {"name": "track_pricing_viewed", "type": "track", "properties": ["plan", "currency"], "required": False, "notes": "Purchase intent signal."},
        {"name": "track_checkout_started", "type": "track", "properties": ["plan", "seats"], "required": False, "notes": "Purchase funnel entry."},
        {"name": "track_purchase_completed", "type": "track", "properties": ["plan", "seats", "amount"], "required": True, "notes": "Paid conversion anchor."},
        {"name": "track_blocker_encountered", "type": "track", "properties": ["blocker_type", "message", "surface"], "required": True, "notes": "Capture friction and errors."},
    ]

    # Instrumentation plan heuristic
    gaps: List[str] = []
    if not instr.get("event_tracking_available", False):
        gaps.append("Event tracking not available; implement a track/identify pipeline.")
    if "track_blocker_encountered" and not instr.get("warehouse_available", False):
        assumptions.append("No warehouse available; rely on analytics tool exports or in-tool dashboards for experiment reads.")

    implementation_steps = [
        "Standardize user_id and account_id across all events.",
        "Implement required events first: signup, onboarding steps, activation, purchase.",
        "Add attribution properties (source, campaign) at session start.",
        "Backfill missing properties using server-side enrichment where feasible.",
    ]
    data_quality_checks = [
        "Daily event volume sanity checks for required events.",
        "Schema validation for required properties (non-null, correct type).",
        "Cross-check signup counts against source of truth (auth/CRM) weekly.",
    ]

    instrumentation_plan = {
        "gaps": gaps,
        "implementation_steps": implementation_steps,
        "data_quality_checks": data_quality_checks,
    }

    # Hypotheses heuristic
    hypotheses = [
        {
            "id": "H1",
            "statement": "Users fail to reach activation because onboarding asks for too much before first value.",
            "funnel_stage": "activation",
            "likely_cause": "High cognitive load and unclear next action.",
            "evidence_needed": ["Onboarding step drop-off rates", "Time-to-activation distribution", "Top blocker events by surface"],
        },
        {
            "id": "H2",
            "statement": "Trial-to-paid is constrained by missing buyer-facing justification and procurement readiness.",
            "funnel_stage": "paid_conversion",
            "likely_cause": "Buyer needs ROI, security posture, and pricing clarity.",
            "evidence_needed": ["Pricing page views vs checkout starts", "Sales assist requests", "Common objections from support/sales notes"],
        },
        {
            "id": "H3",
            "statement": "Activation does not predict purchase because the activation event is too weak or not value-aligned.",
            "funnel_stage": "activation",
            "likely_cause": "Event measures activity, not value.",
            "evidence_needed": ["Activation to paid correlation", "Retention by activated cohort", "Feature usage among paid converters"],
        },
    ]

    # Experiment backlog heuristic
    experiment_backlog = [
        {
            "experiment_id": "E1",
            "title": "Reduce onboarding steps before first value",
            "type": "ux",
            "target_metric": "signup_to_activation",
            "success_threshold": "Increase signup_to_activation by +10% relative within 14 days",
            "duration_days": 14,
            "risk": "low",
            "effort": "m",
            "dependencies": ["Event tracking for onboarding steps", "Feature flag or conditional UI"],
            "stopping_rule": "Stop early if activation rate decreases by >5% for 3 consecutive days",
        },
        {
            "experiment_id": "E2",
            "title": "Add guided next-action prompt after signup",
            "type": "in_app_prompt",
            "target_metric": "time_to_activation",
            "success_threshold": "Reduce median time_to_activation by 20%",
            "duration_days": 14,
            "risk": "low",
            "effort": "s",
            "dependencies": ["Activation event instrumentation"],
            "stopping_rule": "Stop if prompt increases blocker events by >10%",
        },
        {
            "experiment_id": "E3",
            "title": "Buyer-ready value summary for trial accounts",
            "type": "email",
            "target_metric": "trial_start_to_paid",
            "success_threshold": "Increase trial_start_to_paid by +5% absolute over 21 days",
            "duration_days": 21,
            "risk": "medium",
            "effort": "m",
            "dependencies": ["Email delivery system", "Account role identification"],
            "stopping_rule": "Stop if unsubscribe rate exceeds baseline by 2x",
        },
        {
            "experiment_id": "E4",
            "title": "Pricing clarity test: simplify plans and highlight default choice",
            "type": "copy",
            "target_metric": "pricing_view_to_checkout_started",
            "success_threshold": "Increase pricing_view_to_checkout_started by +15% relative",
            "duration_days": 21,
            "risk": "medium",
            "effort": "m",
            "dependencies": ["Pricing page event tracking", "A/B testing or feature flags"],
            "stopping_rule": "Stop if purchase_completed decreases by >5% relative after 7 days",
        },
        {
            "experiment_id": "E5",
            "title": "Instrument blocker events and build a top-friction dashboard",
            "type": "instrumentation",
            "target_metric": "blocker_rate",
            "success_threshold": "Identify top 3 blocker types with reliable counts within 7 days",
            "duration_days": 7,
            "risk": "low",
            "effort": "s",
            "dependencies": ["Add track_blocker_encountered", "Basic dashboarding"],
            "stopping_rule": "Stop if event pipeline causes latency or errors in production",
        },
    ]

    prioritized_actions = [
        {
            "rank": 1,
            "action": "Finalize activation definition and instrument required events (signup, onboarding, activation, purchase)",
            "why": "Measurement integrity is prerequisite for all improvements.",
            "owner": "data",
            "expected_impact": "Enables reliable funnel reads and experimentation",
        },
        {
            "rank": 2,
            "action": "Reduce steps before first value and add a guided next-action prompt",
            "why": "Typically yields the fastest activation gains in PLG funnels.",
            "owner": "product",
            "expected_impact": "Higher signup_to_activation and faster time to value",
        },
        {
            "rank": 3,
            "action": "Add buyer-facing value summary and procurement readiness signals during trial",
            "why": "Addresses B2B conversion friction beyond user activation.",
            "owner": "growth",
            "expected_impact": "Improved trial_start_to_paid conversion",
        },
    ]

    telemetry = [
        make_telemetry_event(pack, "pack_loaded", correlation_id, {"mode": "execute"}),
        make_telemetry_event(pack, "execution_completed", correlation_id, {"stages": stages, "trial_type": trial_type}),
    ]

    return {
        "needs_context": False,
        "missing_fields": [],
        "assumptions": assumptions,
        "activation_definition": activation_definition,
        "event_taxonomy": {"events": events},
        "instrumentation_plan": instrumentation_plan,
        "hypotheses": hypotheses,
        "experiment_backlog": experiment_backlog,
        "prioritized_actions": prioritized_actions,
        "telemetry_events": telemetry,
    }


def validate_outputs(pack: Pack, context: Dict[str, Any], outputs: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    fixes: List[str] = []
    gates: List[Dict[str, Any]] = []

    # Gate 1: needs_context should not be true if outputs contain artifacts
    needs_context = bool(outputs.get("needs_context", False))
    has_artifacts = any(k in outputs for k in ["activation_definition", "event_taxonomy", "experiment_backlog", "prioritized_actions"])
    if needs_context and has_artifacts:
        issues.append("outputs.needs_context is true but artifacts are present.")
        fixes.append("If required context is missing, return only needs_context, missing_fields, assumptions, telemetry_events.")
        gates.append({"gate": "needs_context_consistency", "status": "fail", "evidence": "needs_context=true and artifacts present"})
    else:
        gates.append({"gate": "needs_context_consistency", "status": "pass", "evidence": "needs_context aligns with artifact presence"})

    # Gate 2: activation definition measurable
    act = outputs.get("activation_definition")
    if not isinstance(act, dict) or not act.get("activation_event_name") or not act.get("criteria"):
        issues.append("activation_definition missing or incomplete.")
        fixes.append("Provide activation_event_name, criteria[], and time_window_hours.")
        gates.append({"gate": "activation_measurable", "status": "fail", "evidence": "activation_definition incomplete"})
    else:
        gates.append({"gate": "activation_measurable", "status": "pass", "evidence": "activation_event_name and criteria present"})

    # Gate 3: experiments have thresholds and stopping rules
    exps = outputs.get("experiment_backlog")
    if not isinstance(exps, list) or len(exps) < 5:
        issues.append("experiment_backlog must include at least 5 experiments.")
        fixes.append("Add experiments across UX, prompts, email, pricing/copy, instrumentation.")
        gates.append({"gate": "experiment_backlog_minimum", "status": "fail", "evidence": f"count={0 if not isinstance(exps, list) else len(exps)}"})
    else:
        bad = [e for e in exps if not e.get("success_threshold") or not e.get("stopping_rule")]
        if bad:
            issues.append("Some experiments are missing success_threshold or stopping_rule.")
            fixes.append("Ensure every experiment includes success_threshold and stopping_rule.")
            gates.append({"gate": "experiment_thresholds", "status": "fail", "evidence": f"missing_fields_in={len(bad)} experiments"})
        else:
            gates.append({"gate": "experiment_thresholds", "status": "pass", "evidence": "All experiments include thresholds and stopping rules"})

    # Gate 4: event taxonomy coverage
    tax = outputs.get("event_taxonomy")
    if not isinstance(tax, dict) or not isinstance(tax.get("events"), list) or len(tax["events"]) < 3:
        issues.append("event_taxonomy must include at least 3 events.")
        fixes.append("Include identify_user, signup, activation, purchase, plus friction/blocker events.")
        gates.append({"gate": "event_taxonomy_coverage", "status": "fail", "evidence": "events missing or too few"})
    else:
        gates.append({"gate": "event_taxonomy_coverage", "status": "pass", "evidence": f"events_count={len(tax['events'])}"})

    passed = len(issues) == 0
    return {"passed": passed, "issues": issues, "fixes": fixes, "quality_gates": gates}

def parse_pack_tool_name(tool_name: str, registry: PackRegistry) -> Optional[Tuple[str, str]]:
    """
    Parse Codex-safe tool name back to (pack_id, method).
    tool_name format: pack__<sanitized_pack_id>__<method>
    """
    m = re.match(r"^pack__(?P<spack>[a-zA-Z0-9_-]+)__(?P<method>describe|requirements|plan|execute|validate)$", tool_name)
    if not m:
        return None

    spack = m.group("spack")
    method = m.group("method")

    # Reverse lookup: sanitized id -> real pack_id
    for real_pack_id in registry.packs.keys():
        if sanitize_tool_token(real_pack_id) == spack:
            return real_pack_id, method

    return None

async def main() -> None:
    parser = argparse.ArgumentParser(description="Pack Registry MCP Server (stdio)")
    parser.add_argument(
        "--packs-dir",
        type=str,
        default=os.getenv("PACKS_DIR", "./packs"),
        help="Directory containing pack folders (each with pack.json, schemas/, prompts/).",
    )
    parser.add_argument(
        "--server-name",
        type=str,
        default=os.getenv("MCP_SERVER_NAME", "pack-registry"),
        help="MCP server name.",
    )
    parser.add_argument(
        "--server-version",
        type=str,
        default=os.getenv("MCP_SERVER_VERSION", "0.1.0"),
        help="MCP server version.",
    )
    args = parser.parse_args()

    packs_dir = Path(args.packs_dir).expanduser().resolve()
    registry = PackRegistry(packs_dir=packs_dir)
    registry.load()

    server = Server(args.server_name)

    @server.list_tools()
    async def handle_list_tools() -> List[types.Tool]:
        return registry.list_tools()

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> Any:
        parsed = parse_pack_tool_name(name, registry)
        if not parsed:
            raise ValueError(f"Unknown tool: {name}")

        pack_id, method = parsed
        pack = registry.packs.get(pack_id)
        if not pack:
            raise ValueError(f"Unknown pack_id: {pack_id}")

        correlation_id = str(uuid.uuid4())

        if method == "describe":
            return {
                "pack_id": pack.pack_id,
                "name": pack.name,
                "version": pack.version,
                "summary": pack.summary,
                "domain_tags": pack.pack_json.get("domain_tags", []) or [],
                "primary_outcomes": pack.pack_json.get("primary_outcomes", []) or [],
                "trigger_hints": build_trigger_hints(pack.pack_json),
            }

        if method == "requirements":
            return {
                "required_fields": pack.required_paths(),
                "recommended_fields": pack.recommended_paths(),
                "notes": [
                    "Required fields are extracted from context.schema.json required keys.",
                    "If required fields are missing, plan/execute will return needs_context=true and missing_fields[].",
                ],
            }

        if method == "plan":
            if not isinstance(arguments, dict):
                raise ValueError("plan expects a JSON object matching the context schema.")
            required = pack.required_paths()
            missing = missing_required_fields(arguments, required)
            telemetry = [
                make_telemetry_event(pack, "pack_loaded", correlation_id, {"mode": "plan"}),
            ]
            if missing:
                telemetry.append(make_telemetry_event(pack, "needs_context", correlation_id, {"missing_fields": missing}))
                return {"needs_context": True, "missing_fields": missing, "assumptions": [], "plan_steps": []}

            telemetry.append(make_telemetry_event(pack, "plan_generated", correlation_id, {"required_paths_count": len(required)}))
            return {
                "needs_context": False,
                "missing_fields": [],
                "assumptions": [],
                "plan_steps": plan_steps_template(arguments),
            }

        if method == "execute":
            if not isinstance(arguments, dict):
                raise ValueError("execute expects a JSON object matching the context schema.")
            return execute_artifacts(pack, arguments, correlation_id)

        if method == "validate":
            if not isinstance(arguments, dict):
                raise ValueError("validate expects {context, outputs}.")
            ctx = arguments.get("context")
            out = arguments.get("outputs")
            if not isinstance(ctx, dict) or not isinstance(out, dict):
                raise ValueError("validate requires 'context' and 'outputs' as objects.")
            return validate_outputs(pack, ctx, out)

        raise ValueError(f"Unhandled tool method: {method}")

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=args.server_name,
                server_version=args.server_version,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
