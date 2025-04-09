import functions_framework
import base64
import json
import os
import requests
from google.cloud import storage, pubsub_v1
from google.cloud import secretmanager
from datetime import datetime, timezone

# Environment variables
CRM_AUTH_URL_SECRET = os.environ.get("CRM_AUTH_URL_SECRET", "ct-toru-crm-auth-url")
CRM_API_URL_SECRET = os.environ.get("CRM_API_URL_SECRET", "ct-toru-crm-create-order-url")
CRM_USERNAME_SECRET = os.environ.get("CRM_USERNAME_SECRET", "ct-toru-crm-username")
CRM_PASSWORD_SECRET = os.environ.get("CRM_PASSWORD_SECRET", "ct-toru-crm-password")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-order-created")
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "ct-toru-transcriptions")
PROJECT_ID = os.environ.get("PROJECT_ID", "ct-toru")

# Validate required environment variables
for var in ["CRM_AUTH_URL_SECRET", "CRM_API_URL_SECRET", "CRM_USERNAME_SECRET", "CRM_PASSWORD_SECRET"]:
    if not os.environ.get(var):
        raise ValueError(f"{var} environment variable is required")

# List of valid Toruabi services for workDetails.typeOfWork
TORUABI_SERVICES = [
    "Ummistuse likvideerimine",
    "Hooldustööd",
    "Santehnilised tööd",
    "Elektritööd",
    "Survepesu",
    "Gaasitööd",
    "Keevitustööd",
    "Kaameravaatlus",
    "Lekkeotsing gaasiga",
    "Ehitustööd",
    "Rasvapüüdja tühjendus kuni 4m3",
    "Muu",
    "Freesimistööd",
    "Majasiseste kanalisatsioonitrasside pesu",
    "Fekaalivedu (1 koorem = kuni 5 m3)",
    "Hinnapakkumise küsimine",
    "Väljakutse tasu"
]

# Keyword mappings for detecting typeOfWork from transcription
SERVICE_KEYWORDS = {
    "Ummistuse likvideerimine": ["ummistus", "ummistuse", "likvideerimine", "tõkke", "ummistunud"],
    "Hooldustööd": ["hooldus", "hooldustööd", "hoolduse", "korras", "kontroll"],
    "Santehnilised tööd": ["santehnilised", "santehnika", "torutööd", "toru", "veetoru"],
    "Elektritööd": ["elektritööd", "elekter", "elektri", "juhe", "vool"],
    "Survepesu": ["survepesu", "pesu", "surve", "puhastus"],
    "Gaasitööd": ["gaasitööd", "gaas", "gaasi"],
    "Keevitustööd": ["keevitustööd", "keevitus", "keevita"],
    "Kaameravaatlus": ["kaameravaatlus", "kaamera", "vaatlus", "inspektsioon"],
    "Lekkeotsing gaasiga": ["lekkeotsing", "leke", "gaasiga", "gaasileke"],
    "Ehitustööd": ["ehitustööd", "ehitus", "ehituse", "renoveerimine"],
    "Rasvapüüdja tühjendus kuni 4m3": ["rasvapüüdja", "tühjendus", "rasva", "4m3", "rasva tühjendus"],
    "Muu": ["muu", "teine", "misc", "other"],
    "Freesimistööd": ["freesimistööd", "freesimine", "frees"],
    "Majasiseste kanalisatsioonitrasside pesu": ["kanalisatsioonitrasside", "kanalisatsioon", "pesu", "majasiseste"],
    "Fekaalivedu (1 koorem = kuni 5 m3)": ["fekaalivedu", "fekaal", "koorem", "5 m3"],
    "Hinnapakkumise küsimine": ["hinnapakkumine", "hinnapakkumise", "pakkumine", "hind"],
    "Väljakutse tasu": ["väljakutse", "tasu", "teenustasu"]
}

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

def determine_type_of_work(transcription):
    """Determine the typeOfWork by matching keywords in the transcription."""
    transcription_lower = transcription.lower()
    best_match = None
    best_score = 0
    
    # Score each service based on keyword matches
    for service, keywords in SERVICE_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword.lower() in transcription_lower)
        if score > best_score:
            best_score = score
            best_match = service
    
    # Default to "Muu" if no match is found
    return best_match if best_match else "Muu"

@functions_framework.cloud_event
def main(cloud_event):
    """
    Triggered by a Pub/Sub message containing customer match information.
    This function creates an order in the Bevira CRM system based on the matched customer.
    """
    try:
        # Extract the Pub/Sub message
        data = cloud_event.data
        if "message" not in data:
            print("No message in event")
            return
        
        # Decode message data
        if "data" in data["message"]:
            message_data = base64.b64decode(data["message"]["data"]).decode("utf-8")
            payload = json.loads(message_data)
        else:
            print("No data in message")
            return
        
        print(f"Processing message: {payload}")
        
        # Extract customer match details
        customer_match_file = payload.get("customer_match_file")
        customer_id = payload.get("customer_id")
        caller = payload.get("caller")
        transcript_file = payload.get("transcript_file")
        bucket = payload.get("bucket")
        
        if not customer_match_file or not customer_id or not bucket:
            print("Missing required fields in message")
            return
        
        # Retrieve the customer data from GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket)
        blob = bucket.blob(customer_match_file)
        
        # Download the customer data
        customer_data = json.loads(blob.download_as_string().decode("utf-8"))
        print(f"Retrieved customer data for ID: {customer_id} (caller: {caller})")
        
        # Retrieve the transcription for additional context
        transcript_blob = bucket.blob(transcript_file)
        transcription = transcript_blob.download_as_string().decode("utf-8")
        
        # Authenticate with the CRM
        jwt_token = get_jwt_token()
        CRM_API_URL = access_secret(CRM_API_URL_SECRET)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt_token}"
        }
        
        # Determine the typeOfWork from the transcription
        type_of_work = determine_type_of_work(transcription)
        print(f"Determined typeOfWork: {type_of_work}")
        
        # Construct the order payload
        now = datetime.now(timezone.utc).isoformat()
        uniqueid = transcript_file.split("_")[1].replace(".txt", "") if "_" in transcript_file else "unknown"
        
        order_payload = {
            "customer": {
                "customerType": customer_data.get("customerType", "ETTEVÕTE"),
                "name": customer_data.get("name", "Unknown"),
                "id": customer_id,
                "isNewCustomer": False,  # Assuming existing customer since matched
                "contactPerson": {
                    "firstName": "Unknown",  # Extract from transcription or CRM if available
                    "lastName": "Unknown",
                    "phone": caller,
                    "email": customer_data.get("email")
                }
            },
            "order": {
                "date": now.split("T")[0],  # YYYY-MM-DD
                "plannedWorkDuration": {
                    "start": now,
                    "end": now  # Adjust as needed
                },
                "additionalTimeInfo": "Immediate processing from call"
            },
            "location": {
                "object": "Office",
                "address": {
                    "street": customer_data.get("address", {}).get("street", "Unknown"),
                    "city": "Tallinn",  # Default; adjust as needed
                    "postalCode": customer_data.get("address", {}).get("postalCode", "Unknown"),
                    "country": customer_data.get("address", {}).get("country", "EE")
                },
                "additionalInfo": "From automated call processing"
            },
            "workDetails": {
                "description": transcription[:500],  # Truncate if too long
                "typeOfWork": "Service Request",  # Adjust based on transcription
                "problem": "Customer request from call",
                "additionalNotes": "Processed via AI pipeline"
            },
            "contact": {
                "name": customer_data.get("name", "Unknown"),
                "phone": caller,
                "role": "Caller"
            },
            "payment": {
                "method": "Invoice",
                "terms": "30 days"
            },
            "metadata": {
                "callId": uniqueid,
                "callTimestamp": now,
                "transcriptionTimestamp": now
            }
        }
        
        # Call createOrder endpoint
        print(f"Creating order for customer ID: {customer_id}")
        response = requests.post(CRM_API_URL, json=order_payload, headers=headers)
        response.raise_for_status()
        order_response = response.json()
        
        # Handle the response
        if not order_response.get("success"):
            error_code = order_response.get("errorCode")
            error_message = order_response.get("message", "Unknown error")
            raise Exception(f"Order creation failed: {error_code} - {error_message}")
        
        order_id = order_response["orderId"]
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "caller_phone": caller,
            "order_payload": order_payload,
            "created_at": now
        }
        
        # Save the order to GCS
        order_bucket = storage_client.bucket(STORAGE_BUCKET)
        order_blob = order_bucket.blob(f"orders/{order_id}.json")
        order_blob.upload_from_string(json.dumps(order))
        
        print(f"Order created and stored at: gs://{STORAGE_BUCKET}/orders/{order_id}.json")
        
        # Publish confirmation message
        message_data = {
            "order_id": order_id,
            "customer_id": customer_id,
            "caller": caller,
            "status": "created"
        }
        
        # Publish to Pub/Sub
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC.split('/')[-1])
        message_bytes = json.dumps(message_data).encode("utf-8")
        publish_future = publisher.publish(topic_path, data=message_bytes)
        publish_future.result()
        
        print(f"Published order confirmation to {OUTPUT_TOPIC}")
        return "Order created successfully"
        
    except Exception as e:
        print(f"Error creating order: {str(e)}")
        raise e