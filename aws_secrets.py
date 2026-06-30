import json
import boto3

client = boto3.client("secretsmanager")

def get_db_config(secret_name):

    response = client.get_secret_value(
        SecretId=secret_name
    )

    return json.loads(response["SecretString"])
