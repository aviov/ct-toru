import functions_framework
import requests
from google.cloud import storage
from google.cloud import secretmanager
import os
import json

# Environment variables
CALL_CENTER_API_URL_SECRET = os.environ.get("CALL_CENTER_API_URL_SECRET", "ct-toru-call-center-api-url")
CALL_CENTER_API_KEY_SECRET = os.environ.get("CALL_CENTER_API_KEY_SECRET", "ct-toru-call-center-api-key")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "ct-toru-audio-input")
PROJECT_ID = os.environ.get("PROJECT_ID", "ct-toru")

# Validate required environment variables
for var in ["CALL_CENTER_API_URL_SECRET", "CALL_CENTER_API_KEY_SECRET"]:
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

@functions_framework.http
def main(request):
    """
    HTTP-triggered function that receives call details, fetches the call recording from the call center API,
    and uploads it to Google Cloud Storage.
    """
    try:
        # Parse the request JSON
        request_json = request.get_json(silent=True)
        if not request_json:
            return "Error: Request must contain JSON data", 400

        caller = request_json.get("caller")
        uniqueid = request_json.get("uniqueid")

        if not caller or not uniqueid:
            return "Error: 'caller' and 'uniqueid' are required in the request", 400

        # Get API key and URL from Secret Manager
        api_key = access_secret(CALL_CENTER_API_KEY_SECRET)
        api_url = access_secret(CALL_CENTER_API_URL_SECRET)

        # Construct the API URL to fetch the recording
        download_url = f"{api_url}/{uniqueid}"
        headers = {"Authorization": f"Bearer {api_key}"}

        # Download the audio file
        print(f"Downloading recording for uniqueid {uniqueid} from {download_url}")
        audio_response = requests.get(download_url, headers=headers)
        audio_response.raise_for_status()

        # Initialize GCS client
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)

        # Construct a unique filename using caller and uniqueid
        filename = f"{caller}_{uniqueid}.mp3"

        # Upload to GCS
        blob = bucket.blob(filename)
        if blob.exists():
            print(f"File {filename} already exists in bucket, skipping...")
            return f"File {filename} already exists", 200

        blob.upload_from_string(audio_response.content, content_type="audio/mpeg")
        print(f"Uploaded {filename} to gs://{BUCKET_NAME}/{filename}")

        return f"Audio file {filename} ingested successfully", 200
    except Exception as e:
        print(f"Error ingesting audio file: {str(e)}")
        return f"Error: {str(e)}", 500