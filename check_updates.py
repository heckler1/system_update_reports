#!/usr/bin/env python3
"""
This script runs "yum check-updates" on every server in our environment,
and uploads the data to a given location in S3
"""
import os
import boto3

##########
# CONFIG #
##########

regions = ["us-east-1", "us-west-1", "us-east-2", "us-west-2"]

# Get our config from the environment
bucket_name = os.environ["BUCKET_NAME"]
bucket_prefix = os.environ["BUCKET_PREFIX"]

########
# MAIN #
########

def lambda_handler(event, context): # pylint: disable=unused-argument
  """ The main function """
  # For every region given in the config above
  for region in regions:
    # Instantiate our SSM client
    ssm_client = boto3.client("ssm", region_name=region)

    # Get a list of instances managed by SSM in the given environment
    instance_information = ssm_client.describe_instance_information(MaxResults=50)
    # Pull out the instance info that we need
    information_list = instance_information["InstanceInformationList"]

    # Handle the pagination, as we can only get 50 instance IDs at a time
    while "NextToken" in instance_information:
      # Get the next page of results
      instance_information = ssm_client.describe_instance_information(
        MaxResults=50,
        NextToken=instance_information["NextToken"]
      )
      # Pull out the instance info that we need
      information_list += instance_information["InstanceInformationList"]

    # For every instance
    for instance in information_list:
      # Run "yum check-update" on the instance, and post the results to S3
      ssm_client.send_command(
        DocumentName="AWS-RunShellScript",
        DocumentVersion="$DEFAULT",
        Targets=[
          {
            "Key": "instanceids",
            "Values": [
              instance["InstanceId"]
            ]
          }
        ],
        Parameters={
          "workingDirectory": ['\\'],
          "executionTimeout": ["600"],
          "commands": [
            r"yum check-update &> /tmp/$(hostname)_$(date +%Y-%m-%d).log",
            fr"aws s3 cp /tmp/$(hostname)_$(date +%Y-%m-%d).log s3://{bucket_name}/{bucket_prefix}/$(hostname)_$(date +%Y-%m-%d).log", # pylint: disable=line-too-long
            r"rm /tmp/$(hostname)_$(date +%Y-%m-%d).log"
          ]
        },
        TimeoutSeconds=600,
        MaxConcurrency="50",
        MaxErrors="0"
      )
