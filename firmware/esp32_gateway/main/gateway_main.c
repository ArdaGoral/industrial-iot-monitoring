/*
 * Faz 3 - Adım 2: ESP32 gateway (Modbus master + MQTT)
 *
 *   STM32 (Modbus slave) <--UART1/Modbus RTU--> ESP32 <--MQTT--> broker
 *
 * Görevler:
 *   - Her saniye STM32'den sensör ve ayar register'larını okur,
 *     JSON olarak factory/motor1/telemetry başlığına yayınlar
 *   - factory/motor1/cmd/... komutlarını Modbus yazma isteğine çevirir
 *   - STM32 cevap vermezse factory/motor1/plc/status = offline yayınlar
 *   - 10 saniyede bir gateway sağlık bilgisi (heartbeat) yayınlar
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "protocol_examples_common.h"
#include "mqtt_client.h"
#include "modbus_master.h"

static const char *TAG = "gateway";

/* ---------------- ayarlar ---------------- */

#define BROKER_URI          "mqtt://10.0.2.2:1883"    /* QEMU içinden Mac = 10.0.2.2 */
#define TOPIC_BASE          "factory/motor1"
#define TOPIC_GW_STATUS     TOPIC_BASE "/gateway/status"
#define TOPIC_HEARTBEAT     TOPIC_BASE "/gateway/heartbeat"
#define TOPIC_PLC_STATUS    TOPIC_BASE "/plc/status"
#define TOPIC_TELEMETRY     TOPIC_BASE "/telemetry"
#define TOPIC_EVENT         TOPIC_BASE "/event"
#define TOPIC_CMD_ALL       TOPIC_BASE "/cmd/#"
#define TOPIC_CMD_RELAY     TOPIC_BASE "/cmd/relay"
#define TOPIC_CMD_LIMIT     TOPIC_BASE "/cmd/temp_alarm_limit"

#define MODBUS_UART         UART_NUM_1
#define MODBUS_BAUD         9600
#define MODBUS_TX_PIN       17       /* gerçek kartta MAX485 DI */
#define MODBUS_RX_PIN       16       /* gerçek kartta MAX485 RO */
#define SLAVE_ID            1

#define POLL_MS             1000
#define HEARTBEAT_MS        10000
#define OFFLINE_AFTER_FAILS 3        /* üst üste bu kadar hata -> PLC kopuk */

/* STM32'deki register haritası (firmware/bluepill_rtos/include/modbus.h ile aynı) */
enum { IR_VIB_RMS, IR_VIB_PEAK, IR_KURTOSIS, IR_TEMP, IR_CURRENT, IR_COUNTER, IR_STATUS, IR_COUNT };
enum { HR_RELAY, HR_TEMP_ALARM, HR_COUNT };
#define ST_SENSOR_OFFLINE   0x01
#define ST_TEMP_ALARM       0x02

/* ---------------- komut kuyruğu ---------------- */

typedef enum { CMD_RELAY, CMD_TEMP_LIMIT } cmd_type_t;
typedef struct {
    cmd_type_t type;
    uint16_t   reg_value;    /* register'a yazılacak ham değer */
    float      user_value;   /* kullanıcının gönderdiği değer (olay mesajı için) */
} cmd_t;

static QueueHandle_t            s_cmd_queue;
static esp_mqtt_client_handle_t s_client;
static volatile bool            s_mqtt_connected = false;
static uint32_t                 s_modbus_ok = 0, s_modbus_err = 0;

/* PLC (STM32) durumu: -1 = henüz bilinmiyor, 0 = offline, 1 = online */
static volatile int             s_plc_state = -1;

/* ---------------- yardımcılar ---------------- */

static void publish(const char *topic, const char *payload, int qos, int retain)
{
    if (s_mqtt_connected)
        esp_mqtt_client_publish(s_client, topic, payload, 0, qos, retain);
}

/* MQTT'den gelen metni komuta çevirir. Geçersizse false döner. */
static bool parse_command(const char *topic, const char *payload, cmd_t *cmd)
{
    if (strcmp(topic, TOPIC_CMD_RELAY) == 0) {
        if (!strcmp(payload, "1") || !strcmp(payload, "on"))  { cmd->reg_value = 1; }
        else if (!strcmp(payload, "0") || !strcmp(payload, "off")) { cmd->reg_value = 0; }
        else return false;
        cmd->type = CMD_RELAY;
        cmd->user_value = cmd->reg_value;
        return true;
    }
    if (strcmp(topic, TOPIC_CMD_LIMIT) == 0) {
        char *end;
        float c = strtof(payload, &end);
        if (end == payload || c < 0.0f || c > 150.0f) return false;
        cmd->type = CMD_TEMP_LIMIT;
        cmd->user_value = c;
        cmd->reg_value = (uint16_t)(c * 100.0f + 0.5f);   /* register: C x 100 */
        return true;
    }
    return false;
}

/* ---------------- MQTT ---------------- */

static void mqtt_event_handler(void *arg, esp_event_base_t base, int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t ev = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        s_mqtt_connected = true;
        ESP_LOGI(TAG, "Broker'a baglandi");
        esp_mqtt_client_publish(s_client, TOPIC_GW_STATUS, "online", 0, 1, 1);
        esp_mqtt_client_subscribe(s_client, TOPIC_CMD_ALL, 1);
        /* Broker'a geç bağlandıysak ya da bağlantı koptuysa son PLC durumunu tekrar bildir */
        if (s_plc_state >= 0)
            esp_mqtt_client_publish(s_client, TOPIC_PLC_STATUS,
                                    s_plc_state ? "online" : "offline", 0, 1, 1);
        break;

    case MQTT_EVENT_DISCONNECTED:
        s_mqtt_connected = false;
        ESP_LOGW(TAG, "Broker baglantisi koptu");
        break;

    case MQTT_EVENT_DATA: {
        /* topic ve data sıfırla bitmiyor; güvenli kopyalarını alıyoruz */
        char topic[64], payload[32];
        snprintf(topic, sizeof(topic), "%.*s", ev->topic_len, ev->topic);
        snprintf(payload, sizeof(payload), "%.*s", ev->data_len, ev->data);

        cmd_t cmd;
        if (parse_command(topic, payload, &cmd)) {
            /* Modbus işlemini burada yapmıyoruz: MQTT görevini bekletmemek için
             * komutu kuyruğa atıp Modbus görevine bırakıyoruz. */
            if (xQueueSend(s_cmd_queue, &cmd, 0) != pdPASS)
                ESP_LOGW(TAG, "Komut kuyrugu dolu, komut atildi");
            else
                ESP_LOGI(TAG, "Komut alindi: %s = %s", topic, payload);
        } else {
            ESP_LOGW(TAG, "Gecersiz komut: %s = %s", topic, payload);
            char ev_json[192];
            snprintf(ev_json, sizeof(ev_json),
                     "{\"topic\":\"%s\",\"payload\":\"%s\",\"result\":\"gecersiz_komut\"}", topic, payload);
            publish(TOPIC_EVENT, ev_json, 1, 0);
        }
        break;
    }
    default:
        break;
    }
}

/* ---------------- Modbus görevi ---------------- */

static void execute_command(const cmd_t *cmd)
{
    uint16_t addr = (cmd->type == CMD_RELAY) ? HR_RELAY : HR_TEMP_ALARM;
    const char *name = (cmd->type == CMD_RELAY) ? "relay" : "temp_alarm_limit";
    uint8_t ex = 0;

    mbm_result_t r = mbm_write_single(SLAVE_ID, addr, cmd->reg_value, &ex);
    if (r == MBM_OK) s_modbus_ok++; else s_modbus_err++;

    ESP_LOGI(TAG, "Komut uygulandi: %s = %.2f -> %s", name, cmd->user_value, mbm_result_str(r));

    char json[160];
    snprintf(json, sizeof(json),
             "{\"cmd\":\"%s\",\"value\":%.2f,\"result\":\"%s\",\"exception\":%u}",
             name, cmd->user_value, mbm_result_str(r), (unsigned)ex);
    publish(TOPIC_EVENT, json, 1, 0);
}

static void poll_slave(int *fail_count)
{
    uint16_t ir[IR_COUNT], hr[HR_COUNT];
    uint8_t ex = 0;

    mbm_result_t r = mbm_read(SLAVE_ID, 0x04, 0, IR_COUNT, ir, &ex);
    if (r == MBM_OK)
        r = mbm_read(SLAVE_ID, 0x03, 0, HR_COUNT, hr, &ex);

    if (r != MBM_OK) {
        s_modbus_err++;
        (*fail_count)++;
        ESP_LOGW(TAG, "Modbus okuma hatasi: %s (ust uste %d)", mbm_result_str(r), *fail_count);
        if (s_plc_state != 0 && *fail_count >= OFFLINE_AFTER_FAILS) {
            s_plc_state = 0;
            publish(TOPIC_PLC_STATUS, "offline", 1, 1);
            ESP_LOGE(TAG, "PLC cevap vermiyor -> offline");
        }
        return;
    }

    s_modbus_ok++;
    *fail_count = 0;
    if (s_plc_state != 1) {
        s_plc_state = 1;
        publish(TOPIC_PLC_STATUS, "online", 1, 1);
        ESP_LOGI(TAG, "PLC cevap veriyor -> online");
    }

    /* Ham register'ları gerçek birimlere çevirip JSON yap */
    char json[320];
    snprintf(json, sizeof(json),
             "{\"ts_ms\":%" PRId64 ","
             "\"vib_rms\":%.3f,\"vib_peak\":%.3f,\"kurtosis\":%.2f,"
             "\"temp_c\":%.2f,\"current_a\":%.3f,\"counter\":%u,"
             "\"sensor_online\":%s,\"temp_alarm\":%s,"
             "\"relay\":%u,\"temp_alarm_limit_c\":%.2f}",
             esp_timer_get_time() / 1000,
             ir[IR_VIB_RMS] / 1000.0, ir[IR_VIB_PEAK] / 1000.0, ir[IR_KURTOSIS] / 100.0,
             ir[IR_TEMP] / 100.0, ir[IR_CURRENT] / 1000.0, (unsigned)ir[IR_COUNTER],
             (ir[IR_STATUS] & ST_SENSOR_OFFLINE) ? "false" : "true",
             (ir[IR_STATUS] & ST_TEMP_ALARM) ? "true" : "false",
             (unsigned)hr[HR_RELAY], hr[HR_TEMP_ALARM] / 100.0);
    publish(TOPIC_TELEMETRY, json, 0, 0);
}

static void modbus_task(void *arg)
{
    int fail_count = 0;
    int64_t next_poll = esp_timer_get_time();
    int64_t next_hb   = next_poll + (int64_t)HEARTBEAT_MS * 1000;

    for (;;) {
        /* Bir sonraki okumaya kadar komut bekle: komut gelirse hemen uygula */
        int64_t now = esp_timer_get_time();
        int64_t wait_us = next_poll - now;
        TickType_t wait = wait_us > 0 ? pdMS_TO_TICKS(wait_us / 1000) : 0;

        cmd_t cmd;
        if (xQueueReceive(s_cmd_queue, &cmd, wait) == pdPASS) {
            execute_command(&cmd);
            continue;                /* başka komut var mı diye tekrar bak */
        }

        poll_slave(&fail_count);
        next_poll += (int64_t)POLL_MS * 1000;
        if (next_poll < esp_timer_get_time())          /* çok geride kaldıysak toparla */
            next_poll = esp_timer_get_time() + (int64_t)POLL_MS * 1000;

        if (esp_timer_get_time() >= next_hb) {
            char hb[128];
            snprintf(hb, sizeof(hb),
                     "{\"uptime_s\":%" PRId64 ",\"free_heap\":%" PRIu32
                     ",\"modbus_ok\":%" PRIu32 ",\"modbus_err\":%" PRIu32 "}",
                     esp_timer_get_time() / 1000000, esp_get_free_heap_size(),
                     s_modbus_ok, s_modbus_err);
            publish(TOPIC_HEARTBEAT, hb, 0, 0);
            next_hb += (int64_t)HEARTBEAT_MS * 1000;
        }
    }
}

/* ---------------- başlangıç ---------------- */

void app_main(void)
{
    ESP_LOGI(TAG, "ESP32 gateway basliyor (Modbus master + MQTT)");

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ESP_ERROR_CHECK(mbm_init(MODBUS_UART, MODBUS_BAUD, MODBUS_TX_PIN, MODBUS_RX_PIN));
    s_cmd_queue = xQueueCreate(8, sizeof(cmd_t));

    ESP_ERROR_CHECK(example_connect());

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = BROKER_URI,
        .session.last_will = {
            .topic = TOPIC_GW_STATUS, .msg = "offline", .qos = 1, .retain = 1,
        },
    };
    s_client = esp_mqtt_client_init(&cfg);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);

    xTaskCreate(modbus_task, "modbus", 4096, NULL, 5, NULL);
}
