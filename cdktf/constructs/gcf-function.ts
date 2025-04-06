import { Construct } from "constructs";
import { Cloudfunctions2Function } from "@cdktf/provider-google/lib/cloudfunctions2-function";
import { StorageBucket } from "@cdktf/provider-google/lib/storage-bucket";

interface GcfFunctionOptions {
    name: string;
    sourceDir: string;
    triggerBucket?: StorageBucket;
    triggerTopic?: string;
}

export class GcfFunction extends Construct {
    public readonly fn: Cloudfunctions2Function;
    constructor(scope: Construct, id: string, options: GcfFunctionOptions) {
        super(scope, id);
        this.fn = new Cloudfunctions2Function(this, "function", {
            name: options.name,
            location: "europe-west1",
            buildConfig: {
                runtime: "python312",
                entryPoint: "main",
                source: {
                    storageSource: {
                        bucket: new StorageBucket(this, "source-bucket", {
                            name: `${options.name}-source-${Date.now()}`,
                            location: "EU",
                        }).name,
                        object: `${options.name}.zip`,
                    },
                },
            },
            serviceConfig: {
                availableMemory: "256MB",
                timeoutSeconds: 540, // 9 minutes
            },

            eventTrigger: options.triggerBucket ? {
                eventType: "google.cloud.storage.object.v1.finalized",
                eventFilters: [
                    { attribute: "bucket", value: options.triggerBucket.name }
                ]
            } : options.triggerTopic ? {
                eventType: "google.cloud.pubsub.topic.v1.messagePublished",
                pubsubTopic: options.triggerTopic
            } : undefined
        });
    }
}