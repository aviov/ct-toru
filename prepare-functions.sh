#!/bin/bash

# List of functions
FUNCTIONS=("ingest-audio" "transcribe-audio" "match-customer" "create-order")

# Build and zip each function
for FUNCTION in "${FUNCTIONS[@]}"; do
    echo "Building $FUNCTION..."

    # Navigate to the function directory
    cd gcf/$FUNCTION

    # Copy the shared Dockerfile
    cp ../Dockerfile .

    # Build the Docker image
    docker build -t $FUNCTION .

    # Run a container to extract the files
    docker run -d --name $FUNCTION-container $FUNCTION sleep infinity

    # Copy the application files and dependencies
    docker cp $FUNCTION-container:/app/main.py .
    docker cp $FUNCTION-container:/app/requirements.txt .
    docker cp $FUNCTION-container:/app/venv/lib/python3.11/site-packages site-packages

    # Create the zip file
    zip -r $FUNCTION.zip main.py requirements.txt site-packages

    # Upload the zip file to GCS
    gsutil cp $FUNCTION.zip gs://$FUNCTION-source/

    # Clean up
    docker stop $FUNCTION-container
    docker rm $FUNCTION-container
    rm -rf site-packages $FUNCTION.zip Dockerfile

    cd ../..
done

echo "All functions have been built and uploaded."