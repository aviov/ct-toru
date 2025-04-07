#!/bin/bash

# Set the project ID
PROJECT_ID="ct-toru"
REGION="europe-west1"

# Ensure the correct project is set
gcloud config set project $PROJECT_ID

# Delete Cloud Functions
echo "Deleting Cloud Functions..."
FUNCTIONS=$(gcloud functions list --project=$PROJECT_ID --regions=$REGION --format="value(name)")
for FUNCTION in $FUNCTIONS; do
  gcloud functions delete $FUNCTION --region=$REGION --gen2 --quiet || true
done

# Delete Pub/Sub Topics and Subscriptions
echo "Deleting Pub/Sub Topics and Subscriptions..."
TOPICS=$(gcloud pubsub topics list --project=$PROJECT_ID --format="value(name)")
for TOPIC in $TOPICS; do
  SUBSCRIPTIONS=$(gcloud pubsub subscriptions list --filter="topic=$TOPIC" --format="value(name)")
  for SUB in $SUBSCRIPTIONS; do
    gcloud pubsub subscriptions delete $SUB --quiet || true
  done
  gcloud pubsub topics delete $TOPIC --quiet || true
done

# Delete Cloud Storage Buckets
echo "Deleting Cloud Storage Buckets..."
BUCKETS=$(gsutil ls -p $PROJECT_ID | grep -E "gs://")
for BUCKET in $BUCKETS; do
  gsutil rm -r $BUCKET* || true
  gsutil rb $BUCKET || true
done

# Delete custom service accounts
echo "Deleting custom service accounts..."
SERVICE_ACCOUNTS=$(gcloud iam service-accounts list --project=$PROJECT_ID --format="value(email)" | grep -E "ingest-audio|transcribe-audio|match-customer|create-order")
for SA in $SERVICE_ACCOUNTS; do
  gcloud iam service-accounts delete $SA --quiet || true
done

# Delete local Terraform state
echo "Deleting local Terraform state..."
rm -rf cdktf.out/stacks/call-crm-pipeline/* || true

# Delete the Terraform state bucket (ct-toru-tfstate)
echo "Deleting Terraform state bucket..."
gsutil rm -r gs://ct-toru-tfstate/* || true
gsutil rb gs://ct-toru-tfstate || true

echo "All project-related resources have been deleted."