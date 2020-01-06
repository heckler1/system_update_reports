# System Update Reports/`check_update_report.py`

This script creates a deduplicated JSON report of which updates are due on your systems, and emails it to you. 

It works by performing the following high-level steps:
- SSH into a given list of servers, and run the appropriate command for that server's package manger to check for package updates
- Parse the output of the commands into a JSON structure
- Deduplicate that dictionary based on package type, creating a list of hosts that need each package update.
- Deduplicate again, this time by set of hosts that need a given update.
- Email the report to the given address over SMTP SSL

Example dedupliated JSON:
``` json
{
  "update_item": {
    "package_name": "open-vm-tools.x86_64",
    "package_version": "10.3.0-2.el7_7.1",
    "package_repo": "updates"
  },
  "hostnames": [
    "server1",
    "server2",
    "server3"
  ],
  "host_count": 3
}
```

This script is packaged into a Docker container, and is ideally run as a cron job within a Kubernetes cluster for ease of management and scheduling. However, it can be deployed anywhere that Docker runs.

For ease of deployment, example Kubernetes manifests are included in `k8s_example/`. They include a cron job definition, and an associated secret to store the SMTP password in.

## Configuration
Configuration can be performed either with environment variables, or with a YAML config file. Environment variables always take precedent over the contents of the config file.

### Environment Variables
The names of the environment variables are as follows:
- `EMAIL_FROM`
  - The email address the script will send mail from
- `EMAIL_TO`
  - The email address the script will mail its report to
- `SMTP_USER`
  - The username to login to the SMTP server
- `SMTP_PASS`
  - The password to login to the SMTP server
- `SMTP_SERVER`
  - The SMTP server
- `SMTP_PORT` - Optional
  - The SMTP port. This defaults to 465 for SMTP over SSL
- `SSH_USER`
  - SSH username to use to login to the target systems
- `SSH_KEY_PATH`
  - Path to an SSH private key associated with the given user. Must point to a valid RSA key.
- `APT_SERVERS` and/or `YUM_SERVERS`
  - A JSON-parseable list of server hostnames
  - Example: `[ "server1", "server2.example.com" ]`

### YAML Config File
The path to the config file is passed to the script using the parameter `-c|--config`, like so:

``` shell
python3 create_update_report.py -c /config/config.yaml
```

See `example_config.yaml` for all available configuration directives and how to use them.
