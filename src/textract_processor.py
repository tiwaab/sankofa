import boto3
import time
import json

class TextractProcessor:
    def __init__(self, bucket_name, book_name):
        self.bucket_name = bucket_name
        self.book_name = book_name
        self.s3 = boto3.client('s3')
        self.textract = boto3.client('textract', region_name='us-east-1')
    
    def process_batch(self, s3_key, start_page, end_page):
        """Process single batch with Textract and return structured text"""
        # Start async text detection
        response = self.textract.start_document_text_detection(
            DocumentLocation={
                'S3Object': {
                    'Bucket': self.bucket_name,
                    'Name': s3_key
                }
            }
        )
        
        job_id = response['JobId']
        print(f"Started Textract job {job_id} for {s3_key}")
        
        # Poll for completion
        while True:
            result = self.textract.get_document_text_detection(JobId=job_id)
            status = result['JobStatus']
            
            if status == 'SUCCEEDED':
                # Get all blocks
                all_blocks = result['Blocks']
                next_token = result.get('NextToken')
                
                while next_token:
                    result = self.textract.get_document_text_detection(
                        JobId=job_id, NextToken=next_token
                    )
                    all_blocks.extend(result['Blocks'])
                    next_token = result.get('NextToken')
                
                # Extract structured text
                structured_text = self._extract_structured_text(all_blocks, start_page, end_page)
                
                # Save processed text
                batch_filename = s3_key.split('/')[-1].replace('.pdf', '')
                text_key = f"{self.book_name}/output/processed/{batch_filename}.txt"
                
                self.s3.put_object(
                    Bucket=self.bucket_name,
                    Key=text_key,
                    Body=structured_text,
                    ContentType='text/plain'
                )
                
                print(f"Saved processed text: {text_key}")
                return text_key
                
            elif status == 'FAILED':
                print(f"Textract job {job_id} failed")
                return None
                
            else:
                print(f"Job {job_id} status: {status}, waiting...")
                time.sleep(10)
    
    def _extract_structured_text(self, textract_blocks, start_page, end_page):
        """Extract text with page numbers and figure placeholders"""
        pages = {}
        
        # Group blocks by page
        for block in textract_blocks:
            if 'Page' in block:
                page_num = block['Page'] + start_page - 1
                if page_num not in pages:
                    pages[page_num] = {'lines': [], 'figures': []}
                
                if block['BlockType'] == 'LINE':
                    pages[page_num]['lines'].append(block['Text'])
                elif block['BlockType'] == 'WORD' and 'figure' in block['Text'].lower():
                    pages[page_num]['figures'].append(f"[FIGURE_PLACEHOLDER_{page_num}]")
        
        # Build structured text
        result = []
        for page_num in sorted(pages.keys()):
            result.append(f"\n--- PAGE {page_num} ---\n")
            result.extend(pages[page_num]['lines'])
            result.extend(pages[page_num]['figures'])
        
        return '\n'.join(result)