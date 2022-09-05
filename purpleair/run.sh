#!/bin/bash

DELAY=1800 # 30 minutes

while (true); do
    echo "$(date) Fetching Purple Air data"
    python3 airquality.py
    echo "$(date) sleeping $DELAY seconds"
    sleep $DELAY
done
