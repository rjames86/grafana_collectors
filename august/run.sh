#!/bin/bash

DELAY=1800 # 30 minutes

while (true); do
    echo "$(date) Fetching August lock data"
    python3 main.py
    echo "$(date) sleeping $DELAY seconds"
    sleep $DELAY
done
