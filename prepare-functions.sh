#!/bin/bash

# List of functions
# FUNCTIONS=("ingest-audio" "transcribe-audio" "match-customer" "create-order")
FUNCTIONS=("transcribe-audio")

# Build and zip each function
for FUNCTION in "${FUNCTIONS[@]}"; do
    echo "Building $FUNCTION..."

    # Navigate to the function directory
    cd gcf/$FUNCTION

    # Create a temporary directory for packaging
    mkdir -p tmp_package
    
    # Copy local files directly (instead of from Docker)
    cp main.py tmp_package/
    cp requirements.txt tmp_package/
    
    # Install dependencies in a virtual environment for packaging
    python3 -m venv tmp_venv
    source tmp_venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    
    # Create site-packages directory and copy dependencies
    mkdir -p tmp_package/site-packages
    pip freeze > tmp_package/requirements-freeze.txt
    
    # Instead of copying from Docker, we'll install directly to a temp directory
    pip install -r requirements.txt --target tmp_package/site-packages
    
    # Create the zip file from our local files
    cd tmp_package
    zip -r ../$FUNCTION.zip main.py requirements.txt site-packages
    cd ..
    
    # Upload the zip file to GCS
    gsutil cp $FUNCTION.zip gs://$FUNCTION-source/
    
    # Clean up
    rm -rf tmp_package tmp_venv $FUNCTION.zip
    deactivate
    
    cd ../..
done

echo "All functions have been built and uploaded."