#ifndef UART_H
#define UART_H

#include <stdint.h>
#include <stddef.h>

/* USART1 (PA9 = TX): log çıktısı */
void uart_init(void);
void uart_putc(char c);
void uart_puts(const char *s);
void uart_put_uint(uint32_t n);

/* USART2 (PA2 = TX, PA3 = RX): Modbus RTU hattı.
 * Gerçek kartta buraya MAX485 (RS485) modülü bağlanır. */
void uart2_init(void);
void uart2_write(const uint8_t *data, size_t len);

#endif /* UART_H */
