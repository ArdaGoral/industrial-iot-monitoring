/*
 * Modbus RTU master - UART katmanı
 *
 * modbus_proto'nun ürettiği paketleri UART'tan gönderir, cevabı bekler.
 * Hatta aynı anda tek bir işlem olabileceği için bir mutex ile korunur.
 */
#ifndef MODBUS_MASTER_H
#define MODBUS_MASTER_H

#include <stdint.h>
#include "esp_err.h"
#include "driver/uart.h"
#include "modbus_proto.h"

esp_err_t mbm_init(uart_port_t port, int baud, int tx_pin, int rx_pin);

/* FC03 / FC04 */
mbm_result_t mbm_read(uint8_t slave, uint8_t fc, uint16_t start, uint16_t qty,
                      uint16_t *out, uint8_t *ex_code);

/* FC06 */
mbm_result_t mbm_write_single(uint8_t slave, uint16_t addr, uint16_t value, uint8_t *ex_code);

#endif /* MODBUS_MASTER_H */
