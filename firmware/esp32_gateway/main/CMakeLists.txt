idf_component_register(
    SRCS "gateway_main.c" "modbus_master.c" "modbus_proto.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES nvs_flash esp_netif esp_event esp_timer esp_driver_uart protocol_examples_common
)
