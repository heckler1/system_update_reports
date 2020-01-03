FROM python:slim

RUN pip3 install fabric

ADD create_update_report.py /app

ENTRYPOINT [ "python3 /app/create_update_report.py" ]