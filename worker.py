# worker.py
import boto3
import requests
import os

s3 = boto3.client('s3')

def process_batch(bucket, key):
    # Download PDF batch
    local_file = "/tmp/batch.pdf"
    s3.download_file(bucket, key, local_file)

    # Your image extraction, Textract, formatting, Mistral call, etc.
    # Placeholder: just re-upload file to 'output' folder
    output_key = key.replace("input", "output")
    s3.upload_file(local_file, bucket, output_key)

    print("DONE")

if __name__ == "__main__":
    # Read environment variables passed by ECS
    bucket = os.environ["S3_BUCKET"]
    key = os.environ["S3_KEY"]
    process_batch(bucket, key)
