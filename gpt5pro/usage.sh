# Build the on-disk index once (fast on subsequent runs)
python gcloud_cmdgen.py --reindex

# Generate a command from a prompt
python gcloud_cmdgen.py "show Cloud Run service configuration"

# See top-3 candidates and verbose explanation
python gcloud_cmdgen.py "list VM instances in a zone" --topk 3 --explain

# Validate the chosen command's flags against gcloud help
python gcloud_cmdgen.py "describe cloud run service foo" --validate
