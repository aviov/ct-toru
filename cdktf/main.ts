import { Construct } from "constructs";
import {
  App,
  TerraformStack,
  TerraformOutput,
  GcsBackend,
  // RemoteBackend,
  // CloudBackend,
  // NamedCloudWorkspace
} from "cdktf";
import { GoogleProvider } from "@cdktf/provider-google/lib/provider";
import { GcsBucket } from "./constructs/gcs-bucket";
import { PubSubTopic } from "./constructs/pubsub-topic";
import { GcfFunction } from "./constructs/gcf-function";

const FUNCTION_ROLES: { [key: string]: string[] } = {
    "ingest-audio": [
      "roles/storage.objectAdmin" // Upload MP3s to GCS
    ],
    "transcribe-audio": [
      "roles/storage.objectViewer", // Read MP3s, write transcriptions
      "roles/pubsub.publisher", // Publish to order-confirmed
      "roles/speech.user" // Use Speech-to-Text
    ],
    "match-customer": [
      "roles/storage.objectViewer", // Read transcriptions, write customer matches
      "roles/pubsub.subscriber", // Subscribe to order-confirmed
      "roles/pubsub.publisher" // Publish to customer-matched
    ],
    "create-order": [
      "roles/storage.objectViewer", // Read customer matches, write orders
      "roles/pubsub.subscriber" // Subscribe to customer-matched
    ]
};

class MyStack extends TerraformStack {
  constructor(scope: Construct, id: string) {
    super(scope, id);
    
    new GcsBackend(this, {
      bucket: "ct-toru-tfstate",
      prefix: "terraform/state",
    });

    new GoogleProvider(this, "google", {
      project: "ct-toru",
      region: "europe-west1",
    });

    // Storage
    const audioInputBucket = new GcsBucket(this, "audio-input", {
      name: "ct-toru-audio-input",
      location: "europe-west1",
    });

    const transcriptionsBucket = new GcsBucket(this, "transcriptions", {
      name: "ct-toru-transcriptions",
      location: "europe-west1",
    });

    const customerMatchesBucket = new GcsBucket(this, "customer-matches", {
      name: "ct-toru-customer-matches",
      location: "europe-west1",
    });

    const ordersBucket = new GcsBucket(this, "orders", {
      name: "ct-toru-orders",
      location: "europe-west1",
    });


    const orderConfirmedTopic = new PubSubTopic(this, "order-confirmed", {
      name: "ct-toru-order-confirmed",
    });

    const customerMatchedTopic = new PubSubTopic(this, "customer-matched", {
      name: "ct-toru-customer-matched",
    });



    new GcfFunction(this, "ingest-audio", {
      name: "ingest-audio",
      sourceDir: "../gcf/ingest-audio",
      roles: FUNCTION_ROLES["ingest-audio"],
      environmentVariables: {
        "API_URL": "https://api.example.com/audio-files",
        "BUCKET_NAME": "ct-toru-audio-input"
        // API_KEY should be stored in Secret Manager in production
      }
    });

    new GcfFunction(this, "transcribe-audio", {
      name: "transcribe-audio",
      sourceDir: "../gcf/transcribe-audio",
      triggerBucket: audioInputBucket.bucket,
      roles: FUNCTION_ROLES["transcribe-audio"],
      environmentVariables: {
        "INPUT_BUCKET": audioInputBucket.bucket.name,
        "OUTPUT_TOPIC": orderConfirmedTopic.topic.id,
        "LANGUAGE_CODE": "en-US"
      }
    });

    new GcfFunction(this, "match-customer", {
      name: "match-customer",
      sourceDir: "../gcf/match-customer",
      triggerTopic: orderConfirmedTopic.topic.id,
      roles: FUNCTION_ROLES["match-customer"],
      environmentVariables: {
        "CUSTOMER_API_URL": "https://api.example.com/customers",
        "OUTPUT_TOPIC": customerMatchedTopic.topic.id,
        "STORAGE_BUCKET": transcriptionsBucket.bucket.name
      }
    });

    new GcfFunction(this, "create-order", {
      name: "create-order",
      sourceDir: "../gcf/create-order",
      triggerTopic: customerMatchedTopic.topic.id,
      roles: FUNCTION_ROLES["create-order"],
      environmentVariables: {
        "ORDER_API_URL": "https://api.example.com/orders",
        "OUTPUT_TOPIC": orderConfirmedTopic.topic.id,
        "STORAGE_BUCKET": customerMatchesBucket.bucket.name
      }
    });


    new TerraformOutput(this, "audio-input-bucket", {
      value: audioInputBucket.bucket.name,
      description: "Bucket for storing audio files",
    });

    new TerraformOutput(this, "transcriptions-bucket", {
      value: transcriptionsBucket.bucket.name,
      description: "Bucket for storing transcriptions",
    });

    new TerraformOutput(this, "customer-matches-bucket", {
      value: customerMatchesBucket.bucket.name,
      description: "Bucket for storing customer matches",
    });

    new TerraformOutput(this, "orders-bucket", {
      value: ordersBucket.bucket.name,
      description: "Bucket for storing orders",
    });

    new TerraformOutput(this, "order-confirmed-topic", {
      value: orderConfirmedTopic.topic.id,
      description: "Pub/Sub topic for order confirmation events",
    });

    new TerraformOutput(this, "customer-matched-topic", {
      value: customerMatchedTopic.topic.id,
      description: "Pub/Sub topic for customer match events",
    });
  }
}

const app = new App();
new MyStack(app, "call-crm-pipeline");
app.synth();
