/*
 * USART1 sürücüsü (PA9 = TX), register seviyesi.
 * Bu dosya sadece donanımı bilir; FreeRTOS'tan haberi yoktur.
 */
#include "stm32f1xx.h"
#include "uart.h"

#define UART_CLOCK_HZ  8000000UL   /* dahili 8 MHz osilatör (HSI) */
#define UART_BAUD      115200UL

void uart_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_USART1EN;

    /* PA9: alternatif fonksiyon, push-pull, 50 MHz -> 0xB (CRH bit 4..7) */
    GPIOA->CRH &= ~(0xFUL << 4);
    GPIOA->CRH |=  (0xBUL << 4);

    USART1->BRR = UART_CLOCK_HZ / UART_BAUD;
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
