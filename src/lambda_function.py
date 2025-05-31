# lambda_function.py
import json
import boto3
import os
import ast


ecs = boto3.client('ecs')

CLUSTER_NAME = os.getenv('CLUSTER_NAME')
TASK_DEFINITION = os.getenv('TASK_DEFINITION')
SUBNETS = ast.literal_eval(os.getenv('SUBNETS')) # type: ignore
SECURITY_GROUPS = ast.literal_eval(os.getenv('SECURITY_GROUPS')) # type: ignore
LAUNCH_TYPE = 'FARGATE'

def lambda_handler(event, context):
    print("Received event:", json.dumps(event))
    print('\n')
    
    # Extract S3 bucket and key from the event
    s3_bucket = event['Records'][0]['s3']['bucket']['name']
    s3_key = event['Records'][0]['s3']['object']['key']
    book_name = s3_key.split('/')[0]

    # Pass the S3 file location as an environment variable or override
    overrides = {
        'containerOverrides': [
            {
                'name': 'book-digitization-container',
                'environment': [
                    {'name': 'S3_BUCKET', 'value': s3_bucket},
                    {'name': 'S3_KEY', 'value': s3_key},
                    {'name': 'BOOK_NAME', 'value': book_name},
                    {'name': 'MISTRAL_API_KEY', 'value': os.getenv('MISTRAL_API_KEY')}
                ]
            }
        ]
    }
    
    response = ecs.run_task(
        cluster=CLUSTER_NAME,
        taskDefinition=TASK_DEFINITION,
        launchType=LAUNCH_TYPE,
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': SUBNETS,
                'securityGroups': SECURITY_GROUPS,
                'assignPublicIp': 'ENABLED'
            }
        },
        overrides=overrides
    )
    
    print("ECS task started:", response['tasks'][0]['taskArn'])
    
    return {
        'statusCode': 200,
        'body': json.dumps('ECS task started successfully')
    }


