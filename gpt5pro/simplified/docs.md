# Why this avoids hallucinations

- **Deterministic & whitelisted:** It never invents flags—only emits templates straight from Google’s docs.
- **Read‑only defaults:** `describe`, `list`, and log reads are the safest defaults for troubleshooting.
- **Extensible:** To add coverage, just append another `(service, action) → template` entry and a couple of keywords.

## Source references (templates verified from official docs)

- `gcloud run services describe` and `list`.  
  Google Cloud  
  +1

- `gcloud run services logs read` (existing logs) and `gcloud beta run services logs tail` (tail).  
  Google Cloud  
  +2  
  Google Cloud  
  +2

- `gcloud run revisions list` / `describe`.  
  Google Cloud  
  +1

- `gcloud container clusters describe` / `list`.  
  Google Cloud  
  +2  
  Google Cloud  
  +2

- `gcloud compute instances describe`.  
  Google Cloud  
  +1

- `gcloud sql instances describe`.  
  Google Cloud  
  +1

- `gcloud storage buckets describe` / `list`.  
  Google Cloud  
  +1

- `gcloud projects get-iam-policy`.  
  Google Cloud

---

If you want, I can drop in more templates for your most common cases (e.g., Cloud Run jobs, Pub/Sub, VPC firewall rules) in the same 1‑line style while keeping the script small.
