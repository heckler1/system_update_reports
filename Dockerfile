FROM python:slim

# Install our dependencies
RUN pip3 install fabric pyyaml

# Add our script
ADD create_update_report.py /app/create_update_report.py

ENTRYPOINT [ "python3", "/app/create_update_report.py" ]