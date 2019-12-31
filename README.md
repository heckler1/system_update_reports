# System Update Reports/`check_update_report.py`

This script creates a report about which updates are due on your systems, and emails it to you. Packaged into a Docker container, this is ideally run as a cron job within a Kubernetes cluster, but can be deployed anywhere that Docker runs.

## Configuration
Most configuration is performed with environment variables:
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