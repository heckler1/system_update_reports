#!/usr/bin/env python3
"""
This script logs into the given servers, gets a list of what updates are required,
and parses all the output into a JSON report
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
import smtplib
import ssl
import fabric

##########
# CONFIG #
##########

# Get our config from the environment
# The email address this app will send as
# Also used as the SMTP username
email_from_address = os.environ["EMAIL_FROM"]
# The destination address
email_to_address = os.environ["EMAIL_TO"]

# SMTP config
smtp_server = ""
smtp_port = 465
smtp_password = os.environ["SMTP_PASS"]

apt_servers = []
yum_servers = []

#############
# FUNCTIONS #
#############

def apt_update_filter(output: list) -> list:
  """
  This function takes a list of outputs from "apt list --upgradeable"
  and filters for only the relevant information about which packages need to be upgraded
  """
  # Remove any messages or other output aside from the package information
  for index, line in reversed(list(enumerate(output))):
    if (re.match(r"^WARNING: apt does not have a stable CLI interface.", line) \
      or re.match(r"^Listing\.\.\.$", line)) is not None:

      output.pop(index)

  # Remove any empty lines
  return list(filter(None, output))

####################################################################################################

def yum_update_filter(output: list) -> list:
  """
  This function takes a list of outputs from "yum check-updates"
  and filters for only the relevant information about which packages need to be upgraded
  """
  # Remove any messages or other output aside from the package information
  for index, line in reversed(list(enumerate(output))):
    if (re.match(r"^Loaded plugins: ", line) \
      or re.match(r"^Loading mirror speeds from cached hostfile$", line) \
      or re.match(r"^ \*", line)) is not None:

      output.pop(index)

  # Remove any empty lines
  return list(filter(None, output))

####################################################################################################

def check_updates(server_list: list, package_manager: str) -> list:
  """
  This function logs into each of the given list of servers
  and checks to see if there are any updates required.

  Must be told which package manager the system is running.
  Supported values are "apt" and "yum"

  Outputs a list of JSON objects like this:
  {
    "hostname": "server.example.com",
    "update_list": update_list
  }
  Where "update_list" is a list of lines containing package information
  from the given package manager's output.

  From yum a line would look like:
  open-vm-tools.x86_64 10.3.0-2.el7_7.1 updates
  From apt a line looks like:
  systemd/bionic-updates 237-3ubuntu10.33 amd64 [upgradable from: 237-3ubuntu10.31]
  """
  # Check which command we need to run
  if package_manager == "apt":
    command = r'apt list --upgradeable'
  elif package_manager == "yum":
    command = r'yum check-updates'
  else:
    raise Exception("Unknown package manager")

  # Initialize our list of required updates
  total_update_list = []

  # For every server in our list
  for server in server_list:
    # Login and run our command that checks for updates
    try:
      update_list = fabric.Connection(server).run(command)
    except Exception as exception:
      # TODO: add a more useful failure message, like an email or something
      print(f"Failed to get {package_manager} update list for {server}")
      raise(exception)

    # Split by newline
    update_list = update_list.stdout.split("\n")

    # Filter the output to get only the list of required updates
    if package_manager == "apt":
      update_list = apt_update_filter(update_list)
    elif package_manager == "yum":
      update_list = yum_update_filter(update_list)
    else:
      raise Exception("Unknown package manager - how did you even get here??")

    # Add the list of updates to our master list, along with the hostname
    total_update_list.append({"hostname": server, "update_list": update_list})

  # Return an un-parsed, un-deduplicated list of updates, by system
  return total_update_list

####################################################################################################

def parse_apt_update_list(update_list: list) -> list:
  """
  Takes the output from check_updates() and puts it into a JSON structure to decorate it.

  An object in the inputted list looks something like:
  {
    "hostname": "server.example.com",
    "update_list": [ 'systemd/bionic-updates 237-3ubuntu10.33 amd64 [upgradable from: 237-3ubuntu10.31]' ]
  }

  Whereas an object in the outputted list would look like:
  {
    "hostname": "server.example.com",
    "update_list": [
      {
        "package_name": "systemd",
        "package_version": "237-3ubuntu10.33",
        "package_repo": "bionic-updates"
      }
    ]
  }
  """
  # Initialize a list to store our update info in
  all_updates = []

  # Every item in the update list is a set of information about a specific host
  for host_info in update_list:
    # Instantiate a list to put our JSON decorated package info into
    decorated_list = []

    # Every item in the host-specific update list is a line of package information
    for line in host_info["update_list"]:
      # If the line length post-cleanup is not the 6 segments that we're expecting,
      # we probably got some sort of error from Apt, so we'll output that directly
      if len(line.split()) != 6:
        decorated_line = {
          "error": ' '.join(line)
        }
      else:
        # Create a JSON object with the appropriate keys to decorate the info
        decorated_line = {
          "package_name": line.split('/')[0],
          "package_version": line.split()[1],
          "package_repo": line.split('/')[1].split()[0]
        }

      # Add to our list from before
      decorated_list.append(decorated_line)

    # Finally, add the hostname and decorated list of updates to our master list
    all_updates.append(
      {
        "hostname": host_info["hostname"],
        "update_list": decorated_list
      }
    )

  # Return our list of update info for all hosts
  return all_updates

####################################################################################################

def parse_yum_update_list(update_list: list) -> list:
  """
  Takes the output from check_updates() and puts it into a JSON structure to decorate it.

  An object in the inputted list looks something like:
  {
    "hostname": "server.example.com",
    "update_list": [ 'open-vm-tools.x86_64 10.3.0-2.el7_7.1 updates' ]
  }

  Whereas an object in the outputted list would look like:
  {
    "hostname": "server.example.com",
    "update_list": [
      {
        "package_name": "open-vm-tools.x86_64",
        "package_version": "10.3.0-2.el7_7.1",
        "package_repo": "updates"
      }
    ]
  }
  """
  # Initialize a list to store our update info in
  all_updates = []

  # Every item in the update list is a set of information about a specific host
  for host_info in update_list:
    # Instantiate a list to put our JSON decorated package info into
    decorated_list = []
    # For each update that the system needs
    for line in host_info["update_list"]:
      # Split by whitespace
      line = line.split()

      # If the line length post-cleanup is not the 3 items that we're expecting,
      # we probably got some sort of error from Yum, so we'll output that directly
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
    all_updates.append(
      {
        "hostname": host_info["hostname"],
        "update_list": decorated_list
      }
    )

  # Return our list of update information for every host
  return all_updates

####################################################################################################

def json_dedupe(update_list: list) -> list:
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
    receiver: list,
    title: str,
    text: str = None,
    html: str = None,
    attachments: list = None) -> MIMEMultipart:
  """
  From: https://stackoverflow.com/questions/42998170/how-to-send-html-text-and-attachment-using-boto3-send-email-or-send-raw-email # pylint: disable=line-too-long
  Creates a MIME multipart message object.
  Uses only the Python `email` standard library.
  Emails, both sender and receiver, can be just the email string
  or have the format 'The Name <the_email@host.com>'.

  :param sender: The sender.
  :param receiver: The recipient of the email.
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
  msg['To'] = receiver

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
    receiver: list,
    title: str,
    text: str = None,
    html: str = None,
    attachments: list = None) -> dict:
  """
  Send email to receiver via SMTP SSL
  """
  message = create_multipart_message(sender, receiver, title, text, html, attachments)

  # Create a secure SSL context
  context = ssl.create_default_context()

  with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
    server.login(sender, smtp_password)
    server.sendmail(sender, receiver, message)


########
# MAIN #
########

def main():
  """ The main function """

  # Check our servers for updates
  apt_updates = check_updates(apt_servers, "apt")
  yum_updates = check_updates(yum_servers, "yum")

  # Parse our package manager-specific output into our common structured format
  apt_updates = parse_apt_update_list(apt_updates)
  yum_updates = parse_yum_update_list(yum_updates)

  # Deduplicate seperately since they won't have updates in common
  deduplicated_apt_updates = json_dedupe(apt_updates)
  deduplicated_yum_updates = json_dedupe(yum_updates)

  # Combine the deduplicated sets
  all_updates = deduplicated_apt_updates + deduplicated_yum_updates

  # Get today's date
  today = datetime.date.today().strftime(r"%Y-%m-%d")

  # Build our file name and path that we will save the report to
  file_name = "server_update_report" + today + ".json"
  file_path = "/Users/stephen/" + file_name

  # Write the report to the file
  with open(file_path, 'w') as report_file:
    report_file.write(json.dumps(all_updates, indent=2))

  # Prep our email
  email_title = "Pending Server Updates"
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

  # Send the email
  send_mail(
    email_from_address,
    email_to_address,
    email_title,
    email_text,
    email_body,
    email_attachments
  )

if __name__ == "__main__":
  main()