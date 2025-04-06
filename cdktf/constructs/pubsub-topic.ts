import { Construct } from "constructs";
import { PubsubTopic } from "@cdktf/provider-google/lib/pubsub-topic";

interface PubSubTopicOptions {
    name: string;
}

export class PubSubTopic extends PubsubTopic {
    public readonly topic: PubsubTopic;
    constructor(scope: Construct, id: string, options: PubSubTopicOptions) {
        super(scope, id, options);
        this.topic = new PubsubTopic(this, id, {
            name: options.name,
        });
    }
}