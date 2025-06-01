import json
import time
import logging
from google.cloud import vision
from google.cloud import storage
import boto3

logger = logging.getLogger(__name__)

class GoogleVisionProcessor:
    def __init__(self, s3_bucket_name, gcs_bucket_name, book_name):
        self.s3_bucket_name = s3_bucket_name
        self.gcs_bucket_name = gcs_bucket_name
        self.book_name = book_name
        self.s3 = boto3.client('s3')
        self.vision_client = vision.ImageAnnotatorClient()
        self.storage_client = storage.Client()
    
    def process_batch(self, s3_key, start_page, end_page):
        """Process single batch with Google Vision and return structured text"""
        
        # Upload PDF to GCS
        gcs_input_path = f"{self.book_name}/input/{s3_key.split('/')[-1]}"
        gcs_output_path = f"{self.book_name}/vision_output/"
        
        self._upload_s3_to_gcs(s3_key, gcs_input_path)
        
        # Run Vision OCR
        gcs_source_uri = f"gs://{self.gcs_bucket_name}/{gcs_input_path}"
        gcs_destination_uri = f"gs://{self.gcs_bucket_name}/{gcs_output_path}"
        
        operation = self._run_vision_ocr(gcs_source_uri, gcs_destination_uri)
        
        if not operation:
            return None
        
        # Download and process results
        structured_text = self._extract_vision_text(gcs_output_path, start_page, end_page)
        
        # Save to S3
        batch_filename = s3_key.split('/')[-1].replace('.pdf', '')
        text_key = f"{self.book_name}/output/processed/{batch_filename}.txt"
        
        self.s3.put_object(
            Bucket=self.s3_bucket_name,
            Key=text_key,
            Body=structured_text,
            ContentType='text/plain'
        )
        
        logger.info(f"Saved processed text: {text_key}")
        return text_key
    
    def _upload_s3_to_gcs(self, s3_key, gcs_path):
        """Transfer PDF from S3 to GCS"""
        # Download from S3
        response = self.s3.get_object(Bucket=self.s3_bucket_name, Key=s3_key)
        pdf_data = response['Body'].read()
        
        # Upload to GCS
        bucket = self.storage_client.bucket(self.gcs_bucket_name)
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(pdf_data, content_type='application/pdf')
    
    def _run_vision_ocr(self, gcs_source_uri, gcs_destination_uri):
        """Run Google Vision OCR on GCS file"""
        feature = vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)
        
        gcs_source = vision.GcsSource(uri=gcs_source_uri)
        input_config = vision.InputConfig(gcs_source=gcs_source, mime_type="application/pdf")
        
        gcs_destination = vision.GcsDestination(uri=gcs_destination_uri)
        output_config = vision.OutputConfig(gcs_destination=gcs_destination, batch_size=20)
        
        async_request = vision.AsyncAnnotateFileRequest(
            features=[feature], 
            input_config=input_config, 
            output_config=output_config
        )
        
        try:
            operation = self.vision_client.async_batch_annotate_files(requests=[async_request])
            operation.result(timeout=420)
            return True
        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return False
    
    def _extract_vision_text(self, gcs_output_path, start_page, end_page):
        """Extract structured text from Vision results"""
        bucket = self.storage_client.bucket(self.gcs_bucket_name)
        
        # List output files
        blobs = list(bucket.list_blobs(prefix=gcs_output_path))
        json_blobs = [blob for blob in blobs if blob.name.endswith('.json')]
        
        if not json_blobs:
            return ""
        
        # Process first output file
        blob = json_blobs[0]
        json_string = blob.download_as_text()
        data = json.loads(json_string)
        
        # Extract text from each page
        result = []
        for i, response in enumerate(data.get('responses', [])):
            page_num = start_page + i
            if page_num > end_page:
                break
                
            result.append(f"\n--- PAGE {page_num} ---\n")
            
            if 'fullTextAnnotation' in response:
                text = response['fullTextAnnotation'].get('text', '')
                result.append(text)
        
        return '\n'.join(result)