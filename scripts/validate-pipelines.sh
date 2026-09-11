#!/usr/bin/env python3
"""Validate all pipeline JSON files against config/datasets.yaml.

Checks:
  - Valid JSON syntax
  - Required top-level keys: description, engine, steps
  - Each step references a known dataset key from datasets.yaml
  - Each step scenario matches a test_procedure defined for that dataset

Usage:
  python3 pipelines/validate-pipelines.sh
  # or make it executable:
  chmod +x pipelines/validate-pipelines.sh && ./pipelines/validate-pipelines.sh
"""
import json
import os
import sys
import yaml

REPO_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS_YAML = os.path.join(REPO_ROOT, "config", "datasets.yaml")
PIPELINES_DIR = os.path.join(REPO_ROOT, "pipelines")

with open(DATASETS_YAML) as f:
    cfg = yaml.safe_load(f)

datasets = cfg.get("datasets", {})
valid_scenarios: dict[str, list[str]] = {}
for name, ds in datasets.items():
    procs = [p["name"] if isinstance(p, dict) else p for p in ds.get("test_procedures", [])]
    valid_scenarios[name] = procs

errors   = []
warnings = []
checked  = 0

for fname in sorted(os.listdir(PIPELINES_DIR)):
    if not fname.endswith(".json"):
        continue
    checked += 1
    path = os.path.join(PIPELINES_DIR, fname)

    try:
        with open(path) as f:
            pipeline = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"{fname}: invalid JSON — {e}")
        continue

    for key in ("description", "engine", "steps"):
        if key not in pipeline:
            errors.append(f"{fname}: missing required key '{key}'")

    steps = pipeline.get("steps", [])
    if not steps:
        warnings.append(f"{fname}: no steps defined")

    for i, step in enumerate(steps):
        ds = step.get("dataset")
        sc = step.get("scenario")
        if not ds:
            errors.append(f"{fname} step[{i}]: missing 'dataset'")
            continue
        if not sc:
            errors.append(f"{fname} step[{i}]: missing 'scenario'")
            continue
        if ds not in valid_scenarios:
            errors.append(f"{fname} step[{i}]: unknown dataset '{ds}'")
            continue
        known = valid_scenarios[ds]
        if known and sc not in known:
            errors.append(
                f"{fname} step[{i}]: unknown scenario '{sc}' for dataset '{ds}' "
                f"(valid: {known})"
            )

# ── Report ────────────────────────────────────────────────────────────────────
if errors:
    print(f"ERRORS ({len(errors)}):")
    for e in errors:
        print(f"  ✗  {e}")
else:
    print(f"✓  No errors")

if warnings:
    print(f"\nWARNINGS ({len(warnings)}):")
    for w in warnings:
        print(f"  ⚠  {w}")

print(f"\nChecked {checked} pipeline files against {len(valid_scenarios)} datasets.")
sys.exit(1 if errors else 0)
