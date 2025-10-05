#!/usr/bin/env python3
# Simple, deterministic gcloud command generator.
# Usage: python gcloud_cmd_gen.py "show config for my cloud run service"
import sys

# --- Canonical templates (from official gcloud docs) ---
TEMPLATES = {
    # Cloud Run
    ("cloud_run", "describe"):
        "gcloud run services describe SERVICE_NAME --project=PROJECT_ID --region=REGION --format=yaml",
    ("cloud_run", "list"):
        "gcloud run services list --project=PROJECT_ID --region=REGION",
    ("cloud_run", "logs_read"):
        "gcloud run services logs read SERVICE_NAME --project=PROJECT_ID --limit=50",
    ("cloud_run", "logs_tail"):
        "gcloud beta run services logs tail SERVICE_NAME --project=PROJECT_ID",
    ("cloud_run", "revisions_list"):
        "gcloud run revisions list --service=SERVICE_NAME --region=REGION --project=PROJECT_ID",
    ("cloud_run", "revisions_describe"):
        "gcloud run revisions describe REVISION_NAME --region=REGION --project=PROJECT_ID --format=yaml",

    # GKE (Google Kubernetes Engine)
    ("gke", "describe"):
        "gcloud container clusters describe CLUSTER_NAME --location=LOCATION --project=PROJECT_ID",
    ("gke", "list"):
        "gcloud container clusters list --project=PROJECT_ID",

    # Compute Engine
    ("compute", "describe"):
        "gcloud compute instances describe INSTANCE_NAME --zone=ZONE --project=PROJECT_ID",
    ("compute", "list"):
        "gcloud compute instances list --project=PROJECT_ID",

    # Cloud SQL
    ("cloud_sql", "describe"):
        "gcloud sql instances describe INSTANCE_NAME --project=PROJECT_ID",
    ("cloud_sql", "list"):
        "gcloud sql instances list --project=PROJECT_ID",

    # Cloud Storage (gcloud storage)
    ("storage", "describe"):
        "gcloud storage buckets describe gs://BUCKET_NAME --project=PROJECT_ID",
    ("storage", "list"):
        "gcloud storage buckets list --project=PROJECT_ID",

    # IAM (project-level)
    ("iam", "policy"):
        "gcloud projects get-iam-policy PROJECT_ID --format=json",
}

# --- Keyword dictionaries (simple heuristics) ---
SERVICE_HINTS = {
    "cloud_run": (
        "cloud run", "run service", "cloudrun", "run ",
        "cloud-run", "serverless run"
    ),
    "gke": (
        "gke", "kubernetes", "k8s", "cluster", "kubernetes engine"
    ),
    "compute": (
        "compute engine", "compute", "vm", "instance", "vm instance", "gce"
    ),
    "cloud_sql": (
        "cloud sql", "sql instance", "postgres", "mysql", "cloudsql", "csql"
    ),
    "storage": (
        "cloud storage", "gcs", "bucket", "storage"
    ),
    "iam": (
        "iam", "policy", "who has access", "permissions", "roles", "members", "access"
    ),
}

ACTION_HINTS = {
    "describe": (
        "describe", "config", "configuration", "settings", "details", "inspect", "spec", "yaml", "show config"
    ),
    "list": (
        "list", "ls", "show all", "enumerate", "list all"
    ),
    "logs_read": (
        "logs", "read logs", "view logs", "get logs", "error logs", "errors"
    ),
    "logs_tail": (
        "tail", "stream", "follow"
    ),
    "revisions_list": (
        "revisions", "history", "previous versions", "all revisions"
    ),
    "revisions_describe": (
        "revision details", "describe revision"
    ),
    "policy": (
        "iam policy", "policy", "who has access", "members", "roles", "permissions"
    ),
}

# Prefer actions in this order if multiple match.
ACTION_PREFERENCE = [
    "logs_tail", "logs_read",
    "revisions_describe", "revisions_list",
    "describe", "list", "policy"
]

def pick_service(text: str):
    t = text.lower()
    for svc, hints in SERVICE_HINTS.items():
        if any(h in t for h in hints):
            return svc
    return None

def pick_action(service: str, text: str):
    # IAM defaults to policy if hinted.
    if service == "iam":
        return "policy"
    t = text.lower()
    matches = [k for k, hints in ACTION_HINTS.items() if any(h in t for h in hints)]
    if not matches:
        # default read-only action is describe for most services
        return "describe"
    # resolve conflicts with a stable preference order
    for pref in ACTION_PREFERENCE:
        if pref in matches:
            return pref
    return matches[0]

def generate(prompt: str) -> str:
    service = pick_service(prompt)
    if not service:
        return "Unsupported/ambiguous prompt. Try mentioning a product (e.g., 'Cloud Run', 'GKE', 'Compute Engine', 'Cloud SQL', 'Cloud Storage', or 'IAM')."
    action = pick_action(service, prompt)
    key = (service, action)
    if key not in TEMPLATES:
        return f"No safe template for: {service} + {action}. Add one to TEMPLATES."
    return TEMPLATES[key]

def main():
    if len(sys.argv) < 2:
        print("Usage: python gcloud_cmd_gen.py \"your prompt here\"")
        sys.exit(1)
    prompt = sys.argv[1]
    print(generate(prompt))

if __name__ == "__main__":
    main()
