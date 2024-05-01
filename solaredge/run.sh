#!/bin/bash

DELAY=600 # 10 minutes

while (true); do
    echo "$(date) Fetching SolarEdge data"
    python3 main.py --verbose
    echo "$(date) sleeping $DELAY seconds"
    sleep $DELAY
done
