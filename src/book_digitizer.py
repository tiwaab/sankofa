#book_digitizer.py
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
        self.book_name = book_name #TODO
        self.pdf_url = None
        self.parsed_book = None
        self.bucket_name = "book-digitization"
        self.s3 = boto3.client('s3')  # Initialize once
        self.batch_metadata = []


    def batch_and_upload_pdf(self, batch_size=20, expiration=3600):
        """
        Split PDF into batches, upload each to S3, and track page ranges + URLs.
        
        Args:
            batch_size: number of pages to include in a batch
        """
        try:
            input_pdf = PdfReader(self.source_pdf)
        except:
            print("PdfReader doesn't work in the container")

        num_batches = (len(input_pdf.pages) + batch_size - 1) // batch_size

        for b in range(num_batches):
            writer = PdfWriter()
            start_page = b * batch_size
            end_page = min(start_page + batch_size, len(input_pdf.pages))

            for i in range(start_page, end_page):
                writer.add_page(input_pdf.pages[i])

            batch_filename = f"{self.source_pdf.stem}-batch-{b+1}.pdf"
            batch_path = self.source_pdf.parent / batch_filename
            s3_key = f"{self.book_name}/input/batches/{batch_filename}" # Unique id for file

            with open(batch_path, 'wb') as f:
                writer.write(f)

            try:
                self.s3.upload_file(str(batch_path), self.bucket_name, s3_key)
            except ClientError as e:
                print(f"Upload failed: {e}")
                raise # Otherwise the code will keep trying to run
            os.remove(batch_path)
            
            # Get batch's uri
            s3_uri = f"s3://{self.bucket_name}/{s3_key}"

            self.batch_metadata.append({
                "s3_uri": s3_uri,
                "start_page": start_page + 1,
                "end_page": end_page 
            })

            print(f"Uploaded batch {b+1}: pages {start_page}–{end_page - 1}")
    

    def extract_and_upload_images(self):
        """
        Extract images from the original PDF using Mistral OCR and upload to S3
        """
        from mistralai import Mistral
        import base64
        
        # Generate presigned URL for the original PDF
        pdf_key = f"{self.book_name}/input/full_book.pdf"
        presigned_url = self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': pdf_key},
            ExpiresIn=3600
        )
        
        # Process with Mistral OCR
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        ocr_response = client.ocr.process(
            model="mistral-ocr-latest",
            document={"type": "document_url", "document_url": presigned_url},
            include_image_base64=True
        )
        
        ocr_response = ocr_response.model_dump()
        
        # Extract and upload images
        image_count = 0
        for page_idx, page in enumerate(ocr_response['pages']):
            if 'images' in page:
                for img_idx, img_data in enumerate(page['images']):
                    # Decode base64 image
                    encoded = img_data['image_base64'].split(",", 1)[1]
                    decoded = base64.b64decode(encoded)
                    
                    # Generate filename
                    filename = f"page_{page_idx + 1}_image_{img_idx + 1}.jpg"
                    s3_key = f"{self.book_name}/output/images/{filename}"
                    
                    # Upload to S3
                    self.s3.put_object(
                        Bucket=self.bucket_name, 
                        Key=s3_key, 
                        Body=decoded, 
                        ContentType="image/jpeg"
                    )
                    
                    image_count += 1
                    print(f"Uploaded image: {s3_key}")
        
        print(f"Extracted and uploaded {image_count} images")