UPSTREAM_COMMIT = "4a8e1061254004d9dac807054eed33fad7d1ff14"
RPNH_REVIEWED_COMMIT = "1da3648c401a1cecd323530f75181ad98af6d574"
PUBLIC_DOMAINS = ("sales", "marketing", "operations", "support", "finance", "hr")
TOOLS = ("api_search", "api_fetch", "base64_encode")
PLUGIN_VERSION = "0.2.0"
CONDITION_ID = "ab-public600-rpnh-native-api-no-cumulative-quota-v1"
# These are per-operation native/transport limits, never cumulative task quotas.
PLUGIN_RESULT_BYTES = 16 * 1024 * 1024  # native PluginOperation v1 maximum
FRAME_BYTES = PLUGIN_RESULT_BYTES + 1024 * 1024
OPERATION_TIMEOUT_SECONDS = 60
