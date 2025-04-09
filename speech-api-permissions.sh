#!/bin/bash
# Script to manually set up Speech API permissions for the transcribe-audio function

# Set your project ID
PROJECT_ID="ct-toru"
SERVICE_ACCOUNT="transcribe-audio@${PROJECT_ID}.iam.gserviceaccount.com"

# First, make sure the Speech API is enabled
echo "Enabling Speech API..."
gcloud services enable speech.googleapis.com --project=${PROJECT_ID}

# Grant Speech Client role to the service account
# This is different than the project-level IAM bindings
echo "Granting Speech Client role to ${SERVICE_ACCOUNT}..."
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/speech.client"

echo "Checking Service Account permissions..."
gcloud projects get-iam-policy ${PROJECT_ID} \
    --flatten="bindings[].members" \
    --format="table(bindings.role,bindings.members)" \
    --filter="bindings.members:${SERVICE_ACCOUNT}"

echo "Done! The service account should now have access to the Speech API."
echo "Next steps:"
echo "1. Run ./test-functions.sh to test the transcribe-audio function"
echo "2. Check logs for any remaining errors: gcloud logging read \"resource.type=cloud_function AND resource.labels.function_name=transcribe-audio AND severity>=ERROR\" --limit 10"
