/*
 * Faz 2 - Adım 3: FreeRTOS ile iki görev
 *
 *   task_sensor (öncelik 2): her 500 ms'de "sensör okuması" yapar
 *   task_led    (öncelik 1): her 1000 ms'de LED'i yakıp söndürür
 *
 * İki görev de aynı UART'a yazıyor. Aynı anda yazarlarsa satırlar birbirine
 * karışır. Bunu bir mutex (kilit) ile önlüyoruz.
 */
#include "stm32f1xx.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#include "uart.h"

static SemaphoreHandle_t uart_mutex;

/* Bir satırı UART'a "kilitli" yaz: [zaman ms] görev: mesaj sayı */
static void log_line(const char *who, const char *msg, uint32_t value)
{
    xSemaphoreTake(uart_mutex, portMAX_DELAY);   /* kilidi al (gerekirse bekle) */
    uart_puts("[");
    uart_put_uint(xTaskGetTickCount());          /* 1 tick = 1 ms */
    uart_puts(" ms] ");
    uart_puts(who);
    uart_puts(": ");
    uart_puts(msg);
    uart_put_uint(value);
    uart_puts("\n");
    xSemaphoreGive(uart_mutex);                  /* kilidi bırak */
}

static void led_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    GPIOC->CRH &= ~(0xFUL << 20);                /* PC13: çıkış, push-pull, 2 MHz */
    GPIOC->CRH |=  (0x2UL << 20);
    GPIOC->BSRR = (1UL << 13);                   /* LED sönük başlasın (aktif-düşük) */
}

static void task_sensor(void *arg)
{
    (void)arg;
    uint32_t n = 0;
    TickType_t last_wake = xTaskGetTickCount();

    for (;;) {
        log_line("sensor", "okuma #", n++);
        /* Tam 500 ms'de bir uyan: işin süresi ne olursa olsun periyot kaymaz */
        xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(500));
    }
}

static void task_led(void *arg)
{
    (void)arg;

    for (;;) {
        GPIOC->ODR ^= (1UL << 13);
        uint32_t on = (GPIOC->ODR & (1UL << 13)) ? 0 : 1;   /* PC13 = 0 iken LED yanar */
        log_line("led", "durum=", on);

        /* Bu görevin stack'inde en az ne kadar boş yer kaldı (word cinsinden) */
        log_line("led", "bos stack=", uxTaskGetStackHighWaterMark(NULL));

        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

int main(void)
{
    SCB->VTOR = FLASH_BASE;          /* vektör tablosu flash'ın başında */

    uart_init();
    led_init();
    uart_puts("FreeRTOS basliyor...\n");

    uart_mutex = xSemaphoreCreateMutex();
    configASSERT(uart_mutex != NULL);

    xTaskCreate(task_sensor, "sensor", 256, NULL, 2, NULL);
    xTaskCreate(task_led,    "led",    256, NULL, 1, NULL);

    uart_puts("Bos heap: ");
    uart_put_uint((uint32_t)xPortGetFreeHeapSize());
    uart_puts(" bayt\n");

    vTaskStartScheduler();           /* buradan sonra kontrol FreeRTOS'ta */

    uart_puts("HATA: scheduler baslamadi\n");
    for (;;) { }
}

/* ---------------- Hata kancaları (hooks) ---------------- */

void vAssertCalled(const char *file, int line)
{
    taskDISABLE_INTERRUPTS();
    uart_puts("ASSERT: ");
    uart_puts(file);
    uart_puts(":");
    uart_put_uint((uint32_t)line);
    uart_puts("\n");
    for (;;) { }
}

void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
    (void)task;
    taskDISABLE_INTERRUPTS();
    uart_puts("STACK TASTI: ");
    uart_puts(name);
    uart_puts("\n");
    for (;;) { }
}

void vApplicationMallocFailedHook(void)
{
    taskDISABLE_INTERRUPTS();
    uart_puts("HEAP BITTI\n");
    for (;;) { }
}
