#!/usr/bin/env python3
"""
This script retreives the output of "yum check-update"
from S3 and parses it into a JSON report
"""
import json
import os
import re
import itertools
import collections
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import boto3

##########
# CONFIG #
##########

# Get our config from the environment
bucket_name = os.environ["BUCKET_NAME"]
bucket_prefix = os.environ["BUCKET_PREFIX"]

# The email address this function will send as
email_from_address = "ElasticSearch Alerts <" + os.environ["FROM_ADDR"] + ">"
# A list of destination addresses
email_to_address = json.loads(os.environ["TO_ADDRS"])


#############
# FUNCTIONS #
#############

def json_dedupe(update_list):
  """
  This function deduplicates our list of update information by host.
  Example input update information:
  [
    {
      'hostname': 'ip-172-31-11-103',
      'update_list': [
        {
          'package_name': 'ca-certificates.noarch',
          'package_version': '2018.2.22-65.1.21.amzn1',
          'package_repo': 'amzn-updates'
        }
      ]
    }
  ]
  Example deduplicated output:
  [
    {
      "update_item": {
        "package_name": "ca-certificates.noarch",
        "package_version": "2018.2.22-65.1.21.amzn1",
        "package_repo": "amzn-updates"
      },
      "hostnames": [ "ip-172-31-11-103" ],
      "host_count": 1
    }
  ]
  """
  # Initialize some variables
  deduplicated_list = []

  # Get all of the update items out of our data
  master_update_list = [list_item['update_list'] for list_item in update_list]
  # Combine the list of updates per hostname into one huge list, including duplicates
  master_update_list = list(itertools.chain.from_iterable(master_update_list))
  # Convert every update descriptor object to a string
  for index, item in enumerate(master_update_list):
    master_update_list[index] = json.dumps(item)

  # Iterate through our list of update objects
  # collections.Counter is necessary here vs enumerate because it deduplicates
  # the list before we iterate through it, a la set(<LIST>)
  for update_item in collections.Counter(master_update_list):

    # Initialize our list and count of hosts that need the update
    hostname_list = []
    host_count = 0

    # Find all entries in our list of host/update information
    # that contain the current update we're working on
    dupe_entries = [list_item for list_item in update_list
                    if json.loads(update_item) in list_item['update_list']]

    # Iterate through the list to find all hosts that
    # require the same update, appending to our list initialized above
    for _, entry in enumerate(dupe_entries):
      # Log any host that needs the same update
      if entry["hostname"] not in hostname_list:
        hostname_list.append(entry["hostname"])
        host_count += 1

    # Append the update info, plus any hosts that need it
    # to our master list of de-duplicated updates
    deduplicated_list.append(
      {
        "update_item":json.loads(update_item),
        "hostnames": hostname_list,
        "host_count": host_count
      }
    )

  return deduplicated_list

####################################################################################################

def create_multipart_message(
    sender: str,
    recipients: list,
    title: str,
    text: str = None,
    html: str = None,
    attachments: list = None) -> MIMEMultipart:
  """
  From: https://stackoverflow.com/questions/42998170/how-to-send-html-text-and-attachment-using-boto3-send-email-or-send-raw-email # pylint: disable=line-too-long
  Creates a MIME multipart message object.
  Uses only the Python `email` standard library.
  Emails, both sender and recipients, can be just the email string
  or have the format 'The Name <the_email@host.com>'.

  :param sender: The sender.
  :param recipients: List of recipients. Needs to be a list, even if only one recipient.
  :param title: The title of the email.
  :param text: The text version of the email body (optional).
  :param html: The html version of the email body (optional).
  :param attachments: List of files to attach in the email.
  :return: A `MIMEMultipart` to be used to send the email.
  """
  multipart_content_subtype = 'alternative' if text and html else 'mixed'
  msg = MIMEMultipart(multipart_content_subtype)
  msg['Subject'] = title
  msg['From'] = sender
  msg['To'] = ', '.join(recipients)

  # Record the MIME types of both parts - text/plain and text/html.
  # According to RFC 2046, the last part of a multipart message,
  # in this case the HTML message, is best and preferred.
  if text:
    part = MIMEText(text, 'plain')
    msg.attach(part)
  if html:
    part = MIMEText(html, 'html')
    msg.attach(part)

    # Add attachments
  for attachment in attachments or []:
    with open(attachment, 'rb') as file_to_attach:
      part = MIMEApplication(file_to_attach.read())
      part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(attachment))
      msg.attach(part)

  return msg

####################################################################################################

def send_mail(
    sender: str,
    recipients: list,
    title: str,
    text: str = None,
    html: str = None,
    attachments: list = None) -> dict:
  """
  From: https://stackoverflow.com/questions/42998170/how-to-send-html-text-and-attachment-using-boto3-send-email-or-send-raw-email # pylint: disable=line-too-long
  Send email to recipients. Sends one mail to all recipients.
  The sender needs to be a verified email in SES.
  """
  msg = create_multipart_message(sender, recipients, title, text, html, attachments)
  ses_client = boto3.client('ses')
  return ses_client.send_raw_email(
    Source=sender,
    Destinations=recipients,
    RawMessage={'Data': msg.as_string()}
  )


########
# MAIN #
########

def lambda_handler(event, context): # pylint: disable=unused-argument
  """ The main function """
  # Instantiate our S3 client
  s3_client = boto3.client("s3")

  # Get a list of files inside the prefix. This should be individual log files from each host
  file_list = s3_client.list_objects_v2(
    Bucket=bucket_name,
    Prefix=bucket_prefix
  )["Contents"]

  # Initialize a list for our update information to be stored in
  updates = []

  today = datetime.date.today().strftime(r"%Y-%m-%d")

  # For each file in S3
  for file_desc in [file_desc for file_desc in file_list if today in file_desc["Key"]]:
    # Get the file's key
    key = file_desc["Key"]

    # Get the object out of S3, store it in a variable
    update_list = s3_client.get_object(
      Bucket=bucket_name,
      Key=key
    )["Body"].read().decode('utf-8')

    # Split by newline
    update_list = update_list.split("\n")

    # Remove any messages or other output aside from the package information
    for index, line in reversed(list(enumerate(update_list))):
      if (re.match(r"^Loaded plugins: ", line) \
        or re.match(r"^Loading mirror speeds from cached hostfile$", line) \
        or re.match(r"^ \*", line)) is not None:

        update_list.pop(index)

    # Remove any empty lines
    update_list = list(filter(None, update_list))

    # Instantiate a list to put our JSON decorated package info into
    decorated_list = []

    # For each update that the system needs
    for line in update_list:
      # Split by whitespace
      line = line.split()

      # If the line length post-cleanup is not the 3 items that we're expecting,
      # we probably got some sort of wierd error from Yum, so we'll output that directly
      if len(line) != 3:
        decorated_line = {
          "error": ' '.join(line)
        }
      else:
        # Create a JSON object with the appropriate keys to decorate the info
        decorated_line = {
          "package_name": line[0],
          "package_version": line[1],
          "package_repo": line[2]
        }

      # Add to our list from before
      decorated_list.append(decorated_line)

    # Finally, add the hostname and decorated list of updates to our master list
    updates.append(
      {
        "hostname": key.split('/')[-1].split("_")[0],
        "update_list": decorated_list
      }
    )

  deduplicated_updates = json_dedupe(updates)

  # Build our file name and path that we will save the report to
  file_name = "yum_update_report" + today + ".json"
  file_path = "/tmp/" + file_name

  # Write the report to the file
  with open(file_path, 'w') as report_file:
    report_file.write(json.dumps(deduplicated_updates, indent=2))

  # Prep our email
  email_title = "Pending Yum Updates"
  email_text = ""
  email_body = (
    "<html>"
      "<head>" # pylint: disable=bad-continuation
      "</head>"
      "<body>"
        "<p>"
          "This week's server update report is attached."
        "</p>"
      "</body>" # pylint: disable=bad-continuation
    "</html>"
  )
  email_attachments = [file_path]

  # Upload the report to S3
  s3_client = boto3.client('s3')
  s3_client.upload_file(
    file_path,
    bucket_name,
    bucket_prefix + "/" + file_name
  )

  # Send the email
  send_mail(
    email_from_address,
    email_to_address,
    email_title,
    email_text,
    email_body,
    email_attachments
  )
