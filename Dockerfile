FROM python:slim

RUN pip3 install fabric pyyaml

ADD create_update_report.py /app/create_update_report.py

ENTRYPOINT [ "python3", "/app/create_update_report.py" ]