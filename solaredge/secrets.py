from os import environ

solaredge_token = environ.get('SOLAREDGE_TOKEN')
solaredge_site_id = environ.get('SOLAREDGE_SITE_ID')

MAX_DAYS_PER_REQUEST = int(environ.get("SOLAREDGE_MAX_DAYS_PER_REQUEST", "28"))
