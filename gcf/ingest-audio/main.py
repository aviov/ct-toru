import functions_framework
import requests
from google.cloud import storage
import os

@functions_framework.http
def main(request):
    try:
        # Configuration (replace with your actual API details)
        API_URL = "https://api.example.com/audio-files"
        API_KEY = os.getenv("API_KEY", "your-api-key")  # Set via environment variable
        BUCKET_NAME = "ct-toru-audio-input"

        # Fetch the list of audio files from the external API
        headers = {"Authorization": f"Bearer {API_KEY}"}
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes
        audio_files = response.json()

        # Initialize GCS client
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)

        # Download each audio file and upload to GCS
        for audio in audio_files:
            filename = audio["filename"]
            download_url = audio["download_url"]

            # Download the audio file
            print(f"Downloading {filename} from {download_url}")
            audio_response = requests.get(download_url, headers=headers)
            audio_response.raise_for_status()

            # Upload to GCS
            blob = bucket.blob(filename)
            blob.upload_from_string(audio_response.content, content_type="audio/mpeg")
            print(f"Uploaded {filename} to gs://{BUCKET_NAME}/{filename}")

        return "Audio files ingested successfully", 200
    except Exception as e:
        print(f"Error ingesting audio files: {str(e)}")
        return f"Error: {str(e)}", 500