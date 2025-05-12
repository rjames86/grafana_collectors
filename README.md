
## MQTT

https://medium.com/@tomer.klein/docker-compose-and-mosquitto-mqtt-simplifying-broker-deployment-7aaf469c07ee

```
  mosquitto:
    image: eclipse-mosquitto
    hostname: mosquitto
    container_name: mosquitto
    restart: unless-stopped
    ports:
      - "1883:1883"
      - "9001:9001"
    volumes:
      - ./data/mosquitto:/etc/mosquitto
      - ./data/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf
```

In the mosquitto.conf file, it needs to have the following

```
low_anonymous false
password_file /etc/mosquitto/passwd
listener 1883 0.0.0.0
```

I added that last line since otherwise, I couldn't access mqtt outside of the local machine.

To add more users, run

```
docker exec mosquitto mosquitto_passwd -b /etc/mosquitto/passwd user password
```

