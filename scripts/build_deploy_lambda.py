import shutil
import boto3
import os
import sys
from pathlib import Path

def build_and_deploy_lambda(
    source_files=['lambda_function.py', 'book_digitizer.py', 'requirements.txt'],  # TODO adjust as needed
    build_dir='lambda_build',
    zip_name='lambda_package.zip',
    s3_bucket='your-s3-bucket-name',
    s3_key='lambda_package.zip',
    lambda_function_name='your-lambda-function-name',
    aws_region='us-east-1'
):
    build_path = Path(build_dir)
    zip_path = Path(zip_name)

    # Step 0: Create (or clear) the build directory
    if build_path.exists():
        shutil.rmtree(build_path)
    build_path.mkdir(parents=True, exist_ok=True)

    # Copy source files to build directory
    for f in source_files:
        src = Path(f)
        if src.exists():
            shutil.copy(src, build_path / src.name)
        else:
            print(f"Warning: source file {f} does not exist!")

    # Step 1: Create zip archive of the build directory
    if zip_path.exists():
        zip_path.unlink()

    print(f"Zipping {build_path} into {zip_path} ...")
    shutil.make_archive(str(zip_path.with_suffix('')), 'zip', str(build_path))

    # Step 2: Delete the build directory after zipping
    print(f"Deleting build directory {build_path} ...")
    shutil.rmtree(build_path)

    # Step 3: Upload zip to S3
    s3 = boto3.client('s3', region_name=aws_region)
    print(f"Uploading {zip_path} to s3://{s3_bucket}/{s3_key} ...")
    s3.upload_file(str(zip_path), s3_bucket, s3_key)

    # Step 4: Update Lambda function code from S3
    lambda_client = boto3.client('lambda', region_name=aws_region)
    print(f"Updating Lambda function '{lambda_function_name}' code ...")
    response = lambda_client.update_function_code(
        FunctionName=lambda_function_name,
        S3Bucket=s3_bucket,
        S3Key=s3_key,
        Publish=True
    )
    print("Lambda update response:", response)

    # Optional: Remove zip after deployment
    print(f"Removing zip file {zip_path} ...")
    zip_path.unlink()

if __name__ == "__main__":
    build_and_deploy_lambda(
        source_files=['../src/lambda_function.py', '../src/book_digitizer.py', '../requirements.txt'],
        build_dir='lambda_build',
        zip_name='lambda_package.zip',
        s3_bucket='book-digitization-lambda',
        s3_key='lambda_package.zip',
        lambda_function_name='bookDigitizationLambda',
        aws_region='us-east-1'
    )
