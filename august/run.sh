#!/bin/bash

DELAY=900 # 15 minutes


while (true); do
    echo "$(date) Fetching August lock data"
    python3 main.py
    echo "$(date) sleeping $DELAY seconds"
    sleep $DELAY
done
