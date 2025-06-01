#book_digitizer.py
import boto3
import re
from botocore.exceptions import ClientError
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
import os
import json
import yaml
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        self.s3 = boto3.client('s3')
        self.batch_metadata = []
        self.parsed_content = []
        
        # LLM Parser
        from src.llm_parser import LLMParser
        self.llm_parser = LLMParser()
        
        # Chapter tracking
        self.toc_mapping = {}
        self.current_chapter = None

    def _sanitize(self, name: str) -> str:
        """Clean book name"""
        return re.sub(r'[^a-zA-Z0-9_\-]', '-', name.strip().lower())

    def batch_and_upload_pdf(self, batch_size=20, expiration=3600):
        """
        Split PDF into batches, upload each to S3, and track page ranges + URLs.
        
        Args:
            batch_size: number of pages to include in a batch
        """
        try:
            input_pdf = PdfReader(self.source_pdf)
        except:
            logger.warning("PdfReader doesn't work in the container")

        num_batches = (len(input_pdf.pages) + batch_size - 1) // batch_size

        for b in range(num_batches):
            writer = PdfWriter()
            start_page = b * batch_size
            end_page = min(start_page + batch_size, len(input_pdf.pages))

            for i in range(start_page, end_page):
                writer.add_page(input_pdf.pages[i])

            batch_filename = f"{self.source_pdf.stem}-batch-{b+1}.pdf"
            batch_path = self.source_pdf.parent / batch_filename
            s3_key = f"{self.book_name}/input/batches/{batch_filename}"

            with open(batch_path, 'wb') as f:
                writer.write(f)

            try:
                self.s3.upload_file(str(batch_path), self.bucket_name, s3_key)
            except ClientError as e:
                logger.error(f"Upload failed: {e}")  # Change print() to logger.error()
                raise
            os.remove(batch_path)
            
            s3_uri = f"s3://{self.bucket_name}/{s3_key}"

            self.batch_metadata.append({
                "s3_uri": s3_uri,
                "start_page": start_page + 1,
                "end_page": end_page 
            })

            logger.info(f"Uploaded batch {b+1}: pages {start_page}–{end_page - 1}")

    def extract_and_upload_images(self):
        """
        Extract images from the original PDF using Mistral OCR and upload to S3
        """
        from mistralai import Mistral
        import base64
        
        # Generate presigned URL for the original PDF
        pdf_key = f'{self.book_name}/input/full_book.pdf'
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
                    logger.info(f"Uploaded image: {s3_key}")
        
        logger.info(f"Extracted and uploaded {image_count} images")


    def process_batch_with_llm(self, textract_output, batch_start_page, batch_end_page, is_first_batch=False):
        """Process batch using LLM parser"""
        
        if is_first_batch:
            parsed_data = self.llm_parser.parse_first_batch(
                textract_output,
                batch_start_page,
                batch_end_page,
                self.book_name
            )
        else:
            parsed_data = self.llm_parser.parse_subsequent_batch(
                textract_output,
                batch_start_page,
                batch_end_page,
                self.book_name,
                self.toc_mapping,
                self.current_chapter
            )
        
        # Update context for next batch
        if parsed_data.get('toc_extracted'):
            self.toc_mapping.update(parsed_data['toc_extracted'])

        # Update current chapter from last page
        if parsed_data.get('pages'):
            self.current_chapter = parsed_data['pages'][-1].get('chapter')

        return parsed_data

    def save_parsed_content(self, parsed_data, batch_num):
        """Save parsed content to S3"""
        output_key = f"{self.book_name}/output/parsed/batch_{batch_num}_parsed.json"
        
        # Convert Pydantic model to dict if needed
        if hasattr(parsed_data, 'model_dump'):
            data_dict = parsed_data.model_dump()
        else:
            data_dict = parsed_data
        
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=output_key,
            Body=json.dumps(data_dict, indent=2),
            ContentType='application/json'
        )
        
        logger.info(f"Saved parsed content: {output_key}")
        return output_key

    def process_with_textract(self):
        """Enhanced version that includes LLM processing"""
        from src.textract_processor import TextractProcessor
        processor = TextractProcessor(self.bucket_name, self.book_name)
        
        for i, batch_meta in enumerate(self.batch_metadata):
            s3_key = batch_meta['s3_uri'].replace(f"s3://{self.bucket_name}/", "")
            
            # Get Textract output
            textract_output_key = processor.process_batch(
                s3_key, 
                batch_meta['start_page'], 
                batch_meta['end_page']
            )
            
            if textract_output_key:
                # Get textract content
                textract_response = self.s3.get_object(Bucket=self.bucket_name, Key=textract_output_key)
                textract_output = textract_response['Body'].read().decode('utf-8')
                
                # Process with LLM
                parsed_data = self.process_batch_with_llm(
                    textract_output,
                    batch_meta['start_page'],
                    batch_meta['end_page'],
                    is_first_batch=(i == 0)
                )
                
                # Save parsed content
                self.save_parsed_content(parsed_data, i + 1)
                self.parsed_content.append(parsed_data.model_dump() if hasattr(parsed_data, 'model_dump') else parsed_data)

    def link_images_to_content(self):
        """Update parsed content with actual image URLs"""
        # Get list of uploaded images
        image_prefix = f"{self.book_name}/output/images/"
        try:
            image_objects = self.s3.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=image_prefix
            )
        except:
            logger.warning("No images found")
            return
        
        if 'Contents' not in image_objects:
            return
        
        # Create mapping of page numbers to images
        page_images = {}
        for obj in image_objects['Contents']:
            filename = obj['Key'].split('/')[-1]
            if filename.startswith('page_'):
                page_num = int(filename.split('_')[1])
                if page_num not in page_images:
                    page_images[page_num] = []
                page_images[page_num].append({
                    'filename': filename,
                    'url': f"images/{filename}"
                })
        
        # Update parsed content
        for i, batch in enumerate(self.parsed_content):
            if isinstance(batch, dict) and 'pages' in batch:
                for page in batch['pages']:
                    if isinstance(page, dict) and 'page_number' in page:
                        page_num = int(page['page_number'])
                        if page_num in page_images:
                            image_markdown = '\n'.join([
                                f"![Image {j+1}]({img['url']})" 
                                for j, img in enumerate(page_images[page_num])
                            ])
                            page['content'] = f"{image_markdown}\n\n{page['content']}"
            
            # Save updated content
            self.save_parsed_content(batch, i + 1)

    def create_quarto_chapters(self):
        """Create individual chapter files based on chapter_start markers"""
        # Combine all pages from batches
        all_pages = []
        for batch in self.parsed_content:
            all_pages.extend(batch.get('pages', []))
        
        # Group pages by chapter, handling multiple chapters per batch
        chapters = {}
        current_chapter = None
        
        for page in all_pages:
            # Start new chapter if marked
            if page.get('chapter_start', False):
                current_chapter = page['chapter']
                if current_chapter not in chapters:
                    chapters[current_chapter] = []
            
            # Add page to current chapter
            if current_chapter:
                chapters[current_chapter].append(page)
        
        # Save chapter files
        for chapter_name, chapter_pages in chapters.items():
            content = "\n\n".join([page['content'] for page in chapter_pages])
            
            chapter_key = f"{self.book_name}/output/chapters/{chapter_name}.qmd"
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key=chapter_key,
                Body=content,
                ContentType='text/markdown'
            )
            logger.info(f"Created chapter: {chapter_key}")

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
        
        logger.info(f"Generated Quarto structure: {structure_key}")

    def create_quarto_book(self, output_dir="./quarto_book"):
        """Download parsed content and create local Quarto book, then upload to S3"""
        import os
        import yaml
        
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(f"{output_dir}/images", exist_ok=True)
        
        # Download all parsed JSON files
        all_pages = []
        for i in range(len(self.batch_metadata)):
            try:
                key = f"{self.book_name}/output/parsed/batch_{i+1}_parsed.json"
                response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
                batch_data = json.loads(response['Body'].read())
                if 'pages' in batch_data:
                    all_pages.extend(batch_data['pages'])
            except:
                continue
        
        # Create _quarto.yml
        config = {
            'project': {'type': 'book'},
            'book': {
                'title': self.book_name,
                'chapters': [page['filename'] for page in all_pages if page.get('filename')]
            }
        }
        
        with open(f"{output_dir}/_quarto.yml", 'w') as f:
            yaml.dump(config, f)
        
        # Create chapter files
        for page in all_pages:
            if page.get('filename'):
                with open(f"{output_dir}/{page['filename']}", 'w') as f:
                    f.write(page['content'])
        
        # Download images
        try:
            objects = self.s3.list_objects_v2(Bucket=self.bucket_name, Prefix=f"{self.book_name}/output/images/")
            for obj in objects.get('Contents', []):
                filename = obj['Key'].split('/')[-1]
                self.s3.download_file(self.bucket_name, obj['Key'], f"{output_dir}/images/{filename}")
        except:
            pass
        
        # Upload complete Quarto book to S3
        self._upload_directory_to_s3(output_dir, f"{self.book_name}/output/quarto_book")
        
        logger.info("✅ BOOK DIGITIZATION COMPLETE!")
        logger.info(f"📚 Quarto book created in {output_dir}")
        logger.info("🚀 Starting preview server...")
        logger.info(f"Manual command: cd {output_dir} && quarto preview")
    
    def _upload_directory_to_s3(self, local_dir, s3_prefix):
        """Upload entire directory to S3"""
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_dir)
                s3_key = f"{s3_prefix}/{relative_path}"
                
                self.s3.upload_file(local_path, self.bucket_name, s3_key)
                logger.info(f"Uploaded: {s3_key}")