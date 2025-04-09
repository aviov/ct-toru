#!/bin/bash
# Script to test all deployed Google Cloud Functions

set -e
echo "Testing Cloud Functions..."

PROJECT_ID="ct-toru"
REGION="europe-west1"
FUNCTIONS=(
    # "ingest-audio"
    "transcribe-audio"
    # "match-customer"
    # "create-order"
)

# Test HTTP functions with a simple GET request
test_http_function() {
    local function_name=$1
    echo "Testing HTTP function: $function_name"
    
    # Get the URL of the function
    URL=$(gcloud functions describe $function_name \
        --gen2 \
        --region=$REGION \
        --project=$PROJECT_ID \
        --format="value(serviceConfig.uri)" 2>/dev/null || echo "")
    
    if [ -z "$URL" ]; then
        echo "  ❌ Function $function_name does not have a URL (might be event-driven)"
    else
        # Make a request to the function
        HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" $URL)
        
        if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "204" ]; then
            echo "  ✅ Function $function_name responded with HTTP $HTTP_STATUS"
        else
            echo "  ❌ Function $function_name returned HTTP $HTTP_STATUS"
        fi
    fi
}

# Test event-driven functions by sending a test event
test_event_function() {
    local function_name=$1
    echo "Testing event-driven function: $function_name"
    
    # Get the trigger type of the function
    TRIGGER_TYPE=$(gcloud functions describe $function_name \
        --gen2 \
        --region=$REGION \
        --project=$PROJECT_ID \
        --format="value(eventTrigger.eventType)" 2>/dev/null || echo "")
    
    if [ -z "$TRIGGER_TYPE" ]; then
        echo "  ⚠️ Function $function_name doesn't appear to have an event trigger"
        return
    fi
    
    # Create a test event based on the trigger type
    if [[ "$TRIGGER_TYPE" == *"google.cloud.storage"* ]]; then
        echo "  ℹ️ Storage-triggered function detected"
        echo "  ⚠️ Storage event testing requires uploading a file to the trigger bucket"
        echo "  ⚠️ This test is skipped to avoid side effects"
    elif [[ "$TRIGGER_TYPE" == *"google.cloud.pubsub"* ]]; then
        echo "  ℹ️ Pub/Sub-triggered function detected"
        # Get the topic
        TOPIC=$(gcloud functions describe $function_name \
            --gen2 \
            --region=$REGION \
            --project=$PROJECT_ID \
            --format="value(eventTrigger.pubsubTopic)" 2>/dev/null || echo "")
        
        if [ -n "$TOPIC" ]; then
            # Extract just the topic name from the full path
            TOPIC_NAME=$(echo $TOPIC | sed 's|.*/||')
            echo "  ℹ️ Publishing test message to topic: $TOPIC_NAME"
            
            # Publish a test message
            gcloud pubsub topics publish $TOPIC_NAME \
                --project=$PROJECT_ID \
                --message="Test message from test-functions.sh script" &>/dev/null
                
            echo "  ✅ Published test message to $TOPIC_NAME for $function_name"
        else
            echo "  ❌ Could not determine Pub/Sub topic for $function_name"
        fi
    else
        echo "  ⚠️ Unknown trigger type: $TRIGGER_TYPE, skipping test"
    fi
}

# Main testing loop
for func in "${FUNCTIONS[@]}"; do
    echo ""
    echo "=== Testing $func ==="
    
    # Check if function exists
    if gcloud functions describe $func --gen2 --region=$REGION --project=$PROJECT_ID &>/dev/null; then
        echo "✅ Function $func exists"
        
        # Test both HTTP and event triggers (only one will be successful)
        test_http_function $func
        test_event_function $func
    else
        echo "❌ Function $func does not exist or is not accessible"
    fi
done

echo ""
echo "Testing complete!"
