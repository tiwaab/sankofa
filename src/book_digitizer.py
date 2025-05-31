#book_digitizer.py
import boto3
import re
from botocore.exceptions import ClientError
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
from mistralai import Mistral
import os
import json
import yaml


class BookDigitizer:
    def __init__(self, source_pdf, book_name):
        """
        Args:
            source_pdf: Original pdf before it is split
        """
        self.source_pdf = source_pdf
        if not self.source_pdf.exists():
            raise FileNotFoundError(f"PDF not found at: {self.source_pdf.resolve()}")
        self.book_name = book_name
        self.pdf_url = None
        self.parsed_book = None
        self.bucket_name = "book-digitization"
        self.s3 = boto3.client('s3')
        self.batch_metadata = []
        self.parsed_content = []


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


    def load_parsing_prompt(self):
        """Load the parsing prompt from file"""
        prompt_path = Path("src/parse_prompt.txt")
        with open(prompt_path, 'r') as f:
            return f.read()
    
    def process_with_llm(self, textract_output, batch_start_page, batch_end_page):
        """Process Textract output with LLM using parsing prompt"""
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        
        # Load prompt
        system_prompt = self.load_parsing_prompt()
        
        # Prepare user message with textract output
        user_message = f"""
        Process these pages ({batch_start_page}-{batch_end_page}) from the book "{self.book_name}":
        
        {textract_output}
        
        Return valid JSON following the specified structure.
        """
        
        response = client.chat.complete(
            model="mistral-large-latest",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            response_format={"type": "json_object"}
        )
        
        # Add error handling
        if not response.choices or not response.choices[0].message.content:
            raise ValueError(f"Empty response from LLM for pages {batch_start_page}-{batch_end_page}")
        
        return json.loads(response.choices[0].message.content) # type: ignore
    
    def save_parsed_content(self, parsed_data, batch_num):
        """Save parsed content to S3"""
        output_key = f"{self.book_name}/output/parsed/batch_{batch_num}_parsed.json"
        
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=output_key,
            Body=json.dumps(parsed_data, indent=2),
            ContentType='application/json'
        )
        
        print(f"Saved parsed content: {output_key}")
        return output_key
    
    def process_with_textract(self):
        """Enhanced version that includes LLM processing"""
        from src.textract_processor import TextractProcessor
        processor = TextractProcessor(self.bucket_name, self.book_name)
        
        for i, batch_meta in enumerate(self.batch_metadata):
            s3_key = batch_meta['s3_uri'].replace(f"s3://{self.bucket_name}/", "")
            
            # Get Textract output
            textract_output = processor.process_batch(
                s3_key, 
                batch_meta['start_page'], 
                batch_meta['end_page']
            )
            
            if textract_output:
                # Process with LLM
                parsed_data = self.process_with_llm(
                    textract_output,
                    batch_meta['start_page'],
                    batch_meta['end_page'] 
                )
                
                # Save parsed content
                self.save_parsed_content(parsed_data, i + 1)
                self.parsed_content.append(parsed_data)


    def link_images_to_content(self):
        """Update parsed content with actual image URLs"""
        # Get list of uploaded images
        image_prefix = f"{self.book_name}/output/images/"
        image_objects = self.s3.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=image_prefix
        )
        
        if 'Contents' not in image_objects:
            return
        
        # Create mapping of page numbers to images
        page_images = {}
        for obj in image_objects['Contents']:
            filename = obj['Key'].split('/')[-1]
            # Extract page number from filename like "page_1_image_1.jpg"
            if filename.startswith('page_'):
                page_num = int(filename.split('_')[1])
                if page_num not in page_images:
                    page_images[page_num] = []
                page_images[page_num].append({
                    'filename': filename,
                    'url': f"images/{filename}"
                })
        
        # Update parsed content with image links
        for batch in self.parsed_content:
            for page in batch.get('pages', []):
                page_num = int(page['page_number'])
                if page_num in page_images:
                    # Insert images at start of page content
                    image_markdown = '\n'.join([
                        f"![Image {i+1}]({img['url']})" 
                        for i, img in enumerate(page_images[page_num])
                    ])
                    page['content'] = f"{image_markdown}\n\n{page['content']}"
        
        # Save updated content
        for i, batch in enumerate(self.parsed_content):
            self.save_parsed_content(batch, i + 1)

    
    def generate_quarto_structure(self):
        """Generate Quarto book structure from parsed content"""
        # Combine all parsed batches
        all_pages = []
        all_images = []
        
        for batch in self.parsed_content:
            all_pages.extend(batch.get('pages', []))
            all_images.extend(batch.get('images', []))
        
        # Create Quarto config
        quarto_config = {
            "project": {"type": "book"},
            "book": {
                "title": all_pages[0].get('title', self.book_name) if all_pages else self.book_name,
                "chapters": [page['filename'] for page in all_pages]
            }
        }
        
        # Save structure
        structure_key = f"{self.book_name}/output/quarto_structure.json"
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=structure_key,
            Body=json.dumps(quarto_config, indent=2),
            ContentType='application/json'
        )
        
        print(f"Generated Quarto structure: {structure_key}")
    

    def create_quarto_book(self, output_dir="./quarto_book"):
        """Download parsed content and create local Quarto book"""
        
        # Create directories
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(f"{output_dir}/images", exist_ok=True)
        
        # Download and combine all parsed content
        all_pages = []
        for i, batch in enumerate(self.parsed_content):
            all_pages.extend(batch.get('pages', []))
        
        # Create _quarto.yml
        config = {
            'project': {'type': 'book'},
            'book': {
                'title': all_pages[0].get('title', self.book_name) if all_pages else self.book_name,
                'chapters': [page['filename'] for page in all_pages]
            }
        }
        
        with open(f"{output_dir}/_quarto.yml", 'w') as f:
            yaml.dump(config, f)
        
        # Create chapter files
        for page in all_pages:
            with open(f"{output_dir}/{page['filename']}", 'w') as f:
                f.write(page['content'])
        
        # Download images
        image_prefix = f"{self.book_name}/output/images/"
        try:
            objects = self.s3.list_objects_v2(Bucket=self.bucket_name, Prefix=image_prefix)
            for obj in objects.get('Contents', []):
                filename = obj['Key'].split('/')[-1]
                self.s3.download_file(self.bucket_name, obj['Key'], f"{output_dir}/images/{filename}")
        except:
            print("No images found")
        
        print(f"Quarto book created in {output_dir}")
        print("To build and serve:")
        print(f"cd {output_dir} && quarto preview")
        print(f"✅ BOOK DIGITIZATION COMPLETE!")
        print(f"📚 Quarto book created in {output_dir}")
        print(f"🚀 Starting preview server...")
        
        # Auto-open preview
        import subprocess
        import time
        try:
            subprocess.Popen(['quarto', 'preview', output_dir], 
                            stdout=subprocess.DEVNULL, 
                            stderr=subprocess.DEVNULL)
            time.sleep(3)  # Give it time to start
            print(f"📖 Book available at: http://localhost:3000")
        except:
            print(f"Manual command: cd {output_dir} && quarto preview")