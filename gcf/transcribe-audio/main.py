from google.cloud import speech_v1p1beta1 as speech
from google.cloud import storage
from google.cloud import pubsub_v1

def main(event, context):
    bucket_name = event['bucket']
    file_name = event['name']
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    blob.download_to_filename('tmp/audio.mp3')

    speech_client = speech.SpeechClient()
    config = speech.RecognitionConfig(
        sample_rate_hertz=44100,
        # sample_rate_hertz=16000,
        language_code='et-EE',
        enable_automatic_punctuation=True,
        speech_contexts=[speech.SpeechContext(phrases=["Tellimus on kinnitatud"])],
    )
    
    with open('tmp/audio.mp3', 'rb') as audio_file:
        content = audio_file.read()
    audio = speech.RecognitionAudio(content=content)
    response = speech_client.recognize(config=config, audio=audio)
    
    transcription = " ".join([result.alternatives[0].transcript for result in response.results])
    is_order = "Tellimus on kinnitatud" in transcription
    
    output_blob = bucket.blob(f"transcriptions/{file_name}.txt")
    output_blob.upload_from_string(transcription)

    if is_order:
        pubsub_client = pubsub_v1.PublisherClient()
        topic_path = pubsub_client.topic_path('ct-toru', 'order-confirmed')
        publisher.publish(topic_path, f"{file_name}|gs://{bucket_name}/transcriptions/{file_name}.txt".encode())

    return "Done"