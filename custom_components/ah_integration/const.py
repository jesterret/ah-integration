DOMAIN = "ah_integration"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_TOKEN_TYPE = "token_type"
CONF_EXPIRES_AT = "expires_at"
CONF_MEMBER_ID = "member_id"

CONF_TRACKED_PRODUCTS = "tracked_products"
CONF_RECEIPT_COUNT = "receipt_count"
DEFAULT_RECEIPT_COUNT = 1

DEFAULT_SCAN_INTERVAL = 30

AH_AUTHORIZE_URL = (
    "https://login.ah.nl/login"
    "?client_id=appie-ios&redirect_uri=appie://login-exit&response_type=code"
)
