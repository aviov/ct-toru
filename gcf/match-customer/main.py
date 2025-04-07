import functions_framework
import requests
from google.cloud import storage
from google.cloud import pubsub_v1

@functions_framework.cloud_event
def main(cloud_event):
    try:
        # Extract event data
        data = cloud_event.data
        bucket_name = data["bucket"]
        file_name = data["name"]
        print(f"Processing transcription: gs://{bucket_name}/{file_name}")

        # Download the transcription from GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        transcription = blob.download_as_text()
        print(f"Transcription: {transcription}")

        # Search for the customer in the CRM (replace with actual CRM API)
        CRM_API_URL = "https://crm.example.com/customers/search"
        CRM_API_KEY = "your-crm-api-key"  # Set via environment variable if needed
        headers = {"Authorization": f"Bearer {CRM_API_KEY}"}
        response = requests.post(CRM_API_URL, json={"query": transcription}, headers=headers)
        response.raise_for_status()
        customer = response.json()
        customer_id = customer.get("customer_id")
        print(f"Matched customer: {customer_id}")

        if not customer_id:
            raise ValueError("No customer matched for the transcription")

        # Publish to Pub/Sub
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path("ct-toru", "ct-toru-customer-matched")
        message = f"{file_name}|{customer_id}"
        publisher.publish(topic_path, message.encode("utf-8"))
        print(f"Published message to {topic_path}: {message}")

        return "Customer matched successfully"
    except Exception as e:
        print(f"Error matching customer: {str(e)}")
        raise e