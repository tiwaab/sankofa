import boto3
import re
from botocore.exceptions import ClientError
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
from mistralai import Mistral
import os


class BookDigitizer:
    def __init__(self, source_pdf, book_name):
        """
        Args:
            source_pdf: Original pdf before it is split
        """
        self.source_pdf = source_pdf
        if not self.source_pdf.exists():
            raise FileNotFoundError(f"PDF not found at: {self.source_pdf.resolve()}")
        self.book_name = self._sanitize(book_name)
        self.pdf_url = None
        self.parsed_book = None
        self.bucket_name = "book-digitization"
        self.s3 = boto3.client('s3')  # Initialize once
        self.batch_metadata = []


    def _sanitize(self, name: str) -> str:
        """Clean book name"""
        return re.sub(r'[^a-zA-Z0-9_\-]', '-', name.strip().lower())


    def batch_and_upload_pdf(self, batch_size=20, expiration=3600):
        """
        Split PDF into batches, upload each to S3, and track page ranges + URLs.
        
        Args:
            batch_size: number of pages to include in a batch
        """
        
        input_pdf = PdfReader(self.source_pdf)
        num_batches = (len(input_pdf.pages) + batch_size - 1) // batch_size

        for b in range(num_batches):
            writer = PdfWriter()
            start_page = b * batch_size
            end_page = min(start_page + batch_size, len(input_pdf.pages))

            for i in range(start_page, end_page):
                writer.add_page(input_pdf.pages[i])

            batch_filename = f"{self.source_pdf.stem}-batch-{b+1}.pdf"
            batch_path = self.source_pdf.parent / batch_filename
            s3_key = f"{self.book_name}/input/{batch_filename}" # Unique id for file

            with open(batch_path, 'wb') as f:
                writer.write(f)
                # TODO MAKE SURE TO DELETE THESE LOCAL FILES WHEN DONE UPLOADING

            try:
                self.s3.upload_file(str(batch_path), self.bucket_name, s3_key)
            except ClientError as e:
                print(f"Upload failed: {e}")
                raise # Otherwise the code will keep trying to run
            
            # Get batch's presigned url
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )

            self.batch_metadata.append({
                "url": url,
                "start_page": start_page + 1,
                "end_page": end_page 
            })

            print(f"Uploaded batch {b+1}: pages {start_page}–{end_page - 1}")