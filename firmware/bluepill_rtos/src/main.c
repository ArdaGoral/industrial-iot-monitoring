/*
 * Faz 2 - Adım 5: Modbus RTU slave + sensör hattı
 *
 * Görevler:
 *   task_modbus (öncelik 3): USART2'den gelen Modbus isteklerini cevaplar
 *   task_sensor (öncelik 2): USART3'ten gelen sensör çerçevelerini register'lara yazar,
 *                            sensör kopmasını ve sıcaklık alarmını izler
 *   task_relay  (öncelik 1): holding register'daki röle komutunu LED'e uygular
 *
 * Veri akışı:
 *   USART3 kesmesi --(kuyruk)--> task_sensor --+
 *                                              +--(mutex)--> register tablosu --> task_relay
 *   USART2 kesmesi --(kuyruk)--> task_modbus --+
 */
#include "stm32f1xx.h"
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include "uart.h"
#include "modbus.h"
#include "sensor_link.h"

#define MB_FRAME_GAP_MS     5      /* 9600 baud'da 3.5 karakter süresi ~4 ms */
#define RX_QUEUE_LEN        64
#define SENSOR_QUEUE_LEN    64
#define SENSOR_TIMEOUT_MS   2000   /* bu kadar süre çerçeve gelmezse sensör koptu say */

static SemaphoreHandle_t log_mutex;
static SemaphoreHandle_t regs_mutex;
static QueueHandle_t     rx_queue;
static QueueHandle_t     sensor_queue;
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

/* Sensör hattı: aynı desen, farklı kuyruk */
void USART3_IRQHandler(void)
{
    BaseType_t woken = pdFALSE;

    if (USART3->SR & USART_SR_RXNE) {
        uint8_t b = (uint8_t)USART3->DR;
        xQueueSendFromISR(sensor_queue, &b, &woken);
    }
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
    sl_parser_t parser;
    uint16_t values[SL_VALUE_COUNT];
    uint16_t frames = 0;
    uint32_t reported_crc_errors = 0;
    int online = 0, alarm = 0;
    TickType_t last_frame = xTaskGetTickCount();

    sl_init(&parser);

    for (;;) {
        uint8_t b;

        /* En fazla 100 ms bekle: veri gelmese de kopma kontrolü için uyanalım */
        if (xQueueReceive(sensor_queue, &b, pdMS_TO_TICKS(100)) == pdPASS &&
            sl_feed(&parser, b, values)) {

            frames++;
            last_frame = xTaskGetTickCount();

            xSemaphoreTake(regs_mutex, portMAX_DELAY);
            for (int i = 0; i < SL_VALUE_COUNT; i++)
                regs.input[MB_IR_VIB_RMS + i] = values[i];   /* aynı sıra, aynı ölçek */
            regs.input[MB_IR_COUNTER] = frames;

            int new_alarm = values[MB_IR_TEMP] > regs.holding[MB_HR_TEMP_ALARM];
            regs.input[MB_IR_STATUS] = (uint16_t)(new_alarm ? MB_ST_TEMP_ALARM : 0);  /* çevrimiçi */
            xSemaphoreGive(regs_mutex);

            if (!online) {
                online = 1;
                log_line("sensor", "baglanti var, cerceve #", frames);
            }
            if (new_alarm != alarm) {
                alarm = new_alarm;
                log_line("sensor", alarm ? "ALARM! sicaklik x100=" : "alarm bitti, sicaklik x100=",
                         values[MB_IR_TEMP]);
            }
            if (frames % 20 == 0)
                log_line("sensor", "alinan cerceve=", frames);
        }

        /* Bozuk çerçeve geldiyse haber ver */
        if (parser.crc_errors != reported_crc_errors) {
            reported_crc_errors = parser.crc_errors;
            log_line("sensor", "bozuk cerceve atildi, toplam=", reported_crc_errors);
        }

        /* Uzun süre veri gelmediyse sensörü kopuk say */
        if (online && (xTaskGetTickCount() - last_frame) > pdMS_TO_TICKS(SENSOR_TIMEOUT_MS)) {
            online = 0;
            xSemaphoreTake(regs_mutex, portMAX_DELAY);
            regs.input[MB_IR_STATUS] |= MB_ST_SENSOR_OFFLINE;
            xSemaphoreGive(regs_mutex);
            log_line("sensor", "VERI GELMIYOR! son cerceve #", frames);
        }
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
    rx_queue     = xQueueCreate(RX_QUEUE_LEN, sizeof(uint8_t));
    sensor_queue = xQueueCreate(SENSOR_QUEUE_LEN, sizeof(uint8_t));
    configASSERT(log_mutex && regs_mutex && rx_queue && sensor_queue);

    regs.holding[MB_HR_RELAY]      = 0;
    regs.holding[MB_HR_TEMP_ALARM] = 7000;     /* 70.00 C */
    regs.input[MB_IR_STATUS]       = MB_ST_SENSOR_OFFLINE;   /* ilk çerçeve gelene kadar */

    /* USART2 kesmesi: önceliği FreeRTOS'un izin verdiği aralıkta olmalı
     * (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY = 5, sayıca >= 5) */
    uart2_init();
    NVIC_SetPriority(USART2_IRQn, 6);
    NVIC_EnableIRQ(USART2_IRQn);

    uart3_init();
    NVIC_SetPriority(USART3_IRQn, 6);
    NVIC_EnableIRQ(USART3_IRQn);

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
