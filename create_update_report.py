#!/usr/bin/env python3
"""
This script creates a deduplicated JSON report of which updates are due on your systems, and emails it to you. 

See the README for more information, including configuration instructions.
"""
import argparse
import collections
import datetime
import itertools
import json
import os
import re
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import fabric
import paramiko
import yaml

#############
# FUNCTIONS #
#############

def get_config(config_file_path: str = None) -> dict:
  """
  This function gets our configuration information from both the environment and, if provided, a config file.
  It then compiles all the information into a single dictionary containing all config values.
  All config from the environment is prioritized.
  """

  # Load our config file, if given one
  if config_file_path is not None:
    with open(config_file_path, 'r') as config:
      config_file = yaml.safe_load(config)
  else:
    # Default to an empty dict to make the rest of the parsing easier
    config_file = {}

  ## EMAIL SETTINGS ##
  # The email address this app will send as
  if "EMAIL_FROM" in os.environ:
    email_from_address = os.environ["EMAIL_FROM"]
  elif "email" in config_file:
    if "from_address" in config_file["email"]:
      email_from_address = config_file["email"]["from_address"]
  else:
    raise Exception("No email from address found")

  # The destination address
  if "EMAIL_TO" in os.environ:
    email_to_address = os.environ["EMAIL_TO"]
  elif "email" in config_file:
    if "to_address" in config_file["email"]:
      email_to_address = config_file["email"]["to_address"]
  else:
    raise Exception("No email to address found")

  ## SMTP SETTINGS ##
  # SMTP server address
  if "SMTP_SERVER" in os.environ:
    smtp_server = os.environ["SMTP_SERVER"]
  elif "smtp" in config_file:
    if "server" in config_file["smtp"]:
      smtp_server = config_file["smtp"]["server"]
  else:
    raise Exception("Unable to find SMTP server")

  # SMTP username
  if "SMTP_USER" in os.environ:
    smtp_username = os.environ["SMTP_USER"]
  elif "smtp" in config_file:
    if "username" in config_file["smtp"]:
      smtp_username = config_file["smtp"]["username"]
  else:
    raise Exception("Unable to find SMTP username")

  # SMTP password
  if "SMTP_PASS" in os.environ:
    smtp_password = os.environ["SMTP_PASS"]
  elif "smtp" in config_file:
    if "password" in config_file["smtp"]:
      smtp_password = config_file["smtp"]["password"]
  else:
    raise Exception("Unable to find SMTP password")

  # SMTP port
  if "SMTP_PORT" in os.environ:
    smtp_port = os.environ["SMTP_PORT"]
  elif "smtp" in config_file:
    if "port" in config_file["smtp"]:
      smtp_port = config_file["smtp"]["port"]
  else:
    smtp_port = 465

  ## SSH SETTINGS ##
  # SSH username
  if "SSH_USER" in os.environ:
    ssh_username = os.environ["SSH_USER"]
  elif "ssh" in config_file:
    if "username" in config_file["ssh"]:
      ssh_username = config_file["ssh"]["username"]
  else:
    raise Exception("Unable to find SSH username")

  # SSH key path
  if "SSH_KEY_PATH" in os.environ:
    ssh_key_path = os.environ["SSH_KEY_PATH"]
  elif "ssh" in config_file:
    if "key_path" in config_file["ssh"]:
      ssh_key_path = config_file["ssh"]["key_path"]
  else:
    raise Exception("Unable to find SSH key path")

  # Build out our config dictionary
  config_dict = {
    "email": {
      "from_address": email_from_address,
      "to_address": email_to_address
    },
    "smtp": {
      "server": smtp_server,
      "port": smtp_port,
      "username": smtp_username,
      "password": smtp_password
    },
    "ssh": {
      "username": ssh_username,
      "key_path": ssh_key_path
    }
  }

  ## GET SERVER LISTS ##
  if "YUM_SERVERS" in os.environ:
    try:
      config_dict["yum_servers"] = json.loads(os.environ["YUM_SERVERS"])
    except:
      raise Exception("Unable to parse YUM_SERVERS environment variable as JSON list")
  elif "yum_servers" in config_file:
    config_dict["yum_servers"] = config_file["yum_servers"]
  else:
    print("No Yum servers found")

  if "APT_SERVERS" in os.environ:
    try:
      config_dict["apt_servers"] = json.loads(os.environ["APT_SERVERS"])
    except:
      raise Exception("Unable to parse APT_SERVERS environment variable as JSON list")
  elif "apt_servers" in config_file:
    config_dict["apt_servers"] = config_file["apt_servers"]
  else:
    print("No Apt servers found")

  return config_dict

####################################################################################################

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
      or re.match(r"^ \*", line) \
      or re.match(r"^Determining fastest mirrors", line)) is not None:

      output.pop(index)

  # Remove any empty lines
  return list(filter(None, output))

####################################################################################################

def check_updates(
  server_list: list,
  package_manager: str,
  private_key: paramiko.PKey,
  username: str) -> list:
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
    command_kwargs = {
      "command": "apt list --upgradeable",
      "hide": "both"
    }
  elif package_manager == "yum":
    command_kwargs = {
      "command": "yum check-updates",
      # We have to warn for exit value instead of raise,
      # because Yum returns exit code 100 if there are pending updates
      "warn":True,
      "hide": "stdout"
    }
  else:
    raise Exception("Unknown package manager")

  # Initialize our list of required updates
  total_update_list = []

  # For every server in our list
  for server in server_list:
    # Login and run our command that checks for updates
    try:
      update_list = fabric.Connection(server, user=username, connect_kwargs={"pkey": private_key}).run(**command_kwargs)
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
    total_update_list.append(
      {
        "hostname": server,
        "update_list": update_list
      }
    )

  # Return an un-parsed, un-deduplicated list of updates, by system
  return total_update_list

####################################################################################################

def parse_apt_update_list(update_list: list) -> list:
  """
  Takes the output from check_updates() and formats it into a JSON structure to decorate it.

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

      # Add to our list we initialized before
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

      # Add to our list we initialized before
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

def dedupe_by_host(update_list: list) -> list:
  """
  This function deduplicates our list of update information by hosts that need a given update.

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
  master_update_list = [ list_item['update_list'] for list_item in update_list ]

  # Combine the list of updates per hostname into one huge list, including duplicates
  master_update_list = list(
    itertools.chain.from_iterable(master_update_list)
  )

  # Convert every update descriptor object to a string,
  # so that it can be a key in collections.Counter's dictionary
  for index, item in enumerate(master_update_list):
    master_update_list[index] = json.dumps(item)

  # Iterate through our list of update objects
  # collections.Counter is necessary here, instead of enumerate(), because it deduplicates
  # the list before we iterate through it, similar to set(<LIST>)
  for update_item in collections.Counter(master_update_list):

    # Initialize our list and count of hosts that need the update
    hostname_list = []
    host_count = 0

    # Find all entries in our list of host/update information
    # that contain the current update we're working on
    dupe_entries = [ list_item for list_item in update_list
                      if json.loads(update_item) in list_item['update_list'] ]

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

def dedupe_by_update_list(update_list: list) -> list:
  """
  This function deduplicates our list of update information by updates needed by a given set of hosts.

  Example input update information:
  {
    "update_item": {
      "package_name": "dmsetup",
      "package_version": "2:1.02.145-4.1ubuntu3.18.04.2",
      "package_repo": "bionic-updates"
    },
    "hostnames": [
      "k3s-master",
      "k3s-node1",
      "k3s-node2",
      "k3s-node3"
    ],
    "host_count": 4
  },
  {
    "update_item": {
      "package_name": "grub-common",
      "package_version": "2.02-2ubuntu8.14",
      "package_repo": "bionic-updates"
    },
    "hostnames": [
      "k3s-master",
      "k3s-node1",
      "k3s-node2",
      "k3s-node3"
    ],
    "host_count": 4
  },

  Example deduplicated output:
  [
    {
      "update_list": [
        {
          "package_name": "dmsetup",
          "package_version": "2:1.02.145-4.1ubuntu3.18.04.2",
          "package_repo": "bionic-updates"
        },
        {
          "package_name": "grub-common",
          "package_version": "2.02-2ubuntu8.14",
          "package_repo": "bionic-updates"
        }
      ],
      "update_count": 2,
      "hostnames": [
        "k3s-master",
        "k3s-node1",
        "k3s-node2",
        "k3s-node3"
      ],
      "host_count": 4
    }
  ]
  """

  # Initialize some variables
  deduplicated_list = []

  # Get all of the sets of hostnames out of our data
  master_host_list = [ list_item['hostnames'] for list_item in update_list ]

  # Convert every hostname list to a string,
  # so that it can be a key in collections.Counter's dictionary
  for index, item in enumerate(master_host_list):
    master_host_list[index] = json.dumps(item)

  # Iterate through our list of hostname lists
  # collections.Counter is necessary here, instead of enumerate(), because it deduplicates
  # the list before we iterate through it, similar to set(<LIST>)
  for hostname_list_item in collections.Counter(master_host_list):

    # Initialize our list and count of updates that this set of hosts needs
    update_item_list = []
    update_item_count = 0

    # Find all entries in our list of host/update information
    # that contain the current set of hosts we're working on
    dupe_entries = [ list_item for list_item in update_list
                      if json.loads(hostname_list_item) == list_item['hostnames'] ]

    # Iterate through the list to find all sets of hosts that
    # require the same update, appending to our list initialized above
    for _, entry in enumerate(dupe_entries):
      # Log any update that this set of hosts needs
      if entry["update_item"] not in update_item_list:
        update_item_list.append(entry["update_item"])
        update_item_count += 1

    # Append the list of updates, plus the hosts that need them
    # to our master list of de-duplicated updates
    deduplicated_list.append(
      {
        "update_list": update_item_list,
        "update_count": update_item_count,
        "hostnames": json.loads(hostname_list_item),
        "host_count": len(json.loads(hostname_list_item))
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
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    message: MIMEMultipart
  ):
  """
  Send a pre-created MIMEMultipart message via SMTP SSL
  """

  # Create a secure SSL context
  context = ssl.create_default_context()

  with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
    server.login(smtp_username, smtp_password)
    server.sendmail(message.get("From"), message.get("To"), message.as_string())


########
# MAIN #
########

def main():
  """ The main function """

  # Get arguments
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "-c",
    "--config",
    required=False,
    help="Path to the config file"
  )
  args = parser.parse_args()

  # Parse our config
  if "config" in args:
    config = get_config(args.config)
  else:
    config = get_config()

  # Load our private key as an RSA key object
  private_key = paramiko.RSAKey.from_private_key_file(filename=config["ssh"]["key_path"])

  # Initialize our lists of updates
  deduplicated_apt_updates = []
  deduplicated_yum_updates = []

  # Check our servers for updates
  if "apt_servers" in config:
    apt_updates = check_updates(
      server_list=config["apt_servers"],
      package_manager="apt",
      username=config["ssh"]["username"],
      private_key=private_key
    )
    # Parse our package manager-specific output into our common structured format
    apt_updates = parse_apt_update_list(apt_updates)

    # Deduplicate by hosts per update
    deduplicated_apt_updates = dedupe_by_host(apt_updates)

    # Deduplicate again, this time by updates per set of hosts
    deduplicated_apt_updates = dedupe_by_update_list(deduplicated_apt_updates)

  if "yum_servers" in config:
    yum_updates = check_updates(
      server_list=config["yum_servers"],
      package_manager="yum",
      username=config["ssh"]["username"],
      private_key=private_key
    )
    # Parse our package manager-specific output into our common structured format
    yum_updates = parse_yum_update_list(yum_updates)
    # Deduplicate 
    deduplicated_yum_updates = dedupe_by_host(yum_updates)

    # Deduplicate again, this time by updates per set of hosts
    deduplicated_yum_updates = dedupe_by_update_list(deduplicated_apt_updates)

  # Combine the deduplicated sets
  all_updates = deduplicated_apt_updates + deduplicated_yum_updates

  # Get today's date
  today = datetime.date.today().strftime(r"%Y-%m-%d")

  # Build our file name and path that we will save the report to
  file_name = "server_update_report" + today + ".json"
  file_path = "/tmp/" + file_name

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

  message = create_multipart_message(
    config["email"]["from_address"],
    config["email"]["to_address"],
    email_title,
    email_text,
    email_body,
    email_attachments
  )

  # Send the email
  send_mail(
    config["smtp"]["server"],
    config["smtp"]["port"],
    config["smtp"]["username"],
    config["smtp"]["password"],
    message
  )

if __name__ == "__main__":
  main()
