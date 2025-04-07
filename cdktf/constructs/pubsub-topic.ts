import { Construct } from "constructs";
import { PubsubTopic } from "@cdktf/provider-google/lib/pubsub-topic";

interface PubSubTopicOptions {
    name: string;
}

export class PubSubTopic extends Construct { // Extend Construct, not PubsubTopic
    public readonly topic: PubsubTopic;
    constructor(scope: Construct, id: string, options: PubSubTopicOptions) {
        super(scope, id);
        this.topic = new PubsubTopic(this, "topic", { // Use a fixed ID to avoid duplicates
            name: options.name,
        });
    }
}