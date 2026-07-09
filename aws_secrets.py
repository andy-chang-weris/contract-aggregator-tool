import os
import json
import boto3

client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))

#client = boto3.client("secretsmanager")

def get_db_config(secret_name):

    response = client.get_secret_value(
        SecretId=secret_name
    )

    return json.loads(response["SecretString"])
