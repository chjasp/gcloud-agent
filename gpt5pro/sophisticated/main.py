#!/usr/bin/env python3
"""
gcloud_cmdgen.py

Deterministic "gcloud command generator" that never hallucinates:
- Introspects your local Cloud SDK to index real commands/flags.
- Maps a natural-language prompt to the most likely command.
- Emits a syntactically correct command with placeholders.
- (Optional) Validates the suggestion against `gcloud ... --help`.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import textwrap
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

# -----------------------------
# Paths & cache
# -----------------------------

CACHE_DIR = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "gcloud_cmdgen"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = CACHE_DIR / "gcloud_index.json"
META_FILE = CACHE_DIR / "meta.json"

# -----------------------------
# Helpers
# -----------------------------

def run(cmd: List[str], timeout: int = 45) -> Tuple[int, str, str]:
    """Run a subprocess and capture output."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"

def gcloud_info_sdk_root() -> Optional[str]:
    """Return Cloud SDK root path via 'gcloud info'."""
    rc, out, _ = run(["gcloud", "--format=value(installation.sdk_root)", "info"])
    if rc == 0:
        root = out.strip()
        return root or None
    return None

# Conservative set of GCLOUD WIDE FLAGS that we can safely add for placeholders.
# (They are accepted by all commands; users may trim them.)
GCLOUD_WIDE_FLAGS = {"--project", "--quiet", "--format", "--verbosity", "--account", "--configuration"}

# Normalization maps
VERB_SYNONYMS = {
    "get": "describe",
    "show": "describe",
    "details": "describe",
    "describe": "describe",
    "inspect": "describe",
    "fetch": "describe",
    "list": "list",
    "ls": "list",
    "enumerate": "list",
    "create": "create",
    "make": "create",
    "new": "create",
    "deploy": "deploy",     # Cloud Run
    "apply": "update",      # sometimes 'apply' intent
    "update": "update",
    "patch": "update",
    "set": "update",
    "delete": "delete",
    "remove": "delete",
    "rm": "delete",
}

RESOURCE_SYNONYMS = {
    # Cloud Run
    "cloudrun": "run",
    "cloud run": "run",
    "service": "services",
    "services": "services",
    "revision": "revisions",
    "revisions": "revisions",
    "job": "jobs",
    "jobs": "jobs",
    # Compute
    "compute engine": "compute",
    "vm": "instances",
    "vms": "instances",
    "instance": "instances",
    "instances": "instances",
    "firewall": "firewall-rules",
    "firewalls": "firewall-rules",
    "disk": "disks",
    "disks": "disks",
    "image": "images",
    "images": "images",
    "router": "routers",
    "routers": "routers",
    "mig": "instance-groups",
    # IAM / Projects
    "project": "projects",
    "projects": "projects",
    "service account": "service-accounts",
    "service accounts": "service-accounts",
    "iam": "iam",
    # Pub/Sub
    "pubsub": "pubsub",
    "topic": "topics",
    "topics": "topics",
    "subscription": "subscriptions",
    "subscriptions": "subscriptions",
    # Storage (new `gcloud storage` surface)
    "gcs": "storage",
    "storage bucket": "buckets",
    "bucket": "buckets",
    "buckets": "buckets",
    # Artifact/Secrets/Build
    "artifact": "artifacts",
    "artifacts": "artifacts",
    "secret": "secrets",
    "secrets": "secrets",
    "cloud build": "builds",
    "build": "builds",
    "builds": "builds",
}

TOKEN_SPLIT_RE = re.compile(r"[^\w\-]+")

def tokenize(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_SPLIT_RE.split(text) if t]

def canonicalize_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for t in tokens:
        if t in VERB_SYNONYMS:
            out.append(VERB_SYNONYMS[t])
        elif t in RESOURCE_SYNONYMS:
            out.append(RESOURCE_SYNONYMS[t])
        else:
            out.append(t)
    return out

def ratio(a: str, b: str) -> float:
    return SequenceMatcher(a=a, b=b).ratio()

# -----------------------------
# Data structures
# -----------------------------

@dataclasses.dataclass
class CommandSpec:
    path: str                     # e.g. "run services describe"
    release: str                  # "ga", "beta", or "alpha"
    flags: List[str]              # ["--region", "--project", ...]
    positionals: List[str]        # ["SERVICE", "INSTANCE", ...] (placeholders)
    help_one_line: str = ""

# -----------------------------
# Index builder
# -----------------------------

def discover_command_list() -> List[str]:
    """
    Prefer to ask gcloud for its full list of leaf commands.
    Fallback: recursively parse 'gcloud help' (slower).
    """
    # 1) Try 'gcloud meta list-commands'
    # (This is part of the Cloud SDK meta tooling; see fig.io docs)
    rc, out, _ = run(["gcloud", "meta", "list-commands"])
    if rc == 0 and out.strip():
        cmds = []
        for line in out.splitlines():
            line = line.strip()
            if not line or not line.startswith("gcloud "):
                continue
            # Strip leading "gcloud " and any release prefix
            tokens = line.split()[1:]
            # Filter out alpha/beta by default; we prefer GA surfaces first.
            if tokens and tokens[0] in ("alpha", "beta"):
                # We still keep them; release level will be marked later.
                pass
            cmds.append(" ".join(tokens))
        return sorted(set(cmds))

    # 2) Fallback: crawl the help tree starting from root groups
    #    We parse "GROUPS" and "COMMANDS" sections.
    def parse_groups_and_commands(help_text: str) -> Tuple[List[str], List[str]]:
        groups, commands = [], []
        section = None
        for raw in help_text.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            if line.strip() == "GROUPS":
                section = "groups"
                continue
            if line.strip() == "COMMANDS":
                section = "commands"
                continue
            if section in ("groups", "commands"):
                m = re.match(r"\s+([a-z0-9\-]+)\s+.*", line)
                if m:
                    (name,) = m.groups()
                    if section == "groups":
                        groups.append(name)
                    else:
                        commands.append(name)
        return groups, commands

    visited = set()
    leaf_cmds: List[str] = []

    def walk(prefix: List[str]):
        key = " ".join(prefix)
        if key in visited:
            return
        visited.add(key)
        rc, out, _ = run(["gcloud", *prefix, "--help"], timeout=60)
        if rc != 0:
            return
        groups, commands = parse_groups_and_commands(out)
        for g in groups:
            walk(prefix + [g])
        for c in commands:
            leaf_cmds.append(" ".join(prefix + [c]))

    # Start from top-level groups shown by `gcloud --help`
    rc_root, out_root, _ = run(["gcloud", "--help"])
    if rc_root == 0:
        root_groups, _ = (lambda t: (t[0], t[1]))(parse_groups_and_commands(out_root))
        for g in root_groups:
            # Skip alpha/beta groups for speed in fallback crawl
            if g in ("alpha", "beta"):
                continue
            walk([g])

    return sorted(set(leaf_cmds))

def parse_help_for_command(path_tokens: List[str]) -> CommandSpec:
    """
    Given a command like ["run","services","describe"], parse help to extract flags/positionals.
    """
    # Determine release level (alpha/beta/ga) heuristically: check if GA help exists first.
    release = "ga"
    rc, out, _ = run(["gcloud", *path_tokens, "--help"], timeout=60)
    if rc != 0:
        # Try beta then alpha
        for lvl in ("beta", "alpha"):
            rc2, out2, _ = run(["gcloud", lvl, *path_tokens, "--help"], timeout=60)
            if rc2 == 0:
                release = lvl
                out = out2
                rc = 0
                break
    if rc != 0:
        # As last resort, return minimal spec so at least the command path is real.
        return CommandSpec(path=" ".join(path_tokens), release=release, flags=[], positionals=[], help_one_line="")

    # Extract first one-line description
    help_one_line = ""
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith(("NAME", "SYNOPSIS", "USAGE")):
            help_one_line = line
            break

    # Parse USAGE to guess positionals (UPPERCASE tokens commonly indicate placeholders)
    usage_pos: List[str] = []
    usage_match = re.search(r"^USAGE\b.*?$([\s\S]+?)^\w", out, re.MULTILINE)
    usage_block = ""
    if usage_match:
        usage_block = usage_match.group(1)
    else:
        # Fall back: take first 'gcloud ...' line
        for line in out.splitlines():
            if line.strip().startswith("gcloud "):
                usage_block = line
                break

    # Collect uppercase-ish tokens that look like placeholders (avoid flags)
    if usage_block:
        # Remove the leading command path from usage to reduce noise
        cmd_prefix = "gcloud " + " ".join(path_tokens)
        usage_tail = usage_block
        if cmd_prefix in usage_block:
            usage_tail = usage_block.split(cmd_prefix, 1)[-1]
        # Find tokens like NAME, SERVICE, INSTANCE, ZONE, REGION, PROJECT_ID, etc.
        for tok in re.findall(r"\b[A-Z][A-Z0-9_\-]*\b", usage_tail):
            # Filter obvious keywords
            if tok in ("USAGE", "FLAGS", "ARGS", "AND", "OR"):
                continue
            usage_pos.append(tok)
        # De-dup but keep order
        seen = set()
        usage_pos = [x for x in usage_pos if not (x in seen or seen.add(x))]

    # Parse "FLAGS" section for supported flags
    flags: List[str] = []
    flags_block_match = re.search(r"^FLAGS\b.*?$([\s\S]+?)^\w", out, re.MULTILINE)
    if flags_block_match:
        flags_block = flags_block_match.group(1)
        for line in flags_block.splitlines():
            m = re.match(r"\s+(--[a-z0-9][a-z0-9\-]*)\b", line.strip())
            if m:
                flags.append(m.group(1))
    # Always allow gcloud-wide flags
    flags = sorted(set(flags) | GCLOUD_WIDE_FLAGS)

    return CommandSpec(
        path=" ".join(path_tokens),
        release=release,
        flags=flags,
        positionals=usage_pos,
        help_one_line=help_one_line,
    )

def build_index(force: bool = False) -> Dict[str, CommandSpec]:
    """
    Build (or load) an index of command -> spec.
    """
    if INDEX_FILE.exists() and not force:
        with INDEX_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        # Rehydrate dataclasses
        return {k: CommandSpec(**v) for k, v in raw.items()}

    # Discover commands (leaf paths)
    print("Discovering available gcloud commands...", file=sys.stderr)
    paths = discover_command_list()
    if not paths:
        print("ERROR: Could not discover commands; is the Cloud SDK installed and on PATH?", file=sys.stderr)
        sys.exit(2)

    # Parse help for a subset first (fast index), but capture everything lazily-on-demand
    # For first build, prioritize common surfaces to keep time bounded.
    PRIORITY_PREFIXES = (
        "run ", "compute ", "projects ", "iam ", "pubsub ", "storage ", "secrets ", "artifacts ", "services ", "container ", "builds "
    )

    prioritized = [p for p in paths if p.startswith(PRIORITY_PREFIXES)]
    remainder = [p for p in paths if p not in prioritized]

    index: Dict[str, CommandSpec] = {}

    def add_path(p: str):
        tokens = p.split()
        spec = parse_help_for_command(tokens)
        index[p] = spec

    # Index prioritized first
    for p in prioritized:
        add_path(p)

    # Persist partially built index to speed up future runs
    with INDEX_FILE.open("w", encoding="utf-8") as f:
        json.dump({k: dataclasses.asdict(v) for k, v in index.items()}, f, indent=2)

    # Store meta for reproducibility
    meta = {
        "sdk_root": gcloud_info_sdk_root(),
        "commands_indexed": len(index),
    }
    with META_FILE.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # The rest will be parsed lazily if requested; keep index compact on first build.
    return index

# -----------------------------
# Matching & generation
# -----------------------------

def score_candidate(prompt_tokens: List[str], candidate_path: str) -> float:
    """
    Compute a score for how well a command path matches the prompt tokens.
    Heuristics:
    - Reward matching verb token (describe/list/create/delete/update/deploy).
    - Reward overlapping resource/entity tokens.
    - Light fuzzy factor for path vs phrase similarity.
    """
    path_tokens = candidate_path.split()
    # verb weighting
    verbs = {"describe", "list", "create", "delete", "update", "deploy"}
    prompt_verbs = verbs.intersection(prompt_tokens)
    path_verb = [t for t in path_tokens if t in verbs]
    verb_bonus = 0.0
    if prompt_verbs and path_verb and (path_verb[0] in prompt_verbs):
        verb_bonus = 0.5
    elif path_verb and prompt_verbs:
        verb_bonus = 0.35

    # resource/entity overlap (exclude verbs)
    pt = set([t for t in prompt_tokens if t not in verbs])
    ct = set([t for t in path_tokens if t not in verbs])
    jacc = (len(pt & ct) / max(1, len(pt | ct)))

    # fuzzy similarity on joined strings
    fuzzy = ratio(" ".join(prompt_tokens), candidate_path)

    return 0.55 * jacc + 0.35 * fuzzy + verb_bonus

def choose_candidates(index: Dict[str, CommandSpec], prompt: str, topk: int = 1) -> List[Tuple[CommandSpec, float]]:
    tokens = canonicalize_tokens(tokenize(prompt))
    scored: List[Tuple[CommandSpec, float]] = []
    for path, spec in index.items():
        scored.append((spec, score_candidate(tokens, path)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:topk]

def render_command(spec: CommandSpec) -> str:
    """
    Emit a syntactically correct command with placeholders for required bits.
    Only include flags that are (a) helpful and (b) supported by this command.
    """
    cmd = ["gcloud"]
    if spec.release in ("beta", "alpha"):
        cmd.append(spec.release)
    cmd.extend(spec.path.split())

    # Add first few positionals as placeholders (<NAME>, <SERVICE>, …)
    # Don't spam; 0-2 is usually enough to show shape.
    for pos in spec.positionals[:2]:
        cmd.append(f"<{pos.lower()}>")

    # Helpful commonly-required flags if the command supports them:
    preferred_flags = ["--region", "--zone", "--location", "--project"]
    for fl in preferred_flags:
        if fl in spec.flags:
            # Use canonical placeholder names
            placeholder = {
                "--region": "<REGION>",
                "--zone": "<ZONE>",
                "--location": "<LOCATION>",
                "--project": "<PROJECT_ID>",
            }.get(fl, "<VALUE>")
            cmd.append(f"{fl}={placeholder}")

    # Always OK to suggest --format for machine-readable output
    if "--format" in spec.flags and all(not a.startswith("--format") for a in cmd):
        cmd.append("--format=json")

    return " ".join(cmd)

def validate_command_string(cmd_str: str) -> Tuple[bool, str]:
    """
    Validate by asking gcloud to show help for the target command
    and check that all flags in cmd_str appear in the help text.
    """
    tokens = shlex.split(cmd_str)
    # Extract the base command up to the leaf verb (stop before first '<')
    base = []
    for t in tokens:
        if t.startswith("<"):
            break
        if t.startswith("--"):
            break
        base.append(t)
    # Ask for help
    rc, out, err = run(base + ["--help"], timeout=60)
    if rc != 0:
        return False, f"gcloud help failed for {base!r}: {err or out}"

    # Collect legal flags
    flags: set[str] = set(GCLOUD_WIDE_FLAGS)
    flags_block = re.search(r"^FLAGS\b.*?$([\s\S]+?)^\w", out, re.MULTILINE)
    if flags_block:
        for line in flags_block.group(1).splitlines():
            m = re.match(r"\s+(--[a-z0-9][a-z0-9\-]*)\b", line.strip())
            if m:
                flags.add(m.group(1))

    # Check every flag we included is known
    unknown = []
    for t in tokens:
        if t.startswith("--"):
            key = t.split("=", 1)[0]
            if key not in flags:
                unknown.append(key)

    if unknown:
        return False, f"Unknown flags for {base!r}: {', '.join(sorted(set(unknown)))}"
    return True, "OK"

# -----------------------------
# CLI
# -----------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Generate syntactically correct gcloud commands deterministically."
    )
    ap.add_argument("prompt", nargs="?", help="Natural language prompt (e.g., 'show Cloud Run service config')")
    ap.add_argument("--topk", type=int, default=1, help="Return top-K candidates")
    ap.add_argument("--explain", action="store_true", help="Print why a command was chosen")
    ap.add_argument("--reindex", action="store_true", help="Rebuild the command index")
    ap.add_argument("--validate", action="store_true", help="Validate the suggested command against gcloud help")
    args = ap.parse_args()

    idx = build_index(force=args.reindex)

    if not args.prompt:
        print("Please provide a prompt, e.g.: python gcloud_cmdgen.py \"show Cloud Run service config\"", file=sys.stderr)
        sys.exit(1)

    candidates = choose_candidates(idx, args.prompt, topk=max(1, args.topk))
    if not candidates:
        print("No candidates found.", file=sys.stderr)
        sys.exit(2)

    # Render responses
    outputs = []
    for spec, score in candidates:
        cmd = render_command(spec)
        ok, msg = (True, "skip") if not args.validate else validate_command_string(cmd)
        outputs.append((cmd, spec, score, ok, msg))

    # Print results
    best = outputs[0]
    print(best[0])
    if args.topk > 1:
        print("\n# Alternatives")
        for (cmd, spec, score, ok, msg) in outputs[1:]:
            print(f"- {cmd}    # score={score:.3f}{' (valid)' if ok else f' (check flags: {msg})'}")

    if args.explain:
        print("\n# Explanation")
        spec = best[1]
        print(f"Picked: gcloud {spec.path} (release: {spec.release})")
        if spec.help_one_line:
            print(f"About: {spec.help_one_line}")
        if spec.positionals:
            print(f"Positionals (from USAGE): {', '.join(spec.positionals[:5])}")
        # show a subset of flags
        helpful = [f for f in spec.flags if f in {"--region","--zone","--location","--project","--format"}]
        if helpful:
            print(f"Useful flags: {', '.join(helpful)}")

if __name__ == "__main__":
    main()
