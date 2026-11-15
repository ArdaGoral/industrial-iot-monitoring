/*
 * UART sürücüleri, register seviyesi.
 * Bu dosya sadece donanımı bilir; FreeRTOS'tan haberi yoktur.
 */
#include "stm32f1xx.h"
#include "uart.h"

#define UART_CLOCK_HZ  8000000UL   /* dahili 8 MHz osilatör (HSI) */
#define LOG_BAUD       115200UL
#define MODBUS_BAUD    9600UL      /* endüstride en yaygın Modbus hızı */

/* ---------------- USART1: log ---------------- */

void uart_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_USART1EN;

    /* PA9: alternatif fonksiyon, push-pull, 50 MHz -> 0xB (CRH bit 4..7) */
    GPIOA->CRH &= ~(0xFUL << 4);
    GPIOA->CRH |=  (0xBUL << 4);

    USART1->BRR = UART_CLOCK_HZ / LOG_BAUD;
    USART1->CR1 = USART_CR1_UE | USART_CR1_TE;
}

void uart_putc(char c)
{
    while (!(USART1->SR & USART_SR_TXE)) { }
    USART1->DR = (uint8_t)c;
}

void uart_puts(const char *s)
{
    while (*s) uart_putc(*s++);
}

void uart_put_uint(uint32_t n)
{
    char buf[11];
    int i = 0;
    do { buf[i++] = (char)('0' + n % 10); n /= 10; } while (n);
    while (i) uart_putc(buf[--i]);
}

/* ---------------- USART2: Modbus hattı ---------------- */

void uart2_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;      /* USART2, APB1 veri yolunda */

    /* PA2 (TX): alternatif fonksiyon push-pull 50 MHz -> 0xB  (CRL bit 8..11)
     * PA3 (RX): giriş, floating                      -> 0x4  (CRL bit 12..15) */
    GPIOA->CRL &= ~((0xFUL << 8) | (0xFUL << 12));
    GPIOA->CRL |=  (0xBUL << 8) | (0x4UL << 12);

    USART2->BRR = UART_CLOCK_HZ / MODBUS_BAUD;
    /* UE: aç, TE: gönderici, RE: alıcı, RXNEIE: her bayt gelince kesme üret */
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;
}

void uart2_write(const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        while (!(USART2->SR & USART_SR_TXE)) { }
        USART2->DR = data[i];
    }
    while (!(USART2->SR & USART_SR_TC)) { }    /* son bayt da tamamen çıksın */
}
