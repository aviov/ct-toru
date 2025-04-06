import { Construct } from "constructs";
import {
  App,
  TerraformStack,
  TerraformOutput
} from "cdktf";
import { GoogleProvider } from "@cdktf/provider-google/lib/provider";
import { GcsBucket } from "./constructs/gcs-bucket";
import { PubSubTopic } from "./constructs/pubsub-topic";
import { GcfFunction } from "./constructs/gcf-function";

class MyStack extends TerraformStack {
  constructor(scope: Construct, id: string) {
    super(scope, id);

    new GoogleProvider(this, "google", {
      project: "ct-toru",
      region: "europe-west1",
    });

    const audioInputBucket = new GcsBucket(this, "audio-input", {
      name: "ct-toru-audio-input",
      location: "EU",
    });

    const transcriptionsBucket = new GcsBucket(this, "transcriptions", {
      name: "ct-toru-transcriptions",
      location: "EU",
    });

    const customerMatchesBucket = new GcsBucket(this, "customer-matches", {
      name: "ct-toru-customer-matches",
      location: "EU",
    });

    const ordersBucket = new GcsBucket(this, "orders", {
      name: "ct-toru-orders",
      location: "EU",
    });


    const orderConfirmedTopic = new PubSubTopic(this, "order-confirmed", {
      name: "ct-toru-order-confirmed",
    });

    const customerMatchedTopic = new PubSubTopic(this, "customer-matched", {
      name: "ct-toru-customer-matched",
    });



    new GcfFunction(this, "ingest-audio", {
      name: "ingest-audio",
      sourceDir: "../gcf/ingest-audio"
    });

    new GcfFunction(this, "transcribe-audio", {
      name: "transcribe-audio",
      sourceDir: "../gcf/transcribe-audio",
      triggerBucket: audioInputBucket.bucket,
    });

    new GcfFunction(this, "match-customer", {
      name: "match-customer",
      sourceDir: "../gcf/match-customer",
      triggerTopic: orderConfirmedTopic.topic.id,
    });

    new GcfFunction(this, "create-order", {
      name: "create-order",
      sourceDir: "../gcf/create-order",
      triggerTopic: customerMatchedTopic.topic.id,
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
