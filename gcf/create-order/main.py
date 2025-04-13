import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import uuid # only for test endpoint

import functions_framework
import requests
from google.cloud import storage, secretmanager, pubsub_v1
from flask import Flask, request, jsonify # only for test endpoint

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
USE_TEST_ENDPOINT = os.environ.get("USE_TEST_ENDPOINT", "false").lower() == "true"

# Initialize Flask app for test endpoint if needed, only for test endpoint
test_app = Flask(__name__)
# Store orders in memory for testing, only for test endpoint
test_orders = {}

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

# Test endpoint route, only for test endpoint
@test_app.route('/test-create-order', methods=['POST'])
def test_create_order():
    """Mock endpoint for testing order creation"""
    try:
        # Get the order payload from the request
        payload = request.json
        
        # Generate a random order ID
        order_id = str(uuid.uuid4())[:8]
        
        # Store the order in our test database
        test_orders[order_id] = {
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "created"
        }
        
        # Log the order creation
        print(f"TEST ENDPOINT: Created order with ID {order_id}")
        
        # Return a success response
        return jsonify({
            "success": True,
            "orderId": order_id,
            "message": "Order created successfully in test environment"
        })
    except Exception as e:
        # Return an error response
        return jsonify({
            "success": False,
            "errorCode": "TEST_ERROR",
            "message": str(e)
        }), 500

# Test endpoint function, only for test endpoint
def call_test_endpoint(payload, headers):
    """Call the test endpoint instead of the real CRM API"""
    try:
        print("Using TEST ENDPOINT for order creation")
        
        # If running in Cloud Functions, use a direct HTTP request to avoid starting a server
        if 'FUNCTION_TARGET' in os.environ:
            # Create a mock successful response
            mock_response = requests.Response()
            mock_response.status_code = 200
            mock_response._content = json.dumps({
                "success": True,
                "orderId": f"test-{str(uuid.uuid4())[:8]}",
                "message": "Order created successfully in test environment"
            }).encode('utf-8')
            return mock_response
        else:
            # For local testing, actually spin up a test server
            # This would be more useful for local development
            test_port = int(os.environ.get("TEST_PORT", "8080"))
            
            # Start the test server in a separate thread if not already running
            import threading
            if not hasattr(call_test_endpoint, "server_thread"):
                def run_test_server():
                    test_app.run(host='127.0.0.1', port=test_port)
                
                call_test_endpoint.server_thread = threading.Thread(target=run_test_server)
                call_test_endpoint.server_thread.daemon = True
                call_test_endpoint.server_thread.start()
                print(f"Started test server on port {test_port}")
                time.sleep(1)  # Give the server time to start
            
            # Make request to the test endpoint
            response = requests.post(
                f"http://127.0.0.1:{test_port}/test-create-order",
                json=payload,
                headers=headers,
                timeout=5
            )
            return response
    except Exception as e:
        # If something goes wrong, return a fake response
        print(f"Error using test endpoint: {str(e)}")
        mock_response = requests.Response()
        mock_response.status_code = 500
        mock_response._content = json.dumps({
            "success": False,
            "errorCode": "TEST_ERROR",
            "message": f"Error in test endpoint: {str(e)}"
        }).encode('utf-8')
        return mock_response

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

def determine_type_of_work_with_openai(transcription: str) -> dict:
    """
    Determine the type of work and extract detailed order information using OpenAI API.
    Returns a dictionary with typeOfWork and detailed order information.
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
        
        # Define prompt for work type determination
        type_prompt = (
            "You are an expert in understanding Estonian plumbing and maintenance service requests. "
            "Analyze the following transcript of a call to a plumbing company (Toruabi) and extract detailed information.\n\n"
            f"Transcript: {transcription}\n\n"
            "Return a JSON object with the following information:\n"
            "1. \"typeOfWork\": The most appropriate type of work from this list: " + 
            ", ".join([f"\"{service}\"" for service in TORUABI_SERVICES]) + "\n"
            "2. \"companyInfo\": Any company or business names mentioned in the call\n"
            "3. \"maintenanceType\": Whether this is a one-time job or periodic/recurring maintenance\n"
            "4. \"specificIssue\": The specific issue or task that needs to be addressed\n"
            "5. \"preferredTechnician\": Name of any specific technician requested by the customer\n"
            "6. \"timePreference\": Any time preferences mentioned (specific hours, days, time windows)\n"
            "7. \"locationDetails\": Any details about the specific location within the property\n"
            "8. \"accessInstructions\": Any special instructions for accessing the property\n"
            "9. \"contractStatus\": Whether the customer mentioned being under contract or not\n"
            "10. \"customerRole\": The role of the person calling (manager, owner, receptionist, etc.)\n\n"
            "Respond ONLY with the JSON object containing these fields, with NO additional text or explanation."
        )
        
        # Prepare the request payload
        payload = {
            "model": "gpt-4o",  # Use latest model for best results
            "messages": [
                {"role": "system", "content": "You are a helpful assistant specializing in extracting structured information."},
                {"role": "user", "content": type_prompt}
            ],
            "temperature": 0.0,  # Use zero temperature for deterministic outputs
            "max_tokens":
            1000  # Response could be longer now with all details
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
                    extracted_text = result["choices"][0]["message"]["content"].strip()
                    
                    # Clean the response if needed
                    extracted_text = re.sub(r'```json|```', '', extracted_text).strip()
                    
                    # Parse the JSON
                    extracted_data = json.loads(extracted_text)
                    
                    # Validate typeOfWork against valid services
                    type_of_work = extracted_data.get("typeOfWork")
                    if type_of_work and type_of_work in TORUABI_SERVICES:
                        print(f"OpenAI determined type of work: {type_of_work}")
                        return extracted_data
                    else:
                        print(f"OpenAI returned invalid type of work: {type_of_work}")
                        return None
                else:
                    print(f"OpenAI API error: {response.status_code} - {response.text}")
                    
            except requests.exceptions.RequestException as e:
                print(f"OpenAI API request failed: {str(e)}")
                
            except json.JSONDecodeError as e:
                print(f"Failed to parse OpenAI response as JSON: {str(e)}")
                
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

def determine_type_of_work(transcription: str) -> tuple:
    """
    Determine the type of work using both OpenAI and keyword-based approaches.
    The method used is based on environment variables USE_LLM and LLM_PRIMARY.
    Returns a tuple of (type_of_work, extracted_details) where extracted_details may be None
    """
    # Always try the keyword-based approach
    keyword_match = determine_type_of_work_with_keywords(transcription)
    print(f"Keyword-based work type: {keyword_match}")
    
    # If OpenAI is enabled, try that too
    openai_result = None
    extracted_details = None
    
    if USE_LLM:
        openai_result = determine_type_of_work_with_openai(transcription)
        if openai_result:
            openai_match = openai_result.get("typeOfWork")
            extracted_details = openai_result
            print(f"OpenAI-based work type: {openai_match}")
    
    # Determine which result to use based on configuration
    if LLM_PRIMARY and openai_result and openai_result.get("typeOfWork"):
        print("Using OpenAI as primary method for work type determination")
        return openai_result.get("typeOfWork"), extracted_details
    else:
        return keyword_match, extracted_details

def extract_contact_details(transcription, customer_data, caller):
    """
    Extract contact person details from transcription and customer data
    """
    # Default values
    contact_info = {
        "firstName": "Unknown",
        "lastName": "Unknown",
        "phone": caller,
        "email": customer_data.get("email")
    }
    
    # Get customer_match_data if available from previous extraction
    customer_match_file = customer_data.get("_customer_match_file")
    if customer_match_file:
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(customer_data.get("_bucket", STORAGE_BUCKET))
            match_blob = bucket.blob(customer_match_file)
            match_data_raw = match_blob.download_as_string().decode("utf-8")
            match_data = json.loads(match_data_raw)
            
            # Debug: Print match data for inspection
            print(f"Customer match data sample: {match_data_raw[:200]}...")
            
            # Extract name from various possible locations in the match data
            extracted_name = None
            
            # Get data from openai_extraction field
            if "openai_extraction" in match_data:
                openai_data = match_data["openai_extraction"]
                print(f"Found OpenAI extraction data: {openai_data}")
                
                # Handle both string and dict formats
                if isinstance(openai_data, str):
                    try:
                        openai_data = json.loads(openai_data)
                    except:
                        print("OpenAI extraction is string but not valid JSON")
                
                # Get name from OpenAI extraction
                if isinstance(openai_data, dict) and "name" in openai_data:
                    extracted_name = openai_data["name"]
                    print(f"Found name in openai_extraction: {extracted_name}")
                    
                # Also try to get phone from OpenAI extraction
                if isinstance(openai_data, dict) and "phoneNumber" in openai_data:
                    phone = openai_data["phoneNumber"]
                    if phone and phone != "test":
                        clean_phone = re.sub(r'[^0-9+]', '', phone)
                        if len(clean_phone) >= 5:
                            contact_info["phone"] = clean_phone
                            print(f"Using phone from OpenAI extraction: {contact_info['phone']}")
            
            # Fallback to other locations if no name found
            if not extracted_name:
                # Try lookup_criteria
                if match_data.get("lookup_criteria", {}).get("name"):
                    extracted_name = match_data.get("lookup_criteria", {}).get("name")
                    print(f"Found name in lookup_criteria: {extracted_name}")
                
                # Try direct in customer data (old format)
                elif match_data.get("name"):
                    extracted_name = match_data.get("name")
                    print(f"Found name in top level: {extracted_name}")
            
            # If we found a valid name, use it
            if extracted_name and extracted_name not in ["test", "Unknown"]:
                # Ensure the name is not a company name
                if (" OÜ" in extracted_name or " AS" in extracted_name or 
                    extracted_name.endswith("OÜ") or extracted_name.endswith("AS")):
                    print(f"Ignoring company name as person: {extracted_name}")
                else:
                    if " " in extracted_name:
                        name_parts = extracted_name.split(" ", 1)
                        contact_info["firstName"] = name_parts[0]
                        contact_info["lastName"] = name_parts[1] if len(name_parts) > 1 else ""
                    else:
                        contact_info["firstName"] = extracted_name
                    print(f"Using extracted name: firstName={contact_info['firstName']}, lastName={contact_info['lastName']}")

        except Exception as e:
            print(f"Error retrieving match data: {e}")
    
    # Never use company name as person name
    company_name = customer_data.get("name", "")
    if contact_info["firstName"] == company_name or "OÜ" in contact_info["firstName"] or "AS" in contact_info["firstName"]:
        contact_info["firstName"] = "Unknown"
        contact_info["lastName"] = ""
    
    # Look for phone numbers in the transcript
    phone_pattern = re.compile(r'\b(?:\+372[- ]?|8[- ]?)?(?:\d{3,4}[- ]?\d{3,4}|\d{7,8})\b')
    phone_matches = phone_pattern.findall(transcription)
    
    if phone_matches:
        # Clean up the first extracted phone (remove spaces, dashes)
        clean_phone = re.sub(r'[^0-9+]', '', phone_matches[0])
        if len(clean_phone) >= 5:  # Ensure it's a reasonably long number
            contact_info["phone"] = clean_phone
    
    # Look for email addresses in the transcript
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    email_matches = email_pattern.findall(transcription)
    
    if email_matches:
        contact_info["email"] = email_matches[0]
    
    # Look for names in the transcript
    name_indicators = ['mina olen', 'nimi on', 'helistab', 'kontakt']
    for indicator in name_indicators:
        if indicator in transcription.lower():
            # Look for the name after the indicator
            pattern = re.compile(f'{indicator}\\s+([A-Za-zÕÄÖÜõäöü]+(?:\\s+[A-Za-zÕÄÖÜõäöü]+)?)', re.IGNORECASE)
            matches = pattern.findall(transcription)
            if matches:
                name = matches[0].strip()
                if " " in name:
                    name_parts = name.split(" ", 1)
                    contact_info["firstName"] = name_parts[0]
                    contact_info["lastName"] = name_parts[1] if len(name_parts) > 1 else ""
                else:
                    contact_info["firstName"] = name
    
    return contact_info

def extract_time_preferences(transcription):
    """Extract preferred time windows or specific times from the transcript"""
    time_info = "Immediate processing from call"
    
    # Common time-related phrases in Estonian
    today_indicators = ['täna', 'tänane päev', 'tänaseks']
    tomorrow_indicators = ['homme', 'homne päev', 'homseks']
    
    # Time windows
    morning_indicators = ['hommikul', 'hommikuks', 'hommikune', '8-12', '8 ja 12', '8 kuni 12']
    afternoon_indicators = ['päeval', 'lõuna ajal', 'lõunaks', '12-16', '12 ja 16', '12 kuni 16']
    evening_indicators = ['õhtul', 'õhtuks', 'õhtune', '16-20', '16 ja 20', '16 kuni 20']
    
    # Check for time windows
    if any(indicator in transcription.lower() for indicator in morning_indicators):
        if any(indicator in transcription.lower() for indicator in tomorrow_indicators):
            time_info = "Tomorrow morning (8-12)"
        elif any(indicator in transcription.lower() for indicator in today_indicators):
            time_info = "Today morning (8-12)"
        else:
            time_info = "Morning hours (8-12)"
    elif any(indicator in transcription.lower() for indicator in afternoon_indicators):
        if any(indicator in transcription.lower() for indicator in tomorrow_indicators):
            time_info = "Tomorrow afternoon (12-16)"
        elif any(indicator in transcription.lower() for indicator in today_indicators):
            time_info = "Today afternoon (12-16)"
        else:
            time_info = "Afternoon hours (12-16)"
    elif any(indicator in transcription.lower() for indicator in evening_indicators):
        if any(indicator in transcription.lower() for indicator in tomorrow_indicators):
            time_info = "Tomorrow evening (16-20)"
        elif any(indicator in transcription.lower() for indicator in today_indicators):
            time_info = "Today evening (16-20)"
        else:
            time_info = "Evening hours (16-20)"
    elif any(indicator in transcription.lower() for indicator in tomorrow_indicators):
        time_info = "Tomorrow, time not specified"
    elif any(indicator in transcription.lower() for indicator in today_indicators):
        time_info = "Today, time not specified"
    
    # Look for specific hour patterns
    hour_pattern = re.compile(r'kell\s+(\d{1,2})(?:\s*(?:ja|kuni|-)\s*(\d{1,2}))?', re.IGNORECASE)
    hour_matches = hour_pattern.findall(transcription)
    
    if hour_matches:
        start_hour = hour_matches[0][0]
        end_hour = hour_matches[0][1] if len(hour_matches[0]) > 1 and hour_matches[0][1] else None
        
        if end_hour:
            time_window = f"{start_hour}-{end_hour}"
            if any(indicator in transcription.lower() for indicator in tomorrow_indicators):
                time_info = f"Tomorrow between {time_window}"
            elif any(indicator in transcription.lower() for indicator in today_indicators):
                time_info = f"Today between {time_window}"
            else:
                time_info = f"Between {time_window}"
        else:
            if any(indicator in transcription.lower() for indicator in tomorrow_indicators):
                time_info = f"Tomorrow at {start_hour}"
            elif any(indicator in transcription.lower() for indicator in today_indicators):
                time_info = f"Today at {start_hour}"
            else:
                time_info = f"At {start_hour}"
    
    return time_info

def extract_access_instructions(transcription):
    """Extract specific access instructions or notes from the transcript"""
    access_info = ""
    
    # Look for access-related keywords in Estonian
    access_indicators = [
        'võti on', 'võtke võti', 'kood on', 'ukse kood', 'sissepääsu kood', 
        'valve all', 'valvur', 'administraator', 'reception', 'vastuvõtt',
        'uksekell', 'helista', 'registratuur', 'signalisatsioon'
    ]
    
    for indicator in access_indicators:
        if indicator in transcription.lower():
            # Extract the sentence containing the access info
            sentences = re.split(r'[.!?]+', transcription)
            for sentence in sentences:
                if indicator in sentence.lower():
                    access_info = sentence.strip() + ". "
                    break
    
    return access_info.strip()

def extract_technician_preference(transcription):
    """Extract preferred technician name from the transcript"""
    technician_name = ""
    
    # Look for technician name indicators in Estonian
    technician_indicators = [
        'saatke', 'tuleks', 'tehnik', 'meister', 'spetsialist',
        'meesterahvas', 'mees', 'naine', 'sama inimene', 'sama tehnik',
        'eelmine kord käis'
    ]
    
    for indicator in technician_indicators:
        if indicator in transcription.lower():
            # Look for names near these indicators
            pattern = re.compile(r'(?:' + indicator + r')\s+([A-Za-zÕÄÖÜõäöü]+)', re.IGNORECASE)
            matches = pattern.findall(transcription)
            if matches:
                technician_name = matches[0].strip()
                # Verify it looks like a name (starts with capital letter, has reasonable length)
                if technician_name[0].isupper() and 3 <= len(technician_name) <= 20:
                    return technician_name
            
            # Also look before the indicator
            pattern = re.compile(r'([A-Za-zÕÄÖÜõäöü]+)\s+(?:' + indicator + r')', re.IGNORECASE)
            matches = pattern.findall(transcription)
            if matches:
                technician_name = matches[0].strip()
                # Verify it looks like a name
                if technician_name[0].isupper() and 3 <= len(technician_name) <= 20:
                    return technician_name
    
    return technician_name

def generate_order_summary(transcription, customer_data, type_of_work, caller, extracted_details=None):
    """
    Generate a concise summary with key order information
    Uses OpenAI extracted details if available, falls back to regex-based extraction
    """
    # Use OpenAI extracted details if available
    if extracted_details and USE_LLM:
        # Extract key information from the OpenAI result
        company_info = extracted_details.get("companyInfo", "")
        maintenance_type = extracted_details.get("maintenanceType", "")
        specific_issue = extracted_details.get("specificIssue", "")
        preferred_technician = extracted_details.get("preferredTechnician", "")
        time_preference = extracted_details.get("timePreference", "")
        location_details = extracted_details.get("locationDetails", "")
        access_instructions = extracted_details.get("accessInstructions", "")
        contract_status = extracted_details.get("contractStatus", "")
        customer_role = extracted_details.get("customerRole", "")
        
        # Get basic customer info
        company_name = customer_data.get("name", "").replace(" OÜ", "").replace(" AS", "")
        if company_info and company_info != company_name:
            company_name = f"{company_name} ({company_info})"
        
        # Address info
        address = customer_data.get("address", {}).get("street", "Address unknown")
        
        # Work details
        work_type = type_of_work
        recurring_note = ""
        if maintenance_type and "periodi" in maintenance_type.lower():
            recurring_note = ", perioodiline hooldus"
        elif "periodi" in specific_issue.lower() or "regulaar" in specific_issue.lower():
            recurring_note = ", perioodiline hooldus"
        elif "aeg-ajalt" in transcription.lower() or "aeg ajalt" in transcription.lower():
            recurring_note = ", perioodiline hooldus"
        
        # Contract info - directly check the transcription to avoid misinterpretation
        contract_text = ""
        if "ei ole lepinguline" in transcription.lower() or "lepinguline ei ole" in transcription.lower() or "lepinguline otseselt ei ole" in transcription.lower():
            contract_text = "mitte lepinguline"
        elif contract_status:
            if "ei" in contract_status.lower() or "mitte" in contract_status.lower() or "pole" in contract_status.lower():
                contract_text = "mitte lepinguline"
            else:
                contract_text = "lepinguline"
        else:
            # Fallback to regex
            is_contract = "lepinguline" in transcription.lower() and not ("ei ole lepinguline" in transcription.lower() or "lepinguline ei ole" in transcription.lower() or "lepinguline otseselt ei ole" in transcription.lower())
            contract_text = "lepinguline" if is_contract else "mitte lepinguline"
        
        # Technician preference
        technician_note = ""
        if preferred_technician:
            technician_note = f", palub {preferred_technician} tuleks"
        
        # Contact details from extracted data or fallback
        contact_details = extract_contact_details(transcription, customer_data, caller)
        contact_name = contact_details.get("firstName") != "Unknown" and (contact_details.get("firstName", "") + " " + contact_details.get("lastName", "").strip()).strip() or customer_data.get("name", "Unknown")
        if contact_name == "Unknown" or contact_name == company_name or "OÜ" in contact_name or "AS" in contact_name:
            contact_name = ""
        contact_phone = contact_details.get("phone", caller)
        
        # Generate summary
        summary = f"{company_name} {work_type}"
        
        # Add specific issue if available
        if specific_issue:
            summary += f", {specific_issue}"
        
        # Add recurring note if applicable
        summary += recurring_note
        
        # Add address and contract status
        summary += f", {address}, {contract_text}"
        
        # Add location details if available
        if location_details:
            summary += f", {location_details}"
        
        # Add technician preference
        summary += technician_note
        
        # Add time preference
        if time_preference:
            summary += f", {time_preference}"
        else:
            # Fallback to regex extraction
            regex_time = extract_time_preferences(transcription)
            if regex_time != "Immediate processing from call":
                summary += f", {regex_time}"
        
        # Add contact info
        if contact_name:
            role_text = f" ({customer_role})" if customer_role else ""
            summary += f", kontakt on {contact_name}{role_text}"
        summary += f", tel {contact_phone}"
        
        # Add access instructions if any
        if access_instructions:
            summary += f", {access_instructions}"
        
        # Add billing info indicator
        summary += f", arvesaaja {company_name}"
        
        return summary
    
    # Fallback to regex-based extraction if OpenAI details not available
    else:
        # Customer info
        company_name = customer_data.get("name", "").replace(" OÜ", "").replace(" AS", "")
        
        # Address info
        address = customer_data.get("address", {}).get("street", "Address unknown")
        
        # Extract work details
        work_type = type_of_work
        
        # Extract time preferences
        time_preference = extract_time_preferences(transcription)
        
        # Extract additional instructions
        access_instructions = extract_access_instructions(transcription)
        
        # Extract technician preference
        technician = extract_technician_preference(transcription)
        technician_note = f", palub {technician} tuleks" if technician else ""
        
        # Extract contact details
        contact_details = extract_contact_details(transcription, customer_data, caller)
        contact_name = contact_details.get("firstName", "")
        contact_phone = contact_details.get("phone", caller)
        
        # Look for contract status
        is_contract = "lepinguline" in transcription.lower() and not ("ei ole lepinguline" in transcription.lower() or "lepinguline ei ole" in transcription.lower() or "lepinguline otseselt ei ole" in transcription.lower())
        contract_status = "lepinguline" if is_contract else "mitte lepinguline"
        
        # Extract recurring service indicators
        recurring_indicators = ['perioodiline', 'regulaarne', 'iga nädal', 'iga kuu', 'hooldus', 'aeg-ajalt', 'kord kuus']
        is_recurring = any(indicator in transcription.lower() for indicator in recurring_indicators)
        recurring_note = ", perioodiline hooldus" if is_recurring else ""
        
        # Generate summary
        summary = f"{company_name} {work_type}{recurring_note}, {address}, {contract_status}{technician_note}, {time_preference}"
        
        # Add contact info
        if contact_name:
            summary += f", kontakt on {contact_name}"
        summary += f", tel {contact_phone}"
        
        # Add access instructions if any
        if access_instructions:
            summary += f", {access_instructions}"
        
        # Add billing info indicator
        summary += f", arvesaaja {company_name}"
        
        return summary

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
        bucket_name = payload.get("bucket")
        customer_match_file = payload.get("customer_match_file")
        customer_id = payload.get("customer_id")
        caller = payload.get("caller")
        transcript_file = payload.get("transcript_file")
        
        if not customer_match_file or not customer_id or not bucket_name:
            print("Missing required fields in message")
            return
        
        # Setup storage client
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        
        # Load the customer match data
        customer_match_blob = bucket.blob(customer_match_file)
        customer_match_raw = customer_match_blob.download_as_string().decode("utf-8")
        customer_match = json.loads(customer_match_raw)
        customer_id = customer_match.get("id")
        customer_data = customer_match.get("customerDetails", {})
        
        # Add source file info to customer_data for reference
        customer_data["_customer_match_file"] = customer_match_file
        customer_data["_bucket"] = bucket_name
        
        # Print the first part of the match data for debugging
        print(f"Customer match raw data sample: {customer_match_raw[:200]}...")
        
        # Check for OpenAI extraction result specifically
        if "openai_extraction" in customer_match:
            openai_extraction = customer_match["openai_extraction"]
            # It could be a string or already parsed JSON
            if isinstance(openai_extraction, str):
                try:
                    openai_extraction = json.loads(openai_extraction)
                    print(f"Parsed OpenAI extraction: {json.dumps(openai_extraction)}")
                except:
                    print(f"OpenAI extraction as string: {openai_extraction}")
            else:
                print(f"OpenAI extraction as object: {json.dumps(openai_extraction)}")
                
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
        type_of_work, extracted_details = determine_type_of_work(transcription)
        print(f"Final determined typeOfWork: {type_of_work}")
        
        # Generate order summary with key information
        order_summary = generate_order_summary(transcription, customer_data, type_of_work, caller, extracted_details)
        print(f"Order summary: {order_summary}")
        
        # Extract contact details from transcription and customer data
        contact_details = extract_contact_details(transcription, customer_data, caller)
        
        # Extract time preferences
        time_preference = extract_time_preferences(transcription)
        
        # Construct the order payload
        now = datetime.now(timezone.utc)
        # Convert to Tallinn timezone (UTC+3)
        tallinn_offset = "+03:00"
        # Format timestamps according to API docs: YYYY-MM-DDThh:mm:ss+03:00
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S") + tallinn_offset
        date_str = now.strftime("%Y-%m-%d")  # Format as "YYYY-MM-DD"
        uniqueid = transcript_file.split("_")[1].replace(".txt", "") if "_" in transcript_file else "unknown"
        
        # Get city from customer data or default to Tallinn
        city = "Tallinn"
        if customer_data.get("address", {}).get("city"):
            # Extract just the city name before any commas for consistency
            full_city = customer_data.get("address", {}).get("city", "")
            city = full_city.split(",")[0].strip()
        
        order_payload = {
            "customer": {
                "customerType": customer_data.get("customerType", "ETTEVÕTE"),
                "name": customer_data.get("name", "Unknown"),
                "id": customer_id,
                "isNewCustomer": False,  # Assuming existing customer since matched
                "contactPerson": contact_details
            },
            "order": {
                "date": date_str,  # YYYY-MM-DD
                "plannedWorkDuration": {
                    "start": now_str,
                    "end": now_str  # Adjust as needed
                },
                "additionalTimeInfo": time_preference
            },
            "location": {
                "object": customer_data.get("name", "Unknown"),
                "address": {
                    "street": customer_data.get("address", {}).get("street", "Unknown"),
                    "city": city,
                    "postalCode": customer_data.get("address", {}).get("postalCode", "Unknown"),
                    "country": customer_data.get("address", {}).get("country", "EE")
                },
                "additionalInfo": extract_access_instructions(transcription) or "From automated call processing"
            },
            "workDetails": {
                "description": transcription[:500],  # Truncate if too long
                "typeOfWork": type_of_work,
                "problem": order_summary[:100],  # Use first part of summary as problem description
                "additionalNotes": order_summary  # Use full summary as additional notes
            },
            "contact": {
                "name": contact_details.get("firstName") != "Unknown" and (contact_details.get("firstName", "") + " " + contact_details.get("lastName", "").strip()).strip() or customer_data.get("name", "Unknown"),
                "phone": contact_details.get("phone", caller),
                "role": extracted_details.get("customerRole", "Contact Person") if extracted_details and USE_LLM else "Contact Person"
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
            if USE_TEST_ENDPOINT:
                response = call_test_endpoint(order_payload, headers) # only for test endpoint
            else:
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