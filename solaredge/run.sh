#!/bin/bash

DELAY=1800 # 30 minutes

while (true); do
    echo "$(date) Fetching SolarEdge data"
    python3 main.py -v
    echo "$(date) sleeping $DELAY seconds"
    sleep $DELAY
done
