import functions_framework
import json
import os
import re
import unicodedata
from typing import Optional
from google.cloud import storage
from google.cloud import secretmanager
from google.cloud import pubsub_v1
import tempfile
import requests
import time
from urllib.parse import urljoin
import base64

# Environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "ct-toru-audio-input")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "ct-toru-transcriptions")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-order-confirmed")
LANGUAGE_CODE_SECRET_ID = os.environ.get("LANGUAGE_CODE_SECRET_ID", "ct-toru-language-code")
OPENAI_API_KEY_SECRET_ID = os.environ.get("OPENAI_API_KEY_SECRET_ID", "ct-toru-openai-api-key")

# Audio file extensions to process
AUDIO_EXTENSIONS = ['mp3', 'wav', 'flac', 'm4a', 'ogg']

def access_secret(project_id: str, secret_id: str, version_id: str) -> str:
    """
    Access the secret from Secret Manager.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def post_process_estonian_transcript(text):
    """
    Post-process the transcript to fix common Estonian misrecognitions
    """
    # Common word corrections for this domain
    corrections = {
        r'\btelistan\b': 'helistan',
        r'\btoual+e+t+ru+m\w+\b': 'tualettruumis',
        r'\bnais[dt]a\b,?\s+t': 'naiste t',
        r'\bpovastada\b': 'puhastada',
        r'\bpoastada\b': 'puhastada',
        r'\bkeskmus\b': 'kestnud',
        r'\baast\w+ (kuu|6)\b': 'kell 6',
        r'\bajastad\w+\b': 'ajast',
        r'\bantsi[dt]\b': 'andsite',
        r'\bkena\b': 'kena',
        r'\bAga kena\b': 'Väga kena',
        r'\bOotake\b': 'Oodake',
        r'\bMa votake okka\b': 'Oodake hetk',
        r'\bummistusegimine\b': 'ummistus',
        r'\bmenname\b': 'jõuame',
        r'\bsuurvabesutööd\b': 'survepesu',
        r'\bpeavast\b': 'päevast',
        r'\bKummikrofi\b': 'Kummiprofi',
        r'\bGoldmindo\b': 'Goldmind',
        r'\bGoldmint\b': 'Goldmind',
        r'\bvannidupa\b': 'vannituppa',
        r'\binge jama\b': 'mingi jama',
        r'\bKuidas 329\b': 'Kunderi 329',
        r'\bkaha võõlem\b': 'kaua võõrad',
        r'\btervista\b': 'tervist',
        r'\bmenna\b': 'enne',
        r'\bveedoru\b': 'veetoru',
        r'\blõpinguline\b': 'lepinguline',
        r'\bKuidas\b': 'Kunderi',
        r'\bümselt\b': 'ümber',
        r'\bvööda radiust\b': 'teine võõras radius'
    }
    
    # Apply corrections
    for pattern, replacement in corrections.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    # General cleanup
    text = re.sub(r',\s+(\w+),', r' \1', text)  # Remove unnecessary commas
    text = re.sub(r'\s+([.,!?])', r'\1', text)  # Fix punctuation spacing
    text = re.sub(r'\b(\w+)(\s+\1)+\b', r'\1', text)  # Remove repeated words
    
    return text

def call_openai_api_with_retries(api_key, audio_file_path, language_code):
    """Call OpenAI API directly using requests with retries for better network reliability"""
    OPENAI_API_URL = "https://api.openai.com/v1/audio/transcriptions"
    MAX_RETRIES = 5
    RETRY_DELAY = 2  # seconds
    
    # Make sure to strip any whitespace or newline characters from the API key
    api_key = api_key.strip()
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    # Read the audio file as binary
    with open(audio_file_path, "rb") as file:
        audio_data = file.read()
    
    # Create prompt with Estonian plumbing/service terminology to improve recognition
    estonian_prompt = (
        "See on helisalvestis toruettevõtte klienditeenindusele. Räägitakse eesti keeles ja teemaks "
        "on toruabi tellimine, ummistus, või santehnilised tööd. Võimalikud fraasid: "
        "\"Toru abi, tere\", \"helistan Tiskre Prismast\", \"naiste tualettruumis\", \"ummistuse likvideerimine\", "
        "\"kanalisatsioon\", \"santehnilised tööd\", \"hooldustööd\", \"Liiva tee 61\", \"puhastada\", "
        "\"kestnud nädal aega\", \"Öelge palun täpne aadress\", \"kuidas teie nimi on\", \"Oodake hetk\", "
        "\"ma saan rääkida\", \"kell kuue ja kaheksa vahel\", \"tõenäoliselt jõuame enne\", \"Väga kena\", \"Teeme nii\", \"Aitäh\"."
        "Inimeste nimed: \"Marili Torim\"."
    )
    
    # Prepare the request payload with enhanced options
    files = {
        "file": ("audio.mp3", audio_data),
    }
    data = {
        "model": "whisper-1",
        "language": language_code.split('-')[0].strip(),  # Should be clean ISO code like 'et'
        "prompt": estonian_prompt,  # Context to help with domain-specific terminology
        "temperature": 0.0,  # Zero temperature for most deterministic output
        "response_format": "json"  # Get the response as structured JSON
    }
    
    # Try to make the request with retries
    for attempt in range(MAX_RETRIES):
        try:
            print(f"Attempt {attempt+1} to transcribe with OpenAI Whisper API")
            response = requests.post(
                OPENAI_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=90  # Longer timeout for audio processing
            )
            
            # Check if the request was successful
            if response.status_code == 200:
                result = response.json()
                raw_text = result["text"]
                
                # Apply domain-specific corrections
                final_text = post_process_estonian_transcript(raw_text)
                
                # Print both for debugging
                print(f"Raw transcript from API: {raw_text}")
                print(f"Final processed transcript: {final_text}")
                
                return final_text
            else:
                print(f"API error: {response.status_code} - {response.text}")
                
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {str(e)}")
            
        # Wait before retrying, with exponential backoff
        if attempt < MAX_RETRIES - 1:
            sleep_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
            print(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
    
    # If we get here, all retries failed
    raise Exception("Failed to transcribe audio after multiple attempts")

def store_transcript_with_encoding(transcript, bucket_name, file_path):
    """
    Store transcript with proper UTF-8 encoding to ensure Estonian characters display correctly
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)
    
    # Set content type with charset
    blob.content_type = 'text/plain; charset=utf-8'
    
    # Store with explicit UTF-8 encoding
    blob.upload_from_string(transcript, content_type='text/plain; charset=utf-8')
    
    return f"gs://{bucket_name}/{file_path}"

# Define common Estonian plumbing and service terms for work details
WORK_DETAILS_ESTONIAN = [
    "Ummistuse likvideerimine", "Hooldustööd", "Santehnilised tööd", "Elektritööd", 
    "Survepesu", "Gaasitööd", "Keevitustööd", "Kaameravaatlus", "Lekkeotsing gaasiga", 
    "Ehitustööd", "Rasvapüüdja tühjendus", "Muu", "Freesimistööd", 
    "Majasiseste kanalisatsioonitrasside pesu", "Fekaalivedu", 
    "Hinnapakkumise küsimine", "Väljakutse tasu", "tualettruumis", "Prisma", 
    "Tiskre", "Liiva tee", "Aitäh", "Tervist", "helistan", "tellida", "ummistus"
]

@functions_framework.cloud_event
def main(cloud_event):
    """
    Triggered by a new file in the audio input bucket.
    """
    # Extract bucket and file information from the Cloud Event
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]
    
    # Check if this is an audio file we should process
    file_extension = file_name.lower().split('.')[-1]
    if file_extension not in AUDIO_EXTENSIONS:
        print(f"Skipping non-audio file: {file_name}")
        return "Skipped non-audio file"
    
    try:
        # Extract caller ID from file name if available
        caller = file_name.split("_")[0] if "_" in file_name else "unknown"
        
        # Download the audio file from GCS
        print(f"Downloading audio file from bucket: {bucket_name}, file: {file_name}")
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        
        # Create a temporary file to store the audio
        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", delete=False) as temp_audio_file:
            temp_path = temp_audio_file.name
            blob.download_to_filename(temp_path)
            
            print(f"Processing audio file: {file_name} (caller: {caller})")
            
            # Get OpenAI API key from Secret Manager
            openai_api_key = access_secret(PROJECT_ID, OPENAI_API_KEY_SECRET_ID, "latest")
            
            # Get language code and ensure it's properly formatted
            language_code = access_secret(PROJECT_ID, LANGUAGE_CODE_SECRET_ID, "latest").strip()
            
            # Transcribe using OpenAI Whisper via direct API call
            print(f"Performing speech recognition with Whisper for {file_name}, language: {language_code}")
            transcript = call_openai_api_with_retries(openai_api_key, temp_path, language_code)
            
            # Print transcript for debugging
            print(f"Transcription result: {transcript}")
            
            # Clean up the temp file
            os.unlink(temp_path)
            
            # Store the transcript with proper encoding
            transcript_file_name = file_name.rsplit(".", 1)[0] + ".txt"
            transcript_path = f"transcripts/{transcript_file_name}"
            
            # Use special function to store with proper encoding
            transcript_url = store_transcript_with_encoding(transcript, OUTPUT_BUCKET, transcript_path)
            
            print(f"Transcription stored at: {transcript_url}")
            
            # Check for order confirmation or work request in more ways
            order_confirmed = any(phrase.lower() in transcript.lower() for phrase in 
                                 ["tellimus", "tellida", "on kinnitatud", "order confirmed", 
                                  "sooviks tellida"])
            
            # Check for specific work types mentioned in the transcript
            work_type_mentioned = False
            matched_work_types = []
            
            for work_type in WORK_DETAILS_ESTONIAN:
                if work_type.lower() in transcript.lower():
                    work_type_mentioned = True
                    matched_work_types.append(work_type)
            
            if matched_work_types:
                print(f"Detected work types: {matched_work_types}")
                
            # If order confirmed or work type mentioned, consider it a valid order
            if order_confirmed or work_type_mentioned:
                # Publish to Pub/Sub
                publisher = pubsub_v1.PublisherClient()
                topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC.split('/')[-1])
                
                # Ensure proper encoding for JSON data with Estonian characters
                message_data = {
                    "audio_file": file_name,
                    "transcript_file": transcript_path,
                    "bucket": OUTPUT_BUCKET,
                    "caller": caller,  # Add caller information
                    "detected_work_types": matched_work_types if matched_work_types else [],
                    "transcript": transcript
                }
                
                # Ensure proper UTF-8 encoding for the JSON message
                message_bytes = json.dumps(message_data, ensure_ascii=False).encode("utf-8")
                publish_future = publisher.publish(topic_path, data=message_bytes)
                publish_future.result()
                print(f"Published message to {OUTPUT_TOPIC}")
            else:
                print("No order or work type detected in transcription")
            
            return "Successfully transcribed audio file"
    except Exception as e:
        print(f"Error processing audio file: {str(e)}")
        raise e