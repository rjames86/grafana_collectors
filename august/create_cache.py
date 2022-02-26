from os import environ

from august.api import Api
from august.authenticator import Authenticator, AuthenticationState

AUGUST_USERNAME = environ.get("AUGUST_USERNAME")
AUGUST_PASSWORD = environ.get("AUGUST_PASSWORD")

api = Api(timeout=20)
authenticator = Authenticator(
    api,
    "email",
    AUGUST_USERNAME,
    AUGUST_PASSWORD,
    access_token_cache_file="auth_cache",
)
authentication = authenticator.authenticate()

state = authentication.state

if state == AuthenticationState.REQUIRES_VALIDATION:
    authenticator.send_verification_code()

    code = input("Enter in the code that was sent to you: ")
    validation_result = authenticator.validate_verification_code(code)
    authentication = authenticator.authenticate()
    print(authentication.state)
