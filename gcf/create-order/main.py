import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import functions_framework
import requests
from google.cloud import storage, secretmanager, pubsub_v1

# Environment variables
CRM_AUTH_URL_SECRET = os.environ.get("CRM_AUTH_URL_SECRET", "ct-toru-crm-auth-url")
CRM_API_URL_SECRET = os.environ.get("CRM_API_URL_SECRET", "ct-toru-crm-create-order-url")
CRM_USERNAME_SECRET = os.environ.get("CRM_USERNAME_SECRET", "ct-toru-crm-username")
CRM_PASSWORD_SECRET = os.environ.get("CRM_PASSWORD_SECRET", "ct-toru-crm-password")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-order-created")
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "ct-toru-transcriptions")
PROJECT_ID = os.environ.get("PROJECT_ID", "ct-toru")
OPENAI_API_KEY_SECRET_ID = os.environ.get("OPENAI_API_KEY_SECRET_ID", "ct-toru-openai-api-key")
USE_LLM = os.environ.get("USE_LLM", "false").lower() == "true"
LLM_PRIMARY = os.environ.get("LLM_PRIMARY", "false").lower() == "true"

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
        CRM_USERNAME = access_secret(CRM_USERNAME_SECRET).strip()
        CRM_PASSWORD = access_secret(CRM_PASSWORD_SECRET).strip()
        CRM_AUTH_URL = access_secret(CRM_AUTH_URL_SECRET).strip()
        auth_payload = {
            "clientId": CRM_USERNAME,
            "clientSecret": CRM_PASSWORD
        }
        response = requests.post(CRM_AUTH_URL, json=auth_payload)
        response.raise_for_status()
        auth_data = response.json()
        return auth_data["jwt"]
    except Exception as e:
        raise Exception(f"Failed to authenticate with CRM: {str(e)}")

def determine_type_of_work_with_openai(transcription: str) -> str:
    """
    Determine the type of work by analyzing the transcription with OpenAI.
    Returns the best matching service type from TORUABI_SERVICES.
    """
    try:
        # Get OpenAI API key from Secret Manager
        api_key = access_secret(OPENAI_API_KEY_SECRET_ID).strip()
        
        # Define OpenAI API endpoint
        OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
        
        # Set up headers with API key
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Create a formatted list of all valid service types
        services_list = "\n".join([f"- {service}" for service in TORUABI_SERVICES])
        
        # Define prompt for work type determination
        extraction_prompt = (
            "You are an expert in analyzing Estonian plumbing and service call transcripts. "
            "Determine which type of service is being requested based on this transcript. "
            "Match it to one of these exact service types:\n\n"
            f"{services_list}\n\n"
            f"Transcript: {transcription}\n\n"
            "Return ONLY the matching service type. Choose the most appropriate one - "
            "if you're not completely sure, make your best guess from the available options. "
            "If no service type seems to match at all, return 'Muu'. "
            "Return only one of the exact service names listed above with no additional text."
        )
        
        # Prepare the request payload
        payload = {
            "model": "gpt-4o",  # Use the most capable model
            "messages": [
                {"role": "system", "content": "You are a helpful assistant specializing in information extraction."},
                {"role": "user", "content": extraction_prompt}
            ],
            "temperature": 0.0,  # Use zero temperature for deterministic outputs
            "max_tokens": 50  # Limit response size
        }
        
        # Set up retry parameters
        MAX_RETRIES = 3
        RETRY_DELAY = 2  # seconds
        
        # Try to make the request with retries
        for attempt in range(MAX_RETRIES):
            try:
                print(f"Attempt {attempt+1} to determine work type with OpenAI API")
                response = requests.post(
                    OPENAI_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                
                # Check if the request was successful
                if response.status_code == 200:
                    result = response.json()
                    work_type = result["choices"][0]["message"]["content"].strip()
                    
                    # Clean up the response (remove quotes and other formatting)
                    work_type = re.sub(r'["\']', '', work_type).strip()
                    
                    # Validate that the response is one of the valid service types
                    if work_type in TORUABI_SERVICES:
                        print(f"OpenAI determined work type: {work_type}")
                        return work_type
                    else:
                        print(f"OpenAI returned invalid work type: '{work_type}', falling back to default")
                        return "Muu"  # Fallback if the response isn't a valid type
                else:
                    print(f"OpenAI API error: {response.status_code} - {response.text}")
                    
            except requests.exceptions.RequestException as e:
                print(f"OpenAI API request failed: {str(e)}")
                
            # Wait before retrying, with exponential backoff
            if attempt < MAX_RETRIES - 1:
                sleep_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                print(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
        
        # If all retries failed, return None to fall back to keyword-based method
        print("Failed to determine work type after multiple attempts")
        return None
    
    except Exception as e:
        print(f"Error in OpenAI work type determination: {str(e)}")
        return None

def determine_type_of_work_with_keywords(transcription: str) -> str:
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

def determine_type_of_work(transcription: str) -> str:
    """
    Determine the type of work using both OpenAI and keyword-based approaches.
    The method used is based on environment variables USE_LLM and LLM_PRIMARY.
    """
    # Always try the keyword-based approach
    keyword_match = determine_type_of_work_with_keywords(transcription)
    print(f"Keyword-based work type: {keyword_match}")
    
    # If OpenAI is enabled, try that too
    openai_match = None
    if USE_LLM:
        openai_match = determine_type_of_work_with_openai(transcription)
        if openai_match:
            print(f"OpenAI-based work type: {openai_match}")
    
    # Determine which result to use based on configuration
    if LLM_PRIMARY and openai_match:
        print("Using OpenAI as primary method for work type determination")
        return openai_match
    else:
        return keyword_match

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
        CRM_API_URL = access_secret(CRM_API_URL_SECRET).strip()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt_token}"
        }
        
        # Determine the typeOfWork from the transcription
        type_of_work = determine_type_of_work(transcription)
        print(f"Final determined typeOfWork: {type_of_work}")
        
        # Construct the order payload
        now = datetime.now(timezone.utc)
        # Convert to Tallinn timezone (UTC+3)
        tallinn_offset = "+03:00"
        # Format timestamps according to API docs: YYYY-MM-DDThh:mm:ss+03:00
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S") + tallinn_offset
        date_str = now.strftime("%Y-%m-%d")  # Format as "YYYY-MM-DD"
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
                "date": date_str,  # YYYY-MM-DD
                "plannedWorkDuration": {
                    "start": now_str,
                    "end": now_str  # Adjust as needed
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
                "typeOfWork": type_of_work,  # Adjust based on transcription
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
                "callTimestamp": now_str,
                "transcriptionTimestamp": now_str
            }
        }
        
        # Call createOrder endpoint
        print(f"Creating order for customer ID: {customer_id}")
        try:
            # Debug output to see exactly what we're sending
            print(f"Order payload: {json.dumps(order_payload)}")
            print(f"Making request to: {CRM_API_URL}")
            print(f"Headers: Authorization: Bearer <token hidden>, Content-Type: {headers['Content-Type']}")
            
            # Make the API request with more detailed error handling
            response = requests.post(CRM_API_URL, json=order_payload, headers=headers, timeout=30)
            
            # Log the response status and content for debugging
            print(f"Response status code: {response.status_code}")
            print(f"Response content: {response.text[:500]}")  # Limit to first 500 chars
            
            # Check for non-200 responses
            if response.status_code != 200:
                print(f"API error: {response.status_code} - {response.text}")
                return f"Order creation failed with status {response.status_code}"
            
            # Parse the response as JSON
            try:
                order_response = response.json()
            except ValueError as json_err:
                print(f"Failed to parse response as JSON: {str(json_err)}")
                return "Order creation failed: Invalid response format"
            
            # Handle the response
            if not order_response.get("success"):
                error_code = order_response.get("errorCode", "unknown")
                error_message = order_response.get("message", "Unknown error")
                print(f"Order creation failed: {error_code} - {error_message}")
                return f"Order creation failed: {error_code} - {error_message}"
            
            order_id = order_response["orderId"]
            order = {
                "order_id": order_id,
                "customer_id": customer_id,
                "caller_phone": caller,
                "order_payload": order_payload,
                "created_at": now_str
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
        except requests.exceptions.RequestException as req_err:
            print(f"Request error: {str(req_err)}")
            return f"Order creation request failed: {str(req_err)}"
        except Exception as e:
            print(f"Error creating order: {str(e)}")
            return f"Order creation failed: {str(e)}"
        
    except Exception as e:
        print(f"Error creating order: {str(e)}")
        raise e