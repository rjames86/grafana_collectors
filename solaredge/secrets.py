from os import environ

solaredge_token = environ.get('SOLAREDGE_TOKEN')
solaredge_site_id = environ.get('SOLAREDGE_SITE_ID')


INFLUXDB_URL = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_URL")
influxdb_user = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_USERNAME")
influxdb_pass = environ.get("WEATHERFLOW_COLLECTOR_INFLUXDB_PASSWORD")
