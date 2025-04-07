import functions_framework
import base64
import json
from google.cloud import storage
import requests

@functions_framework.cloud_event
def main(cloud_event):
    try:
        # Extract Pub/Sub message
        pubsub_message = cloud_event.data["message"]
        message_data = base64.b64decode(pubsub_message["data"]).decode("utf-8")
        file_name, customer_id = message_data.split("|")
        print(f"Processing message: file_name={file_name}, customer_id={customer_id}")

        # Download the transcription from GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket("ct-toru-transcriptions")
        blob = bucket.blob(f"{file_name}.txt")
        transcription = blob.download_as_text()
        print(f"Transcription: {transcription}")

        # Construct the audio file URL
        audio_file_url = f"gs://ct-toru-audio-input/{file_name}"

        # Create the order in the CRM
        CRM_API_URL = "https://crm.example.com/orders"
        CRM_API_KEY = "your-crm-api-key"  # Set via environment variable if needed
        headers = {"Authorization": f"Bearer {CRM_API_KEY}"}
        order_data = {
            "customer_id": customer_id,
            "transcription": transcription,
            "audio_file": audio_file_url
        }
        response = requests.post(CRM_API_URL, json=order_data, headers=headers)
        response.raise_for_status()
        order = response.json()
        order_id = order.get("order_id")
        print(f"Created order: {order_id}")

        return "Order created successfully"
    except Exception as e:
        print(f"Error creating order: {str(e)}")
        raise e