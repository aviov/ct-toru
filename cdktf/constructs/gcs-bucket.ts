import { Construct } from "constructs";
import { StorageBucket } from "@cdktf/provider-google/lib/storage-bucket";

interface GcsBucketOptions {
    name: string;
    location: string;
}

export class GcsBucket extends Construct { // Extend Construct, not StorageBucket
    public readonly bucket: StorageBucket;
    constructor(scope: Construct, id: string, options: GcsBucketOptions) {
        super(scope, id);
        this.bucket = new StorageBucket(this, "bucket", { // Use a fixed ID to avoid duplicates
            name: options.name,
            location: options.location || 'europe-west1',
            uniformBucketLevelAccess: true,
            forceDestroy: true,
        });
    }
}