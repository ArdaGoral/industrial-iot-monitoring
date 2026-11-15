/*
 * Faz 2 - Adım 2: Blue Pill "merhaba"
 *
 * USART1 (PA9 = TX) üzerinden yazı gönderir ve PC13'teki LED'i yakıp söndürür.
 * HAL kullanmıyoruz: her şey register'lara doğrudan yazılarak yapılıyor.
 * Saat: dahili 8 MHz osilatör (HSI), hiçbir saat ayarı yapılmıyor.
 */

#include "stm32f1xx.h"

#define CPU_HZ   8000000UL
#define BAUD     115200UL

static void uart1_init(void)
{
    /* 1) Saat ver: GPIOA portu ve USART1 çevre birimi */
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_USART1EN;

    /* 2) PA9'u "alternatif fonksiyon, push-pull, 50 MHz" yap (USART1 TX).
     *    CRH register'ında her pin 4 bit: PA9 -> bit 4..7, değer 0b1011 = 0xB */
    GPIOA->CRH &= ~(0xFUL << 4);
    GPIOA->CRH |=  (0xBUL << 4);

    /* 3) Baud hızı ve USART'ı aç: UE = USART enable, TE = transmitter enable */
    USART1->BRR = CPU_HZ / BAUD;
    USART1->CR1 = USART_CR1_UE | USART_CR1_TE;
}

static void uart1_putc(char c)
{
    while (!(USART1->SR & USART_SR_TXE)) { }   /* gönderim tamponu boşalana kadar bekle */
    USART1->DR = (uint8_t)c;
}

static void uart1_puts(const char *s)
{
    while (*s) uart1_putc(*s++);
}

/* printf kullanmadan sayıyı yazıya çevirip gönder */
static void uart1_put_uint(uint32_t n)
{
    char buf[11];
    int i = 0;
    do { buf[i++] = (char)('0' + n % 10); n /= 10; } while (n);
    while (i) uart1_putc(buf[--i]);
}

static void led_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    /* PC13: genel amaçlı çıkış, push-pull, 2 MHz -> 0b0010, CRH bit 20..23 */
    GPIOC->CRH &= ~(0xFUL << 20);
    GPIOC->CRH |=  (0x2UL << 20);
}

static void delay(volatile uint32_t n)
{
    while (n--) { }
}

int main(void)
{
    uart1_init();
    led_init();

    uart1_puts("Merhaba, ben sanal Blue Pill!\n");

    uint32_t counter = 0;
    while (1) {
        GPIOC->ODR ^= (1UL << 13);            /* LED'in durumunu tersine çevir */
        uart1_puts("tik ");
        uart1_put_uint(counter++);
        uart1_puts("\n");
        delay(400000);
    }
}
