
import json
import boto3
from src.book_digitizer import BookDigitizer 

s3 = boto3.client('s3')


def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    # Assuming key = book_name/full_book.pdf
    book_name = key.split('/')[0]

    # Download the full PDF locally (in /tmp for Lambda)
    download_path = f"/tmp/{key.split('/')[-1]}"
    s3.download_file(bucket, key, download_path)

    # Initialize and run digitizer
    digitizer = BookDigitizer(source_pdf=download_path, book_name=book_name)
    digitizer.batch_and_upload_pdf()

    return {
        'statusCode': 200,
        'body': json.dumps('Batch upload complete')
    }
