FROM python:slim

RUN pip3 install fabric pyyaml

ADD create_update_report.py /app

ENTRYPOINT [ "python3 /app/create_update_report.py" ]