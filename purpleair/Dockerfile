FROM python:slim

RUN pip3 install requests

WORKDIR /purpleair
COPY . /purpleair

RUN chmod a+x /purpleair/run.sh

CMD ["/purpleair/run.sh"]