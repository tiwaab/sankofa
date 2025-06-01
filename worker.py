# worker.py
import os
import logging
from pathlib import Path
from src.book_digitizer import BookDigitizer  
import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    bucket = os.getenv('S3_BUCKET') or os.getenv('BUCKET_NAME')
    pdf_key = os.getenv('S3_KEY')
    batch_size = int(os.getenv('BATCH_SIZE', 20))
    tmp_dir = os.getenv('TMP_DIR', '/tmp')

    if not bucket or not pdf_key:
        raise ValueError("S3_BUCKET (or BUCKET_NAME) and S3_KEY env vars must be set")

    logger.info(f"Starting digitization task for bucket: {bucket}, key: {pdf_key}")
    
    s3 = boto3.client('s3')
    download_path = Path(tmp_dir) / Path(pdf_key).name
    logger.info(f"Downloading PDF to {download_path}")
    s3.download_file(bucket, pdf_key, str(download_path))

    book_name = pdf_key.split('/')[0]
    logger.info(f"Initializing BookDigitizer for book: {book_name}")
    digitizer = BookDigitizer(source_pdf=download_path, book_name=book_name, gcs_bucket_name="book-digitzation-bucket")
    
    logger.info(f"Running batch_and_upload_pdf with batch_size={batch_size}")
    digitizer.batch_and_upload_pdf(batch_size=batch_size)
    logger.info("Batch upload complete")

    logger.info("Extracting images from original PDF")
    digitizer.extract_and_upload_images()
    logger.info("Image extraction complete")

    logger.info("Processing text with Google and LLM")
    digitizer.process_with_ocr()
    logger.info("LLM processing complete")
    
    logger.info("Linking images to content")
    digitizer.link_images_to_content()
    logger.info("Image linking complete")
    
    logger.info("Creating chapter structure")
    digitizer.create_quarto_chapters()
    logger.info("Chapter creation complete")
    
    logger.info("Creating final Quarto book")
    digitizer.create_quarto_book()
    logger.info("Book creation complete")

if __name__ == "__main__":
    main()