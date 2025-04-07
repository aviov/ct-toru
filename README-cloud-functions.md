# Google Cloud Functions Implementation Guide

This document provides an overview of the Google Cloud Functions used in the CT Toru project.

## Functions Overview

The system consists of four Google Cloud Functions that work together in a pipeline:

1. **Ingest Audio**: HTTP-triggered function that fetches audio files from an external API and uploads them to Google Cloud Storage.
2. **Transcribe Audio**: GCS-triggered function that transcribes audio files using Google's Speech-to-Text API.
3. **Match Customer**: Pub/Sub-triggered function that analyzes transcriptions to match customers in the CRM system.
4. **Create Order**: Pub/Sub-triggered function that creates orders based on customer data.

## Environment Variables

Each function requires specific environment variables, which are set during deployment through CDKTF. The `.env.example` files in each function directory document the required variables.

### Setting Environment Variables

Environment variables are set in the CDKTF stack via the `environmentVariables` property in the `GcfFunction` construct.

**Important**: For sensitive values like API keys, use Google Cloud Secret Manager in production environments rather than directly setting them as environment variables.

## Function Details

### Ingest Audio

**Trigger**: HTTP

**Environment Variables**:
- `API_URL`: URL of the external API for audio files
- `API_KEY`: API key for authentication
- `BUCKET_NAME`: GCS bucket to store audio files

**Purpose**: Fetches audio files from an external API and uploads them to GCS, triggering the transcription process.

### Transcribe Audio

**Trigger**: GCS event (new file in audio input bucket)

**Environment Variables**:
- `INPUT_BUCKET`: GCS bucket containing audio files
- `OUTPUT_TOPIC`: Pub/Sub topic to publish transcription results
- `LANGUAGE_CODE`: Language code for transcription (e.g., "en-US")

**Purpose**: Uses Google's Speech-to-Text API to transcribe audio files and publishes the results to a Pub/Sub topic.

### Match Customer

**Trigger**: Pub/Sub message (transcription complete)

**Environment Variables**:
- `CUSTOMER_API_URL`: URL of the customer API
- `CUSTOMER_API_KEY`: API key for customer service
- `OUTPUT_TOPIC`: Pub/Sub topic to publish customer match results
- `STORAGE_BUCKET`: GCS bucket to store customer match data

**Purpose**: Analyzes transcriptions to extract customer information and matches it with customer records.

### Create Order

**Trigger**: Pub/Sub message (customer matched)

**Environment Variables**:
- `ORDER_API_URL`: URL of the order API
- `ORDER_API_KEY`: API key for order service
- `OUTPUT_TOPIC`: Pub/Sub topic for order confirmation
- `STORAGE_BUCKET`: GCS bucket to store order data

**Purpose**: Creates orders in the system based on matched customer data.

## IAM Roles and Permissions

Each function requires specific IAM roles, which are defined in the CDKTF stack:

```typescript
const FUNCTION_ROLES: { [key: string]: string[] } = {
    "ingest-audio": [
      "roles/storage.objectAdmin" // Upload MP3s to GCS
    ],
    "transcribe-audio": [
      "roles/storage.objectViewer", // Read MP3s, write transcriptions
      "roles/pubsub.publisher", // Publish to order-confirmed
      "roles/speech.user" // Use Speech-to-Text
    ],
    "match-customer": [
      "roles/storage.objectViewer", // Read transcriptions, write customer matches
      "roles/pubsub.subscriber", // Subscribe to order-confirmed
      "roles/pubsub.publisher" // Publish to customer-matched
    ],
    "create-order": [
      "roles/storage.objectViewer", // Read customer matches, write orders
      "roles/pubsub.subscriber" // Subscribe to customer-matched
    ]
};
```

## Testing the Functions

To test the deployed functions, use the `test-functions.sh` script, which tests both HTTP and event-driven triggers.

## Deployment

Functions are deployed using CDKTF:

```bash
cd cdktf
npm run deploy
```

This will deploy all functions with their respective triggers, IAM roles, and environment variables.

## Troubleshooting

- **HTTP 403 errors**: Check that IAM roles are properly assigned and that the service account has the `roles/cloudfunctions.invoker` permission.
- **Missing environment variables**: Verify environment variables are correctly set in the CDKTF configuration.
- **Function not triggering**: Confirm that triggers (GCS buckets or Pub/Sub topics) are correctly configured.
