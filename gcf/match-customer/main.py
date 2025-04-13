import base64
import json
import re
import os
import time
from typing import Dict, Any, List, Optional

import functions_framework
import requests
from google.cloud import storage, secretmanager, pubsub_v1

# Environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "ct-toru-transcriptions")
CRM_AUTH_URL_SECRET = os.environ.get("CRM_AUTH_URL_SECRET", "ct-toru-crm-auth-url")
CRM_USERNAME_SECRET = os.environ.get("CRM_USERNAME_SECRET", "ct-toru-crm-username")
CRM_PASSWORD_SECRET = os.environ.get("CRM_PASSWORD_SECRET", "ct-toru-crm-password")
CRM_API_URL_SECRET = os.environ.get("CRM_API_URL_SECRET", "ct-toru-crm-api-url")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-customer-matched")
OPENAI_API_KEY_SECRET_ID = os.environ.get("OPENAI_API_KEY_SECRET_ID", "ct-toru-openai-api-key")

def access_secret(secret_id, version_id="latest"):
    """
    Access the secret from Secret Manager.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def get_jwt_token():
    """
    Authenticate with the CRM API and get a JWT token.
    Returns the token as a string.
    """
    try:
        # Get secrets from Secret Manager
        CRM_USERNAME = access_secret(CRM_USERNAME_SECRET).strip()
        CRM_PASSWORD = access_secret(CRM_PASSWORD_SECRET).strip()
        CRM_AUTH_URL = access_secret(CRM_AUTH_URL_SECRET).strip()
        
        # Ensure URL ends with trailing slash for consistent concatenation
        if not CRM_AUTH_URL.endswith('/'):
            CRM_AUTH_URL += '/'
        
        # Prepare the authentication payload with correct parameter names
        auth_payload = {
            "clientId": CRM_USERNAME,
            "clientSecret": CRM_PASSWORD
        }
        
        # Make the authentication request
        response = requests.post(CRM_AUTH_URL, json=auth_payload)
        
        # Raise an HTTPError if the response was unsuccessful
        response.raise_for_status()
        
        # Parse the JSON response
        auth_data = response.json()
        
        # Extract and return the token
        if "jwt" in auth_data:
            return auth_data["jwt"]
        else:
            raise Exception(f"Auth response does not contain jwt field. Fields found: {list(auth_data.keys())}")
    except requests.exceptions.RequestException as e:
        # Handle request exceptions with more context
        error_detail = ""
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = f", Response: {e.response.json()}"
            except:
                error_detail = f", Response text: {e.response.text[:200]}"
                
        raise Exception(f"Failed to authenticate with CRM: {str(e)}{error_detail}")
    except Exception as e:
        raise Exception(f"Failed to authenticate with CRM: {str(e)}")

def extract_customer_info_with_openai(transcript_content: str) -> Dict[str, Any]:
    """
    Extract customer information from transcript using OpenAI's API.
    Returns a dictionary of extracted information.
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
        
        # Define prompt for customer information extraction
        extraction_prompt = (
            "You are an expert in analyzing Estonian customer service call transcripts. "
            "Extract the following customer information from this phone call transcript. "
            "Only extract information that is explicitly mentioned in the transcript. "
            "If the information is not present, reply with null for that field. "
            "\n\n"
            f"Transcript: {transcript_content}\n\n"
            "Return ONLY a JSON object with these exact fields:\n"
            "{\n"
            '  "phoneNumber": "extracted phone number",\n'
            '  "name": "customer contact person name",\n'
            '  "companyName": "company name",\n'
            '  "companyRegCode": "company registration code if mentioned",\n'
            '  "email": "email address if mentioned",\n'
            '  "customerType": "either ERAKLIENT (if personal customer) or ETTEVÕTE (if business customer)"\n'
            "}\n\n"
            "IMPORTANT: Only include these exact fields. Do not add any additional fields, explanations, or text. "
            "If information is not present in the transcript, use null for that field. "
            "For customerType, determine whether the caller is a personal customer (ERAKLIENT) or business customer (ETTEVÕTE) "
            "based on context clues like whether they mention a company name, use formal business language, "
            "or identify themselves as a company representative."
        )
        
        # Prepare the request payload
        payload = {
            "model": "gpt-4o",  # Use the most capable model
            "messages": [
                {"role": "system", "content": "You are a helpful assistant specializing in information extraction."},
                {"role": "user", "content": extraction_prompt}
            ],
            "temperature": 0.0,  # Use zero temperature for deterministic outputs
            "max_tokens": 500  # Limit response size
        }
        
        # Set up retry parameters
        MAX_RETRIES = 3
        RETRY_DELAY = 2  # seconds
        
        # Try to make the request with retries
        for attempt in range(MAX_RETRIES):
            try:
                print(f"Attempt {attempt+1} to extract customer info with OpenAI API")
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
                    
                    # Clean the response - remove markdown formatting that might be in the response
                    extracted_text = re.sub(r'```json|```', '', extracted_text).strip()
                    
                    # Parse the JSON
                    extracted_data = json.loads(extracted_text)
                    
                    # Filter out None/null values
                    filtered_data = {k: v for k, v in extracted_data.items() if v is not None and v != "null"}
                    
                    print(f"OpenAI successfully extracted: {filtered_data}")
                    return filtered_data
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
        
        # If we get here, all retries failed
        print("Failed to extract customer info after multiple attempts")
        return {}
    
    except Exception as e:
        print(f"Error in OpenAI extraction: {str(e)}")
        return {}

def extract_customer_info_with_regex(transcript_content: str) -> Dict[str, str]:
    """
    Extract customer information from transcript using regex patterns.
    """
    # Extract email addresses
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    email_matches = email_pattern.findall(transcript_content)
    
    # Extract Estonian company names (typically ending with OÜ, AS, etc.)
    company_pattern = re.compile(r'\b([A-Za-zÕÄÖÜõäöü\s-]+(?:OÜ|AS|MTÜ|TÜ|FIE|UÜ|TüH))\b')
    company_matches = company_pattern.findall(transcript_content)
    
    # Extract addresses - looking for typical Estonian address patterns
    address_pattern = re.compile(r'\b([A-Za-zÕÄÖÜõäöü\s-]+\s+(?:tee|tn|puiestee|pst|tänav|maantee)\s+\d+(?:[,\s]+[A-Za-zÕÄÖÜõäöü\s-]+)?)\b')
    address_matches = address_pattern.findall(transcript_content)
    
    # Extract potential person names - names typically preceded by words indicating a person
    name_indicators = ['nimi', 'on', 'mina olen', 'helistab', 'kontakt']
    name_pattern = re.compile(r'(?:' + '|'.join(name_indicators) + r')\s+(?:on\s+)?([A-Za-zÕÄÖÜõäöü]{2,}(?:\s+[A-Za-zÕÄÖÜõäöü]{2,})?)')
    name_matches = name_pattern.findall(transcript_content)
    
    # Extract phone numbers - various Estonian formats
    phone_pattern = re.compile(r'\b(?:\+372[- ]?|8[- ]?)?(?:\d{3,4}[- ]?\d{3,4}|\d{7,8})\b')
    phone_matches = phone_pattern.findall(transcript_content)
    
    # Determine customer type based on keywords and patterns
    company_indicators = ['firma', 'ettevõte', 'ettevõtte', 'äriühing', 'organisatsioon', 'OÜ', 'AS', 'FIE', 'registrikood']
    personal_indicators = ['eraklient', 'erakliendid', 'eraisik', 'kodune', 'kodus', 'korter', 'korterisse', 'pere', 'isiklik']
    
    # Count indicators for both types
    company_count = sum(1 for indicator in company_indicators if indicator.lower() in transcript_content.lower())
    personal_count = sum(1 for indicator in personal_indicators if indicator.lower() in transcript_content.lower())
    
    # Default to ETTEVÕTE if company is mentioned, otherwise ERAKLIENT
    customer_type = "ERAKLIENT"
    if company_count > personal_count or company_matches:
        customer_type = "ETTEVÕTE"
    
    # Log extracted information
    print(f"Regex extracted emails: {email_matches}")
    print(f"Regex extracted companies: {company_matches}")
    print(f"Regex extracted addresses: {address_matches}")
    print(f"Regex extracted names: {name_matches}")
    print(f"Regex extracted phones: {phone_matches}")
    print(f"Regex determined customer type: {customer_type}")
    
    # Determine the best phone number to use
    best_phone = None  # Don't default to caller
    
    # If we have extracted phones
    if phone_matches:
        # Clean up the first extracted phone (remove spaces, dashes)
        clean_phone = re.sub(r'[^0-9+]', '', phone_matches[0])
        if len(clean_phone) >= 5:  # Ensure it's a reasonably long number
            best_phone = clean_phone
    
    # Build results dictionary
    results = {}
    
    if best_phone:
        results["phoneNumber"] = best_phone
        
    if email_matches:
        results["email"] = email_matches[0]
    
    if company_matches:
        # Clean up company name (remove leading/trailing whitespace, normalize spaces)
        company_name = re.sub(r'\s+', ' ', company_matches[0].strip())
        results["companyName"] = company_name
    
    # Try to extract a company registration code (typically in format 12345678)
    reg_code_pattern = re.compile(r'\b\d{8}\b')
    reg_code_matches = reg_code_pattern.findall(transcript_content)
    if reg_code_matches:
        results["companyRegCode"] = reg_code_matches[0]
    
    # For the "name" field, use the contact person's name if available
    if name_matches:
        # Clean up name (remove leading/trailing whitespace, normalize spaces)
        contact_name = re.sub(r'\s+', ' ', name_matches[0].strip())
        results["name"] = contact_name
    
    # Add customer type
    results["customerType"] = customer_type
    
    return results

@functions_framework.cloud_event
def main(cloud_event):
    """
    Triggered by a Pub/Sub message from the ct-toru-order-confirmed topic.
    This function takes transcription data, matches it to a customer using the Bevira CRM API,
    and publishes the result.
    """
    try:
        # Extract message data from the Cloud Event
        data = cloud_event.data
        
        # For Pub/Sub events, the data is base64 encoded
        if "message" in data:
            message = data["message"]
            if "data" in message:
                message_data = json.loads(base64.b64decode(message["data"]).decode("utf-8"))
                print(f"Received message: {json.dumps(message_data)}")
                
                # Extract fields from the message
                bucket_name = message_data["bucket"]
                file_name = message_data["transcript_file"]
                caller = message_data["caller"]
                transcript_content = message_data["transcript"]
                
                print(f"Processing transcript file: {file_name} from bucket: {bucket_name} (caller: {caller})")
            else:
                raise Exception("No data field in Pub/Sub message")
        else:
            raise Exception("Not a valid Pub/Sub message")
        
        # First try regex-based extraction
        regex_results = extract_customer_info_with_regex(transcript_content)
        
        # If the USE_LLM environment variable is set to "true", also try OpenAI extraction
        use_llm = os.environ.get("USE_LLM", "false").lower() == "true"
        
        # Determine if we should use LLM extraction (either as primary or fallback)
        llm_data = {}
        if use_llm:
            llm_data = extract_customer_info_with_openai(transcript_content)
        
        # Combine or choose between the two approaches based on configuration
        llm_primary = os.environ.get("LLM_PRIMARY", "false").lower() == "true"
        
        if llm_primary and llm_data:
            # Use LLM data with regex as fallback for missing fields
            lookup_criteria = {**regex_results, **llm_data}
            print("Using OpenAI as primary extraction method with regex fallback")
        else:
            # Use regex data with LLM as fallback for missing fields
            lookup_criteria = {**llm_data, **regex_results}
            print("Using regex as primary extraction method with OpenAI fallback")
        
        # Always ensure we have a phone number
        if "phoneNumber" not in lookup_criteria and caller != "test":
            lookup_criteria["phoneNumber"] = caller
        
        # Log the final criteria
        print(f"Final lookup criteria after combining extraction methods: {lookup_criteria}")
        
        # If we have caller ID as "test" but no actual phone number was found, we can't proceed
        if "phoneNumber" not in lookup_criteria or lookup_criteria["phoneNumber"] == "test":
            print("No valid phone number found in transcript and caller ID is 'test'")
            # Optionally continue without phone number, but it's required by the API
        
        # Remove None values from lookupCriteria
        lookup_criteria = {k: v for k, v in lookup_criteria.items() if v is not None}
        
        # Create the full payload with the required customerType
        payload = {
            "lookupCriteria": lookup_criteria,
            "customerType": lookup_criteria.get("customerType", "ETTEVÕTE")  # Use extracted type or default to ETTEVÕTE
        }
        
        # Authenticate with the CRM
        jwt_token = get_jwt_token()
        CRM_API_URL = access_secret(CRM_API_URL_SECRET).strip()  # Strip any whitespace/newlines
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {jwt_token}"
        }
        
        # Prepare variations for retry logic
        phone_variations = []
        company_variations = []
        
        # Generate phone number variations to handle transcription errors
        if "phoneNumber" in lookup_criteria:
            original_phone = lookup_criteria["phoneNumber"]
            clean_phone = re.sub(r'[^0-9+]', '', original_phone)
            
            # Only generate variations if phone number is long enough
            if len(clean_phone) > 6:
                # Remove first digit (might be misheard)
                phone_variations.append(clean_phone[1:])
                # Remove last digit (might be cut off)
                phone_variations.append(clean_phone[:-1])
                # If it starts with country code (+372), try without it
                if clean_phone.startswith("+372"):
                    phone_variations.append(clean_phone[4:])
                # If it doesn't have country code, try adding Estonian code
                elif not clean_phone.startswith("+"):
                    phone_variations.append("+372" + clean_phone)
            
            print(f"Generated phone variations for retry: {phone_variations}")
        
        # Generate company name variations to handle transcription errors
        if "companyName" in lookup_criteria:
            original_company = lookup_criteria["companyName"]
            
            # Remove short words (< 3 chars)
            words = original_company.split()
            filtered_words = [word for word in words if len(word) >= 3]
            if len(filtered_words) < len(words):
                company_variations.append(" ".join(filtered_words))
            
            # Remove legal entity suffix (OÜ, AS, etc.)
            legal_suffixes = ["OÜ", "AS", "MTÜ", "TÜ", "FIE"]
            for suffix in legal_suffixes:
                if original_company.endswith(suffix):
                    company_variations.append(original_company[:-len(suffix)].strip())
                    break
            
            print(f"Generated company name variations for retry: {company_variations}")
        
        # Define a function to try a single API call with given criteria
        def try_customer_lookup(search_criteria):
            retry_payload = {
                "lookupCriteria": search_criteria,
                "customerType": payload["customerType"]
            }
            print(f"Trying customer lookup with: {retry_payload}")
            
            retry_response = requests.post(CRM_API_URL, json=retry_payload, headers=headers)
            
            # Log minimal response info
            print(f"Search response status: {retry_response.status_code}")
            
            if retry_response.status_code == 200:
                try:
                    retry_result = retry_response.json()
                    # Check if customer was found
                    if "customerFound" not in retry_result or retry_result["customerFound"]:
                        print("Successful match found with modified criteria!")
                        return retry_response, retry_result
                except:
                    pass
            
            return None, None
        
        # Try the original criteria first
        print(f"Searching for customer with criteria: {payload}")
        response = requests.post(CRM_API_URL, json=payload, headers=headers)
        
        # Log the response details for debugging
        print(f"Customer search response status: {response.status_code}")
        print(f"Customer search response headers: {dict(response.headers)}")
        
        try:
            customer_response = response.json()
            print(f"Customer search response body: {customer_response}")
        except:
            print(f"Customer search response text: {response.text[:200]}")
            raise Exception("Failed to parse customer search response as JSON")
        
        # Check if we need to try variations (if no customer found or error)
        if response.status_code != 200 or "customerFound" in customer_response and not customer_response["customerFound"]:
            print("Initial search failed, trying variations...")
            
            # Try phone variations
            for phone_var in phone_variations:
                retry_criteria = lookup_criteria.copy()
                retry_criteria["phoneNumber"] = phone_var
                
                retry_response, retry_result = try_customer_lookup(retry_criteria)
                if retry_response:
                    response = retry_response
                    customer_response = retry_result
                    break
            
            # If still not found, try company variations
            if (response.status_code != 200 or "customerFound" in customer_response and not customer_response["customerFound"]) and company_variations:
                for company_var in company_variations:
                    retry_criteria = lookup_criteria.copy()
                    retry_criteria["companyName"] = company_var
                    
                    retry_response, retry_result = try_customer_lookup(retry_criteria)
                    if retry_response:
                        response = retry_response
                        customer_response = retry_result
                        break
        
        # Now raise for status to handle error codes that weren't resolved through retries
        response.raise_for_status()
        
        # Handle the response - check what fields are actually in the response
        print(f"Available keys in customer response: {list(customer_response.keys())}")
        
        # Adjust field checking based on actual API response structure
        if "customerFound" in customer_response and not customer_response["customerFound"]:
            error_message = customer_response.get("message", "No customer found")
            raise Exception(f"Customer matching failed: {error_message}")
        
        # Try different possible field names for customer details
        customer_data = None
        possible_keys = ["customerDetails", "customer", "customerData", "data", "result"]
        
        for key in possible_keys:
            if key in customer_response:
                customer_data = customer_response[key]
                print(f"Found customer data under key: {key}")
                break
        
        # If customer_data is still None, check if the response itself is the customer data
        if customer_data is None:
            # Check if response has typical customer fields
            if any(field in customer_response for field in ["id", "name", "email", "phone"]):
                customer_data = customer_response
                print("Using entire response as customer data")
            else:
                # As a last resort, use the entire response
                print("Warning: Could not identify customer data structure, using entire response")
                customer_data = customer_response
        
        print(f"Customer matched: {customer_data}")
        
        # Ensure customer_data has an id field for later use
        if "id" not in customer_data:
            # Generate a placeholder ID if needed
            customer_data["id"] = f"unknown-{caller}"
            print(f"Warning: No customer ID found, using placeholder: {customer_data['id']}")
        
        # Save the customer match to GCS
        customer_match_file = file_name.replace(".txt", "_customer.json")
        if "/" in customer_match_file:
            # Extract just the filename if it contains a path
            customer_match_file = customer_match_file.split("/")[-1]
            
        # Include the extraction data in the output for downstream processing
        output_data = {
            "customerDetails": customer_data,
            "id": customer_data.get("id", f"unknown-{caller}"),
            "openai_extraction": llm_data,  # Include OpenAI extraction results
            "lookup_criteria": lookup_criteria,  # Include the combined extraction criteria
            "customerFound": "customerFound" in customer_response and customer_response["customerFound"]
        }
            
        storage_client = storage.Client()
        match_bucket = storage_client.bucket(STORAGE_BUCKET)
        match_blob = match_bucket.blob(f"customer_matches/{customer_match_file}")
        match_blob.upload_from_string(json.dumps(output_data))
        
        print(f"Customer match stored at: gs://{STORAGE_BUCKET}/customer_matches/{customer_match_file}")
        
        # Prepare message for Pub/Sub
        output_message_data = {
            "transcript_file": file_name,
            "customer_match_file": f"customer_matches/{customer_match_file}",
            "customer_id": customer_data["id"],
            "bucket": STORAGE_BUCKET,
            "caller": caller
        }
        
        # Publish to Pub/Sub
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC.split('/')[-1])
        message_bytes = json.dumps(output_message_data).encode("utf-8")
        publish_future = publisher.publish(topic_path, data=message_bytes)
        publish_future.result()
        
        print(f"Published customer match to {OUTPUT_TOPIC}")
        return "Customer matching completed successfully"
        
    except Exception as e:
        print(f"Error processing message: {str(e)}")
        raise e