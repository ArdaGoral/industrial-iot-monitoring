/*
 * Modbus RTU master - UART katmanı
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "modbus_master.h"

static const char *TAG = "modbus";

#define RESPONSE_TIMEOUT_MS  300   /* slave'in cevaba başlaması için en fazla süre */
#define REST_TIMEOUT_MS      100   /* cevabın geri kalanı için */
#define FRAME_GAP_MS         5     /* iki işlem arası sessizlik (9600 baud'da 3.5 karakter ~4 ms) */

static uart_port_t       s_port;
static SemaphoreHandle_t s_bus_lock;

esp_err_t mbm_init(uart_port_t port, int baud, int tx_pin, int rx_pin)
{
    s_port = port;
    s_bus_lock = xSemaphoreCreateMutex();
    if (!s_bus_lock) return ESP_ERR_NO_MEM;

    const uart_config_t cfg = {
        .baud_rate  = baud,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(port, 512, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(port, &cfg));
    /* Gerçek kartta buraya MAX485 bağlanır. DE/RE pini için ileride
     * UART_MODE_RS485_HALF_DUPLEX kullanılabilir (RTS pini = DE). */
    ESP_ERROR_CHECK(uart_set_pin(port, tx_pin, rx_pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    ESP_LOGI(TAG, "UART%d hazir: %d baud, TX=%d RX=%d", port, baud, tx_pin, rx_pin);
    return ESP_OK;
}

/* İsteği gönderir, cevabı okur. resp_len'e gelen bayt sayısını yazar. */
static mbm_result_t transact(const uint8_t *req, size_t req_len, size_t expected_len,
                             uint8_t *resp, size_t *resp_len)
{
    uart_flush_input(s_port);                       /* önceki işlemden kalan çöpü at */
    uart_write_bytes(s_port, req, req_len);
    uart_wait_tx_done(s_port, pdMS_TO_TICKS(100));

    /* İlk 3 bayt: adres, fonksiyon kodu ve (bayt sayısı ya da istisna kodu) */
    int n = uart_read_bytes(s_port, resp, 3, pdMS_TO_TICKS(RESPONSE_TIMEOUT_MS));
    if (n < 3) {
        *resp_len = n > 0 ? (size_t)n : 0;
        return MBM_ERR_TIMEOUT;
    }

    /* En üst bit 1 ise istisna cevabı: toplam 5 bayt. Değilse beklenen uzunluk. */
    size_t total = (resp[1] & 0x80) ? MBM_EXCEPTION_LEN : expected_len;
    int rest = uart_read_bytes(s_port, resp + 3, total - 3, pdMS_TO_TICKS(REST_TIMEOUT_MS));
    *resp_len = 3 + (rest > 0 ? (size_t)rest : 0);
    return MBM_OK;
}

mbm_result_t mbm_read(uint8_t slave, uint8_t fc, uint16_t start, uint16_t qty,
                      uint16_t *out, uint8_t *ex_code)
{
    uint8_t req[8], resp[MBM_MAX_FRAME];
    size_t resp_len = 0;
    size_t req_len = mbm_build_read(req, slave, fc, start, qty);

    xSemaphoreTake(s_bus_lock, portMAX_DELAY);
    mbm_result_t r = transact(req, req_len, mbm_read_response_len(qty), resp, &resp_len);
    vTaskDelay(pdMS_TO_TICKS(FRAME_GAP_MS));
    xSemaphoreGive(s_bus_lock);

    if (r != MBM_OK) return r;
    return mbm_parse_read(resp, resp_len, slave, fc, qty, out, ex_code);
}

mbm_result_t mbm_write_single(uint8_t slave, uint16_t addr, uint16_t value, uint8_t *ex_code)
{
    uint8_t req[8], resp[MBM_MAX_FRAME];
    size_t resp_len = 0;
    size_t req_len = mbm_build_write_single(req, slave, addr, value);

    xSemaphoreTake(s_bus_lock, portMAX_DELAY);
    mbm_result_t r = transact(req, req_len, MBM_WRITE_RESPONSE_LEN, resp, &resp_len);
    vTaskDelay(pdMS_TO_TICKS(FRAME_GAP_MS));
    xSemaphoreGive(s_bus_lock);

    if (r != MBM_OK) return r;
    return mbm_parse_write_single(resp, resp_len, req, ex_code);
}
