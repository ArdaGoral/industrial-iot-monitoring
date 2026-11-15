idf_component_register(
    SRCS "gateway_main.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES nvs_flash esp_netif esp_event protocol_examples_common
)
