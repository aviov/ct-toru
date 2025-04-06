import { Construct } from "constructs";
import { StorageBucket  } from "@cdktf/provider-google/lib/storage-bucket";

interface GcsBucketOptions {
    name: string;
    location: string;
}

export class GcsBucket extends StorageBucket {
    public readonly bucket: StorageBucket;
    constructor(scope: Construct, id: string, options: GcsBucketOptions) {
        super(scope, id, options);
        this.bucket = new StorageBucket(this, id, {
            name: options.name,
            location: options.location || 'EU',
            uniformBucketLevelAccess: true,
        });
    }
}