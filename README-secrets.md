# Google Secret Manager Integration

This document explains how Secret Manager is integrated with the CT Toru Cloud Functions.

## Overview

Google Secret Manager is used to securely store and access sensitive values like API keys, credentials, and API endpoints. This is more secure than storing these values directly as environment variables.

## Required Secrets

The following secrets need to be created in Google Secret Manager:

| Secret Name | Description | Used by Function(s) |
|-------------|-------------|-------------------|
| `ct-toru-call-center-api-key` | API key for call center system | ingest-audio |
| `ct-toru-call-center-api-url` | URL endpoint for call center API | ingest-audio |
| `ct-toru-crm-username` | Username for Bevira CRM | match-customer, create-order |
| `ct-toru-crm-password` | Password for Bevira CRM | match-customer, create-order |
| `ct-toru-crm-auth-url` | Authentication URL for Bevira CRM | match-customer, create-order |
| `ct-toru-crm-api-url` | API URL for customer matching | match-customer |
| `ct-toru-crm-create-order-url` | API URL for order creation | create-order |
| `LANGUAGE_CODE` | Language code for transcription | transcribe-audio |

## Creating Secrets

Create the required secrets using the Google Cloud Console or gcloud CLI:

```bash
# Example using gcloud
echo -n "your-api-key-here" | gcloud secrets create ct-toru-call-center-api-key --data-file=-
echo -n "https://88.196.158.219/api/recording/download/uniqueid" | gcloud secrets create ct-toru-call-center-api-url --data-file=-
echo -n "your-username-here" | gcloud secrets create ct-toru-crm-username --data-file=-
echo -n "your-password-here" | gcloud secrets create ct-toru-crm-password --data-file=-
echo -n "https://api.bevira.com/authenticate/" | gcloud secrets create ct-toru-crm-auth-url --data-file=-
echo -n "https://api.bevira.com/project/run/toruabi/aiconnect/findCustomer" | gcloud secrets create ct-toru-crm-api-url --data-file=-
echo -n "https://api.bevira.com/project/run/toruabi/aiconnect/createOrder" | gcloud secrets create ct-toru-crm-create-order-url --data-file=-
echo -n "et-EE" | gcloud secrets create LANGUAGE_CODE --data-file=-
```

## Accessing Secrets in Functions

Each function uses the Secret Manager client library to access secrets:

```python
from google.cloud import secretmanager

def access_secret(secret_id, version_id="latest"):
    """Access a secret from Google Secret Manager."""
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        raise Exception(f"Failed to access secret {secret_id}: {str(e)}")
```

## IAM Permissions

The service accounts used by Cloud Functions need the `secretmanager.secretAccessor` role to access secrets:

```typescript
// Add to FUNCTION_ROLES in main.ts
const FUNCTION_ROLES: { [key: string]: string[] } = {
    "ingest-audio": [
      "roles/storage.objectAdmin",
      "roles/secretmanager.secretAccessor" // Add this role
    ],
    // Add to other functions too...
};
```

## Environment Variables for Secret References

Instead of storing sensitive values directly, we reference the secret names in environment variables:

```typescript
// In main.ts
environmentVariables: {
  "BUCKET_NAME": "ct-toru-audio-input", 
  "PROJECT_ID": "ct-toru",
  "CALL_CENTER_API_KEY_SECRET": "ct-toru-call-center-api-key", // Secret reference
  "CALL_CENTER_API_URL_SECRET": "ct-toru-call-center-api-url" // URL secret reference
}
```

## Local Development

For local development and testing:

1. Create a `.env` file (don't commit to source control)
2. Add values directly for local testing only
3. Use the `python-dotenv` library to load them during development
4. When deployed to GCP, the functions will use Secret Manager instead

## Security Best Practices

1. Never commit secrets to source control
2. Use version control for secrets (Secret Manager supports versioning)
3. Audit secret access regularly
4. Follow the principle of least privilege for IAM roles
5. Store API endpoints as secrets to prevent exposing internal system structure
