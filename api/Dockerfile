FROM python:3.12-alpine

EXPOSE 5000

WORKDIR /app

COPY * /app/

RUN pip install -r requirements.txt

RUN pip install --force-reinstall "typing_extensions<4.12"
