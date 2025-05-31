# main.py
import boto3
import argparse
from pathlib import Path
import re

def sanitize(name: str) -> str:
    """Clean book name"""
    return re.sub(r'[^a-zA-Z0-9_\-]', '-', name.strip().lower())

def upload_book(pdf_path: str, book_name: str, bucket: str):
    book_name = sanitize(book_name)
    s3 = boto3.client('s3')
    key = f"{book_name}/input/full_book.pdf"
    
    s3.upload_file(str(pdf_path), bucket, key)
    print(f"✅ Uploaded {book_name} to s3://{bucket}/{key}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload book PDF to S3 to trigger ECS processing")
    parser.add_argument('--pdf_path', required=True, help='Path to the book PDF')
    parser.add_argument('--book_name', required=True, help='Book name for structuring key')
    parser.add_argument('--bucket', default='book-digitization', help='S3 bucket name')

    args = parser.parse_args()
    upload_book(args.pdf_path, args.book_name, args.bucket)
