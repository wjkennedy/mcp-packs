# Plan: Manufacturing planning: MRP parameters and reorder policy

## Intent
Turn the user's request into an executable plan with minimal assumptions.

## Hard gate: minimum viable context
If any required context is missing, ask only for what is missing:
- Objective (measurable)
- Current state (tools, process, pain)
- Constraints (time, budget, compliance)

Then proceed.

## Planning rules
- Prefer smallest viable scope that produces evidence within the user's time constraint.
- Output must include: procedure steps, decision rules, artifacts to ship, quality gates, telemetry.
- Label assumptions explicitly and propose how to validate them quickly.

## Domain keywords
BOM, lead times, safety stock, reorder point
