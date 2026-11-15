/*
 * Faz 2 - Adım 4: Modbus RTU slave
 *
 * Görevler:
 *   task_modbus (öncelik 3): USART2'den gelen istekleri cevaplar
 *   task_sensor (öncelik 2): her 500 ms'de input register'ları günceller
 *   task_relay  (öncelik 1): holding register'daki röle komutunu LED'e uygular
 *
 * Veri akışı:
 *   USART2 kesmesi --(kuyruk)--> task_modbus --(mutex)--> register tablosu
 *                                                         ^            |
 *                                          task_sensor ---+            +--> task_relay
 */
#include "stm32f1xx.h"
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include "uart.h"
#include "modbus.h"

#define MB_FRAME_GAP_MS   5     /* 9600 baud'da 3.5 karakter süresi ~4 ms */
#define RX_QUEUE_LEN      64

static SemaphoreHandle_t log_mutex;
static SemaphoreHandle_t regs_mutex;
static QueueHandle_t     rx_queue;
static TaskHandle_t      relay_task;
static mb_regs_t         regs;

/* ---------------- yardımcılar ---------------- */

static void log_line(const char *who, const char *msg, uint32_t value)
{
    xSemaphoreTake(log_mutex, portMAX_DELAY);
    uart_puts("[");
    uart_put_uint(xTaskGetTickCount());
    uart_puts(" ms] ");
    uart_puts(who);
    uart_puts(": ");
    uart_puts(msg);
    uart_put_uint(value);
    uart_puts("\n");
    xSemaphoreGive(log_mutex);
}

static void led_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    GPIOC->CRH &= ~(0xFUL << 20);              /* PC13: çıkış, push-pull, 2 MHz */
    GPIOC->CRH |=  (0x2UL << 20);
    GPIOC->BSRR = (1UL << 13);                 /* LED sönük başlasın (aktif-düşük) */
}

/* ---------------- kesme ---------------- */

/* USART2'ye her bayt geldiğinde donanım bu fonksiyonu çağırır.
 * Kesme içinde uzun iş yapılmaz: baytı kuyruğa atıp hemen çıkıyoruz. */
void USART2_IRQHandler(void)
{
    BaseType_t woken = pdFALSE;

    if (USART2->SR & USART_SR_RXNE) {
        uint8_t b = (uint8_t)USART2->DR;       /* DR'yi okumak RXNE bayrağını temizler */
        xQueueSendFromISR(rx_queue, &b, &woken);
    }
    /* Kuyruk bekleyen bir görevi uyandırdıysa, kesmeden çıkınca hemen ona geç */
    portYIELD_FROM_ISR(woken);
}

/* ---------------- görevler ---------------- */

static void task_modbus(void *arg)
{
    (void)arg;
    static uint8_t rx[MB_MAX_FRAME];
    static uint8_t tx[MB_MAX_FRAME];

    for (;;) {
        size_t n = 0;
        int overflow = 0;
        uint8_t b;

        /* 1) Paketin ilk baytını süresiz bekle */
        xQueueReceive(rx_queue, &b, portMAX_DELAY);
        rx[n++] = b;

        /* 2) Hat MB_FRAME_GAP_MS boyunca sessiz kalana kadar baytları topla.
         *    Modbus RTU'da paketin bittiğini bu sessizlikten anlarız. */
        while (xQueueReceive(rx_queue, &b, pdMS_TO_TICKS(MB_FRAME_GAP_MS)) == pdPASS) {
            if (n < sizeof(rx)) rx[n++] = b;
            else overflow = 1;
        }
        if (overflow) {
            log_line("modbus", "paket cok uzun, atildi. bayt=", (uint32_t)n);
            continue;
        }

        /* 3) Paketi işle (register tablosu kilitliyken) */
        xSemaphoreTake(regs_mutex, portMAX_DELAY);
        size_t m = mb_process(MB_SLAVE_ID, rx, n, &regs, tx);
        xSemaphoreGive(regs_mutex);

        /* 4) Cevap varsa gönder */
        if (m > 0) {
            uart2_write(tx, m);
            /* Başarılı bir yazma olduysa röle görevini hemen uyandır */
            if (tx[1] == 0x06) xTaskNotifyGive(relay_task);
            if (tx[1] & 0x80) log_line("modbus", "istisna cevabi, kod=", tx[2]);
            else              log_line("modbus", "cevap verildi, FC=", tx[1]);
        } else {
            log_line("modbus", "yok sayildi (CRC/adres/uzunluk), bayt=", (uint32_t)n);
        }
    }
}

static void task_sensor(void *arg)
{
    (void)arg;
    uint32_t n = 0;
    TickType_t last_wake = xTaskGetTickCount();

    for (;;) {
        /* Şimdilik basit, değişen değerler. Sonraki adımda bu veri
         * Python'daki sensör simülatöründen gelecek. */
        xSemaphoreTake(regs_mutex, portMAX_DELAY);
        regs.input[MB_IR_VIB_RMS]  = (uint16_t)(360 + n % 8);
        regs.input[MB_IR_VIB_PEAK] = (uint16_t)(650 + n % 30);
        regs.input[MB_IR_KURTOSIS] = (uint16_t)(165 + n % 3);
        regs.input[MB_IR_TEMP]     = (uint16_t)(5480 + (n % 10) * 5);
        regs.input[MB_IR_CURRENT]  = (uint16_t)(4950 + (n % 11) * 10);
        regs.input[MB_IR_COUNTER]  = (uint16_t)n;
        xSemaphoreGive(regs_mutex);

        n++;
        xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(500));
    }
}

static void task_relay(void *arg)
{
    (void)arg;
    uint16_t last = 0xFFFF;                    /* ilk turda mutlaka log yazsın */

    for (;;) {
        xSemaphoreTake(regs_mutex, portMAX_DELAY);
        uint16_t cmd = regs.holding[MB_HR_RELAY];
        xSemaphoreGive(regs_mutex);

        if (cmd != last) {
            if (cmd) GPIOC->BRR  = (1UL << 13);    /* PC13 = 0 -> LED yanar */
            else     GPIOC->BSRR = (1UL << 13);    /* PC13 = 1 -> LED söner */
            log_line("role", "durum=", cmd);
            last = cmd;
        }
        /* Uyu, Modbus görevi yeni bir komut yazınca uyandırsın.
         * Önceden 100 ms'de bir bakıyorduk (polling) ve kısa süreli
         * komutları kaçırıyorduk. */
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    }
}

/* ---------------- başlangıç ---------------- */

int main(void)
{
    SCB->VTOR = FLASH_BASE;

    uart_init();
    led_init();
    uart_puts("Modbus RTU slave basliyor... adres=");
    uart_put_uint(MB_SLAVE_ID);
    uart_puts("\n");

    log_mutex  = xSemaphoreCreateMutex();
    regs_mutex = xSemaphoreCreateMutex();
    rx_queue   = xQueueCreate(RX_QUEUE_LEN, sizeof(uint8_t));
    configASSERT(log_mutex && regs_mutex && rx_queue);

    regs.holding[MB_HR_RELAY]      = 0;
    regs.holding[MB_HR_TEMP_ALARM] = 7000;     /* 70.00 C */

    /* USART2 kesmesi: önceliği FreeRTOS'un izin verdiği aralıkta olmalı
     * (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY = 5, sayıca >= 5) */
    uart2_init();
    NVIC_SetPriority(USART2_IRQn, 6);
    NVIC_EnableIRQ(USART2_IRQn);

    xTaskCreate(task_modbus, "modbus", 256, NULL, 3, NULL);
    xTaskCreate(task_sensor, "sensor", 256, NULL, 2, NULL);
    xTaskCreate(task_relay,  "role",   256, NULL, 1, &relay_task);

    uart_puts("Bos heap: ");
    uart_put_uint((uint32_t)xPortGetFreeHeapSize());
    uart_puts(" bayt\n");

    vTaskStartScheduler();

    uart_puts("HATA: scheduler baslamadi\n");
    for (;;) { }
}

/* ---------------- hata kancaları ---------------- */

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
