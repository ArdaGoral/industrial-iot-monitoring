# Faz 3 - ESP32 gateway (ESP-IDF projesi)
cmake_minimum_required(VERSION 3.16)

# Espressif'in örnek bağlantı modülü: Ethernet'i (QEMU'da sanal open_eth) başlatıp IP alır
set(EXTRA_COMPONENT_DIRS $ENV{IDF_PATH}/examples/common_components/protocol_examples_common)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32_gateway)
