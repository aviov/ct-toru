import { Construct } from "constructs";
import { Cloudfunctions2Function } from "@cdktf/provider-google/lib/cloudfunctions2-function";
import { StorageBucket } from "@cdktf/provider-google/lib/storage-bucket";
import { ServiceAccount } from "@cdktf/provider-google/lib/service-account";
import { ProjectIamMember } from "@cdktf/provider-google/lib/project-iam-member";

interface GcfFunctionOptions {
    name: string;
    sourceDir: string;
    triggerBucket?: StorageBucket;
    triggerTopic?: string;
    roles?: string[];
    environmentVariables?: Record<string, string>;
}

export class GcfFunction extends Construct {
    public readonly fn: Cloudfunctions2Function;
    public readonly serviceAccount: ServiceAccount;
    constructor(scope: Construct, id: string, options: GcfFunctionOptions) {
        super(scope, id);// Create a custom service account for the function

        this.serviceAccount = new ServiceAccount(this, "service-account", {
            accountId: options.name,
            displayName: `Service account for ${options.name} function`,
            project: "ct-toru",
        });

        this.fn = new Cloudfunctions2Function(this, "function", {
            name: `${options.name}-${new Date().toISOString().replace(/[-:.]/g, '')}`,
            location: "europe-west1",
            buildConfig: {
                runtime: "python311", // Use Python 3.11 for all functions
                entryPoint: "main",
                source: {
                    storageSource: {
                        bucket: new StorageBucket(this, "source-bucket", {
                            name: `${options.name}-source`,
                            location: "europe-west1",
                            forceDestroy: true,
                        }).name,
                        object: `${options.name}.zip`,
                    },
                },
                environmentVariables: {
                    "DEPLOY_TIMESTAMP": new Date().toISOString(),
                },
            },
            serviceConfig: {
                availableMemory: "256M",
                timeoutSeconds: 540,
                environmentVariables: {
                    "GOOGLE_FUNCTION_SOURCE": options.sourceDir,
                    ...(options.environmentVariables || {})
                },
                serviceAccountEmail: this.serviceAccount.email, // Explicitly set the service account
            },
            labels: {
                "deployment-tool": "cdktf"
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

        if (options.roles) {
            // Use project-level IAM bindings instead of function-level IAM bindings
            options.roles.forEach((role, index) => {
                // Clean the role name for use in resource ID
                const cleanRole = role.replace(/[\/\.]/g, '_');
                
                // Make sure the role has the full prefix
                const fullRoleName = role.startsWith('roles/') ? role : `roles/${role}`;
                
                // Skip roles that are not supported at project level
                if (fullRoleName === 'roles/speech.user') {
                    console.warn(`Skipping role ${fullRoleName} as it's not supported at project level. ` + 
                                 `This permission needs to be granted separately via the Google Cloud Speech API.`);
                    return;
                }                
                // Create a project-level IAM binding for the service account
                new ProjectIamMember(this, `iam-${cleanRole}-${index}`, {
                    project: "ct-toru",
                    role: fullRoleName,
                    member: `serviceAccount:${this.serviceAccount.email}`,
                    // dependsOn: [this.fn, this.serviceAccount],
                    dependsOn: [this.serviceAccount],
                });
            });
        }
    }
}