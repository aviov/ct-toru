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
from pydub import AudioSegment
from fuzzywuzzy import fuzz

# Environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "ct-toru-audio-input")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "ct-toru-transcriptions")
OUTPUT_TOPIC = os.environ.get("OUTPUT_TOPIC", "ct-toru-order-confirmed")
LANGUAGE_CODE_SECRET_ID = os.environ.get("LANGUAGE_CODE_SECRET_ID", "ct-toru-language-code")
OPENAI_API_KEY_SECRET_ID = os.environ.get("OPENAI_API_KEY_SECRET_ID", "ct-toru-openai-api-key")

# Audio file extensions to process
AUDIO_EXTENSIONS = ['mp3', 'wav', 'flac', 'm4a', 'ogg']

# Keyword mappings for Toruabi services to include in the prompt
SERVICE_KEYWORDS = {
    "Ummistuse likvideerimine": ["ummistus", "ummistuse", "likvideerimine", "tõkke", "ummistunud"],
    "Hooldustööd": ["hooldus", "hooldustööd", "hoolduse", "korras", "kontroll"],
    "Santehnilised tööd": ["santehnilised", "santehnika", "torutööd", "toru", "veetoru", "veetoruleke"],
    "Elektritööd": ["elektritööd", "elekter", "elektri", "juhe", "vool"],
    "Survepesu": ["survepesu", "pesu", "surve", "puhastus", "suurvabesutööd"],
    "Gaasitööd": ["gaasitööd", "gaas", "gaasi"],
    "Keevitustööd": ["keevitustööd", "keevitus", "keevita"],
    "Kaameravaatlus": ["kaameravaatlus", "kaamera", "vaatlus", "inspektsioon"],
    "Lekkeotsing gaasiga": ["lekkeotsing", "leke", "gaasiga", "gaasileke"],
    "Ehitustööd": ["ehitustööd", "ehitus", "ehituse", "renoveerimine"],
    "Rasvapüüdja tühjendus kuni 4m3": ["rasvapüüdja", "tühjendus", "rasva", "4m3"],
    "Muu": ["muu", "teine", "misc", "other"],
    "Freesimistööd": ["freesimistööd", "freesimine", "frees"],
    "Majasiseste kanalisatsioonitrasside pesu": ["kanalisatsioonitrasside", "kanalisatsioon", "pesu", "majasiseste"],
    "Fekaalivedu (1 koorem = kuni 5 m3)": ["fekaalivedu", "fekaal", "koorem", "5 m3"],
    "Hinnapakkumise küsimine": ["hinnapakkumine", "hinnapakkumise", "pakkumine", "hind"],
    "Väljakutse tasu": ["väljakutse", "tasu", "teenustasu"]
}

# Define common Estonian plumbing and service terms for work details
WORK_DETAILS_ESTONIAN = [
    "Ummistuse likvideerimine", "Hooldustööd", "Santehnilised tööd", "Elektritööd", 
    "Survepesu", "Gaasitööd", "Keevitustööd", "Kaameravaatlus", "Lekkeotsing gaasiga", 
    "Ehitustööd", "Rasvapüüdja tühjendus", "Muu", "Freesimistööd", 
    "Majasiseste kanalisatsioonitrasside pesu", "Fekaalivedu", 
    "Hinnapakkumise küsimine", "Väljakutse tasu"
]

def access_secret(project_id: str, secret_id: str, version_id: str) -> str:
    """
    Access the secret from Secret Manager.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def load_reference_data(bucket_name):
    """Load reference data from GCS."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    # Full lists for fuzzy matching
    full_reference_files = {
        "addresses": "reference_data/estonian_addresses.json",
        "companies": "reference_data/estonian_companies.json",
        "names": "reference_data/estonian_names.json"
    }
    
    # Subset lists for LLM
    subset_reference_files = {
        "addresses": "reference_data/estonian_addresses_subset.json",
        "companies": "reference_data/estonian_companies_subset.json",
        "names": "reference_data/estonian_names_subset.json"
    }
    
    reference_data = {"full": {}, "subset": {}}
    
    # Load full lists
    for key, file_path in full_reference_files.items():
        blob = bucket.blob(file_path)
        try:
            data = json.loads(blob.download_as_string().decode("utf-8"))
            reference_data["full"][key] = data
        except Exception as e:
            print(f"Error loading full {file_path}: {str(e)}")
            reference_data["full"][key] = {}
    
    # Load subset lists
    for key, file_path in subset_reference_files.items():
        blob = bucket.blob(file_path)
        try:
            data = json.loads(blob.download_as_string().decode("utf-8"))
            reference_data["subset"][key] = data
        except Exception as e:
            print(f"Error loading subset {file_path}: {str(e)}")
            reference_data["subset"][key] = {}
    
    return reference_data

def preprocess_audio(audio_file_path):
    """Pre-process the audio to improve quality."""
    audio = AudioSegment.from_file(audio_file_path)
    print(f"Raw audio duration: {len(audio) / 1000} seconds")
    # Convert to mono
    audio = audio.set_channels(1)
    # Resample to 16kHz
    audio = audio.set_frame_rate(16000)
    # Normalize volume
    audio = audio.normalize()
    # Remove silence
    # audio = audio.strip_silence(silence_len=500, silence_thresh=-50, padding=100)
    processed_path = audio_file_path + "_processed.mp3"
    audio.export(processed_path, format="mp3")
    print(f"Processed audio duration: {len(audio) / 1000} seconds")
    return processed_path

def post_process_estonian_transcript(text, reference_data):
    """
    Post-process the transcript to fix common Estonian misrecognitions using reference data.
    """
    # Use full lists for fuzzy matching
    addresses = reference_data["full"].get("addresses", {})
    companies = reference_data["full"].get("companies", {})
    names = reference_data["full"].get("names", {})
    
    # Common word corrections for this domain
    corrections = {
        r'\btelistan\b': 'helistan',
        r'\btoual+e+t+ru+m\w+\b': 'tualettruumis',
        r'\bnais[dt]a\b,?\s+t': 'naiste t',
        r'\bpovastada\b': 'puhastada',
        r'\bpoastada\b': 'puhastada',
        r'\bpohastada\b': 'puhastada',
        r'\bkeskmus\b': 'kestnud',
        r'\baast\w+ (kuu|6)\b': 'kell 6',
        r'\bajastad\w+\b': 'ajast',
        r'\bantsi[dt]\b': 'andsite',
        r'\bkena\b': 'kena',
        r'\bAga kena\b': 'Väga kena',
        r'\bPagab kanna\b': 'Väga kena',
        r'\bOotake\b': 'Oodake',
        r'\bVõidake õks\b': 'Oodake hetk',
        r'\bummistusegimine\b': 'ummistus',
        r'\bmenname\b': 'jõuame',
        r'\bjõua menna\b': 'jõuame enne',
        r'\bsuurvabesutööd\b': 'survepesu',
        r'\bsuurvabesutööti\b': 'survepesutööd',
        r'\bpeavast\b': 'päevast',
        r'\bKummikrofi\b': 'Kummiprofi',
        r'\bViinsi\b': 'Viimsi',
        r'\bViimasi\b': 'Viimsi',
        r'\bGoldmindo\b': 'Goldmind',
        r'\bGoldmint\b': 'Goldmind',
        r'\bvannidupa\b': 'vannituppa',
        r'\binge jama\b': 'mingi jama',
        # r'\bKuidas 329\b': 'Kunderi 329',
        r'\bkaha võõlem\b': 'kaua võõrad',
        r'\btervista\b': 'tervist',
        r'\bTervistelistan\b': 'Tervist, helistan',
        r'\bmenna\b': 'enne',
        r'\bveedoru\b': 'veetoru',
        r'\blõpinguline\b': 'lepinguline',
        # r'\bTallikade 17\b': 'Tallinna tee 14',  # Based on context
        r'\bKuidas\b': 'kuidas',  # Remove incorrect replacement
        r'\bümselt\b': 'ümber',
        r'\bkeevad meil aeg ajal tooltulas\b': 'käivad meil aeg-ajalt hooldamas',
        r'\bniiks renn\b': 'siin üks renn',
        # r'\bvööda radiust\b': 'veebruar',
        r'\bvaatan\b': 'just vaatan',  # Context-specific correction
        # r'\bAijandi\b': 'Aiandi',
        # r'\bhaabneeme\b': 'Haabneeme',
        r'\bliivapüüdipuhastus\b': 'liivapüüdja puhastus',
        r'\bkõtega\b': 'kas',
        r'\bsiva\b': 'siia',
        r'\belatavaliselt\b': 'ettevalmistavalt',
        r'\bvapustel\b': 'vapustav',
        r'\bnimipeal\b': 'nimi peal',
        r'\bsobi västi\b': 'sobib hästi',
        r'\bedistab\b': 'edastab',
        r'\btänavlik\b': 'tänulik',
        r'\bkenapäeva\b': 'kena päeva',
        r'\bnäge vist\b': 'nägemist',
        r'\bT-(\d+)\b': r'tee \1',  # Replace "T-24" with "tee 24"
        # r'\bMarili Torin\b': 'Marili Torim'
    }
    
    # Apply corrections
    for pattern, replacement in corrections.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    # Fuzzy matching for addresses, companies, and names
    def fuzzy_correct(text_segment, reference_list, threshold=85):
        best_match = None
        best_score = 0
        for item in reference_list:
            score = fuzz.ratio(text_segment.lower(), item.lower())
            if score > best_score and score >= threshold:
                best_score = score
                best_match = item
        return best_match if best_match else text_segment
    
    # Correct street names
    street_pattern = re.compile(r'\b([A-Za-zÕÄÖÜõäöü\s-]+)\s+(tee|tn|puiestee|pst|tänav|maantee)\b', re.IGNORECASE)
    for match in street_pattern.finditer(text):
        street_name = match.group(1)
        corrected = fuzzy_correct(street_name, addresses.get("streets", []))
        if corrected != street_name:
            text = text.replace(match.group(0), f"{corrected} {match.group(2)}")
    
    # Correct districts and counties
    for district in addresses.get("districts", []):
        text = fuzzy_correct(text, [district], threshold=85)
    for county in addresses.get("counties", []):
        text = fuzzy_correct(text, [county], threshold=85)
    
    # Correct company names
    company_pattern = re.compile(r'\b([A-Za-zÕÄÖÜõäöü\s-]+(?:OÜ|AS|MTÜ|TÜ|FIE|UÜ|TüH))\b', re.IGNORECASE)
    for match in company_pattern.finditer(text):
        company_name = match.group(1)
        corrected = fuzzy_correct(company_name, companies.get("companies", []))
        if corrected != company_name:
            text = text.replace(company_name, corrected)
    
    # Correct person names
    name_indicators = ['nimi', 'on', 'mina olen', 'helistab', 'kontakt']
    name_pattern = re.compile(r'(?:' + '|'.join(name_indicators) + r')\s+(?:on\s+)?([A-Za-zÕÄÖÜõäöü]{2,}(?:\s+[A-Za-zÕÄÖÜõäöü]{2,})?)', re.IGNORECASE)
    for match in name_pattern.finditer(text):
        name = match.group(1)
        first_name = name.split()[0] if " " in name else name
        corrected = fuzzy_correct(first_name, names.get("first_names", []))
        if corrected != first_name:
            text = text.replace(first_name, corrected)
    
    # General cleanup
    text = re.sub(r',\s+(\w+),', r' \1', text)  # Remove unnecessary commas
    text = re.sub(r'\s+([.,!?])', r'\1', text)  # Fix punctuation spacing
    text = re.sub(r'\b(\w+)(\s+\1)+\b', r'\1', text)  # Remove repeated words
    
    return text

def post_process_with_openai(api_key, raw_transcript, reference_data):
    """Post-process the transcript using OpenAI Chat API to correct errors."""
    OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Use subset lists for LLM
    addresses = reference_data["subset"].get("addresses", {})
    companies = reference_data["subset"].get("companies", {})
    names = reference_data["subset"].get("names", {})
    
    # Format reference data for the prompt
    street_list = ", ".join(addresses.get("streets", []))
    district_list = ", ".join(addresses.get("districts", []))
    county_list = ", ".join(addresses.get("counties", []))
    city_list = ", ".join(addresses.get("cities", []))
    company_list = ", ".join(companies.get("companies", []))
    first_name_list = ", ".join(names.get("first_names", []))
    last_name_list = ", ".join(names.get("last_names", []))

    # Prompt for post-processing
    post_process_prompt = (
        "You are an expert in Estonian language and the domain of plumbing services. "
        "The following text is a transcription of a phone call to a plumbing company (Toruabi) in Estonian. "
        "The transcription may contain errors due to speech recognition issues. Your task is to: "
        "1. Correct any misrecognized words or phrases, especially those related to plumbing services. "
        "2. Remove any repeated phrases that are likely due to transcription errors (e.g., the same phrase repeated multiple times). "
        "3. Ensure the text is coherent and natural in Estonian, preserving the original meaning as much as possible. "
        "4. If a term matches a Toruabi service, ensure it is written correctly (e.g., 'suurvabesutööd' should be 'survepesu'). "
        "5. **Preserve proper nouns such as names of people, companies, and street names unless they are clearly incorrect**. "
        "   Use the following reference lists to correct these entities:\n"
        f"   - Common Estonian street names: {street_list}\n"
        f"   - Common Estonian districts: {district_list}\n"
        f"   - Common Estonian counties: {county_list}\n"
        f"   - Common Estonian cities: {city_list}\n"
        f"   - Common Estonian company names: {company_list}\n"
        f"   - Common Estonian first names: {first_name_list}\n"
        f"   - Common Estonian last names: {last_name_list}\n"
        "   If a name, company, or address component in the transcript closely matches one of these, correct it to the exact match. "
        "6. **Correct addresses to follow Estonian conventions**: Replace 'T' with 'tee' in street names (e.g., 'Liiva T61' should be 'Liiva tee 61'). Ensure the address format is correct (e.g., 'street name' followed by 'number'). "
        "7. **Do not add new conversational elements that were not in the original transcript** (e.g., do not add 'Kas on veel midagi, millega saame aidata?' or 'Selge' unless they were present). "
        "8. **Avoid changing minor stylistic variations unless they are clearly incorrect** (e.g., do not change 'mõtev' to 'mõtlete' or 'sobi västi' to 'sobib hästi' unless the original is grammatically incorrect). "
        "9. **Format the output with single newlines between conversational turns, without extra empty lines** (e.g., 'Line 1\nLine 2\nLine 3'). "
        "Here are the valid Toruabi services: Ummistuse likvideerimine, Hooldustööd, Santehnilised tööd, Elektritööd, "
        "Survepesu, Gaasitööd, Keevitustööd, Kaameravaatlus, Lekkeotsing gaasiga, Ehitustööd, "
        "Rasvapüüdja tühjendus kuni 4m3, Muu, Freesimistööd, Majasiseste kanalisatsioonitrasside pesu, "
        "Fekaalivedu (1 koorem = kuni 5 m3), Hinnapakkumise küsimine, Väljakutse tasu. "
        "Here is the raw transcript to correct:\n\n"
        f"{raw_transcript}\n\n"
        "Provide the corrected transcript in Estonian."
    )
    
    payload = {
        "model": "gpt-4o",  # Use gpt-4o for better language understanding
        "messages": [
            {"role": "system", "content": "You are a helpful assistant for correcting Estonian transcriptions."},
            {"role": "user", "content": post_process_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(OPENAI_CHAT_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        corrected_transcript = result["choices"][0]["message"]["content"].strip()
        corrected_transcript = re.sub(r'\n\s*\n', '\n', corrected_transcript)
        print(f"Corrected transcript from OpenAI Chat API: {corrected_transcript}")
        return corrected_transcript
    except Exception as e:
        print(f"Error in OpenAI Chat API post-processing: {str(e)}")
        return raw_transcript  # Fallback to raw transcript if the API call fails

def generate_estonian_prompt(reference_data):
    """Generate a prompt for Whisper API with Toruabi service keywords and reference data."""
    
    # Use subset lists for Whisper prompt
    addresses = reference_data["subset"].get("addresses", {})
    companies = reference_data["subset"].get("companies", {})
    names = reference_data["subset"].get("names", {})
    
    # Create lists for the prompt
    street_examples = ", ".join(f"\"{street}\"" for street in addresses.get("streets", [])[:5])
    district_examples = ", ".join(f"\"{district}\"" for district in addresses.get("districts", [])[:5])
    county_examples = ", ".join(f"\"{county}\"" for county in addresses.get("counties", [])[:5])
    city_examples = ", ".join(f"\"{city}\"" for city in addresses.get("cities", [])[:5])
    company_examples = ", ".join(f"\"{company}\"" for company in companies.get("companies", [])[:5])
    name_examples = ", ".join(f"\"{first} {last}\"" for first, last in zip(names.get("first_names", [])[:5], names.get("last_names", [])[:5]))
    
    base_prompt = (
        "See on helisalvestis toruettevõtte klienditeenindusele. Räägitakse eesti keeles ja teemaks "
        "on toruabi tellimine, ummistus, või santehnilised tööd. Võimalikud fraasid: "
        "\"Toruabi, tere\", \"Tervist, helistan\", \"helistan Tiskre Prismast\", \"naiste tualettruumis on ummistus\", "
        "\"kanalisatsiooniga on mingi jama\", \"santehnilised tööd\", \"hooldustööd\", \"survepesu\", "
        "\"elektritööd\", \"puhastada\", \"kestnud nädal aega\", \"Palun öelge täpne aadress\", "
        "\"kuidas teie nimi on\", \"Oodake hetk\", \"ma saan rääkida\", \"kell kuue ja kaheksa vahel\", "
        "\"tõenäoliselt jõuame enne\", \"Väga kena\", \"Teeme nii\", \"Aitäh\", \"kanalisatsioonitrasside pesu\", "
        "\"veetoruleke\", \"vannituppa ei lähe vesi alla\", \"kraanikausi alt\", \"Tallinnas\", "
        "\"ei midagi, te andsite kogu informatsiooni\", \"hetkel saan rääkida\", \"meil on siin üks renn\", "
        "\"viimati on käinud teine veebruar, just vaatan\", \"sooviksime palun tellida\", \"mis aadressist me räägime\", "
        "\"kontaktiks oma telefon\", \"ja arvesaaja on\", \"super, väga tänulik teile\", \"kena päeva\", \"nägemist\". "
        f"Inimeste nimed: {name_examples}. "
        f"Aadressid (tänavad): {street_examples}; linnaosad: {district_examples}; vallad/maakonnad: {county_examples}; linnad: {city_examples}. "
        f"Ettevõtted: {company_examples}."
    )
    
    # Add service-related keywords
    service_keywords = []
    for service, keywords in SERVICE_KEYWORDS.items():
        service_keywords.extend(keywords)
    service_keywords_str = ", ".join(f"\"{keyword}\"" for keyword in service_keywords)
    
    return f"{base_prompt} Võimalikud teenused ja märksõnad: {service_keywords_str}."

def call_openai_api_with_retries(api_key, audio_file_path, language_code, reference_data):
    """Call OpenAI API directly using requests with retries for better network reliability"""
    OPENAI_API_URL = "https://api.openai.com/v1/audio/transcriptions"
    MAX_RETRIES = 5
    RETRY_DELAY = 2  # seconds
    
    # Make sure to strip any whitespace or newline characters from the API key
    api_key = api_key.strip()
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    # Pre-process the audio
    processed_audio_path = preprocess_audio(audio_file_path)
    
    # Read the audio file as binary
    with open(processed_audio_path, "rb") as file:
        audio_data = file.read()
    
    # Create prompt with Estonian plumbing/service terminology to improve recognition
    estonian_prompt = generate_estonian_prompt(reference_data)
    
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
                corrected_text = post_process_estonian_transcript(raw_text, reference_data)
                
                # Further post-process with OpenAI Chat API
                final_text = post_process_with_openai(api_key, corrected_text, reference_data)
                
                # Print both for debugging
                print(f"Raw transcript from API: {raw_text}")
                print(f"After correction map: {corrected_text}")
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
        
        # Load reference data
        reference_data = load_reference_data(OUTPUT_BUCKET)
        print(f"Loaded reference data: {reference_data}")
        
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
            transcript = call_openai_api_with_retries(openai_api_key, temp_path, language_code, reference_data)
            
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
                                 ["tellimus", "tellida", "soovime tellida", "on kinnitatud", "order confirmed", 
                                  "sooviks tellida", "tellimus kinnitatud", "tellimus on kinnitatud", 
                                  "sobib", "sulas", "kaardiga", "teha arve", "tehnik tuleb"])
            
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
            # if order_confirmed or work_type_mentioned:
            if order_confirmed:
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