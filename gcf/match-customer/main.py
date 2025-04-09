import functions_framework
import base64
import json
import os
import requests
from google.cloud import storage, pubsub_v1
from google.cloud import secretmanager

# Environment variables
CRM_AUTH_URL_SECRET = os.environ.get("CRM_AUTH_URL_SECRET", "ct-toru-crm-auth-url")
CRM_API_URL_SECRET = os.environ.get("CRM_API_URL_SECRET", "ct-toru-crm-api-url")
CRM_USERNAME_SECRET = os.environ.get("CRM_USERNAME_SECRET", "ct-toru-crm-username")
CRM_PASSWORD_SECRET = os.environ.get("CRM_PASSWORD_SECRET", "ct-toru-crm-password")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-customer-matched")
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "ct-toru-transcriptions")
PROJECT_ID = os.environ.get("PROJECT_ID", "ct-toru")

# Validate required environment variables
for var in ["CRM_AUTH_URL_SECRET", "CRM_API_URL_SECRET", "CRM_USERNAME_SECRET", "CRM_PASSWORD_SECRET"]:
    if not os.environ.get(var):
        raise ValueError(f"{var} environment variable is required")

def access_secret(secret_id, version_id="latest"):
    """Access a secret from Google Secret Manager."""
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        raise Exception(f"Failed to access secret {secret_id}: {str(e)}")

def get_jwt_token():
    """Authenticate with the Bevira CRM API and return a JWT token."""
    try:
        CRM_USERNAME = access_secret(CRM_USERNAME_SECRET)
        CRM_PASSWORD = access_secret(CRM_PASSWORD_SECRET)
        CRM_AUTH_URL = access_secret(CRM_AUTH_URL_SECRET)
        auth_payload = {
            "username": CRM_USERNAME,
            "password": CRM_PASSWORD
        }
        response = requests.post(CRM_AUTH_URL, json=auth_payload)
        response.raise_for_status()
        auth_data = response.json()
        return auth_data["jwt"]
    except Exception as e:
        raise Exception(f"Failed to authenticate with CRM: {str(e)}")

@functions_framework.cloud_event
def main(cloud_event):
    """
    Triggered by a GCS upload event in the ct-toru-transcriptions bucket.
    This function takes transcription data, matches it to a customer using the Bevira CRM API,
    and publishes the result.
    """
    try:
        # Extract bucket and file information from the Cloud Event
        data = cloud_event.data
        bucket_name = data["bucket"]
        file_name = data["name"]
        
        if not file_name.lower().endswith(".txt"):
            print(f"Skipping non-transcript file: {file_name}")
            return
        
        # Extract caller from filename (format: caller_uniqueid.txt)
        caller = file_name.split("_")[0] if "_" in file_name else "unknown"
        
        # Retrieve the transcript from GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        
        # Download the transcript
        transcript_content = blob.download_as_string().decode("utf-8")
        print(f"Retrieved transcript: {transcript_content[:100]}... (caller: {caller})")
        
        # Extract additional customer information from transcript
        import re
        email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        email_matches = email_pattern.findall(transcript_content)
        
        # Build search criteria for findCustomer
        lookup_criteria = {
            "phoneNumber": caller,  # Required field
            "email": email_matches[0] if email_matches else None
        }
        # Remove None values from lookupCriteria
        lookup_criteria = {k: v for k, v in lookup_criteria.items() if v is not None}
        
        # Authenticate with the CRM
        jwt_token = get_jwt_token()
        CRM_API_URL = access_secret(CRM_API_URL_SECRET)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt_token}"
        }
        
        # Call findCustomer endpoint
        payload = {
            "lookupCriteria": lookup_criteria,
            "customerType": "ETTEVÕTE"  # Assuming business customer; adjust as needed
        }
        print(f"Searching for customer with criteria: {payload}")
        response = requests.post(CRM_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        customer_response = response.json()
        
        # Handle the response
        if not customer_response.get("customerFound"):
            error_message = customer_response.get("message", "No customer found")
            raise Exception(f"Customer matching failed: {error_message}")
        
        customer_data = customer_response["customerDetails"]
        print(f"Customer matched: {customer_data}")
        
        # Save the customer match to GCS
        customer_match_file = file_name.replace(".txt", "_customer.json")
        match_bucket = storage_client.bucket(STORAGE_BUCKET)
        match_blob = match_bucket.blob(f"customer_matches/{customer_match_file}")
        match_blob.upload_from_string(json.dumps(customer_data))
        
        print(f"Customer match stored at: gs://{STORAGE_BUCKET}/customer_matches/{customer_match_file}")
        
        # Prepare message for Pub/Sub
        message_data = {
            "transcript_file": file_name,
            "customer_match_file": f"customer_matches/{customer_match_file}",
            "customer_id": customer_data["id"],
            "bucket": STORAGE_BUCKET,
            "caller": caller
        }
        
        # Publish to Pub/Sub
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC.split('/')[-1])
        message_bytes = json.dumps(message_data).encode("utf-8")
        publish_future = publisher.publish(topic_path, data=message_bytes)
        publish_future.result()
        
        print(f"Published customer match to {OUTPUT_TOPIC}")
        return "Customer matching completed successfully"
        
    except Exception as e:
        print(f"Error processing message: {str(e)}")
        raise e