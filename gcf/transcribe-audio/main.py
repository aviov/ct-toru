import functions_framework
import os
from google.cloud import speech
from google.cloud import storage
from google.cloud import pubsub_v1

@functions_framework.cloud_event
def main(cloud_event):
    try:
        # Extract event data
        data = cloud_event.data
        bucket_name = data['bucket']
        file_name = data['name']
        
        # Download the audio file from GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        local_path = f"/tmp/{file_name}"  # Use /tmp for Cloud Functions
        blob.download_to_filename(local_path)

        # Transcribe the audio using Speech-to-Text
        speech_client = speech.SpeechClient()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code='et-EE',
            enable_automatic_punctuation=True,
            speech_contexts=[speech.SpeechContext(phrases=["Tellimus on kinnitatud"])],
        )
        
        with open(local_path, 'rb') as audio_file:
            content = audio_file.read()
        audio = speech.RecognitionAudio(content=content)
        response = speech_client.recognize(config=config, audio=audio)
        
        # Extract transcription
        transcription = " ".join(result.alternatives[0].transcript for result in response.results)
        is_order_confirmed = "Tellimus on kinnitatud" in transcription
        
        # Upload transcription to GCS
        output_bucket = storage_client.bucket('ct-toru-transcriptions')
        output_blob = output_bucket.blob(f"{file_name}.txt")
        output_blob.upload_from_string(transcription)

        # Publish to Pub/Sub if order is confirmed
        if is_order_confirmed:
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path('ct-toru', 'ct-toru-order-confirmed')
            message = f"{file_name}|gs://{bucket_name}/transcriptions/{file_name}.txt"
            publisher.publish(topic_path, message.encode("utf-8"))

        # Clean up
        os.remove(local_path)

        return "Transcription complete"
    except Exception as e:
        print(f"Error processing event: {str(e)}")
        raise e