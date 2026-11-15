/*
 * Faz 3 - Adım 1: ESP32 gateway, MQTT bağlantısı
 *
 * Bu ilk sürüm:
 *   - Sanal Ethernet ile ağa bağlanır (QEMU'da Mac, 10.0.2.2 adresinden görünür)
 *   - Mac'teki Mosquitto broker'a MQTT ile bağlanır
 *   - 2 saniyede bir "heartbeat" yayınlar
 *   - Komut başlıklarına abone olur ve gelen komutları loglar
 *   - Bağlantısı koparsa broker herkese "offline" duyurur (Last Will)
 *
 * Sonraki adımda Modbus master eklenecek ve sensör verisi yayınlanacak.
 */
#include <stdio.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "protocol_examples_common.h"
#include "mqtt_client.h"

static const char *TAG = "gateway";

#define BROKER_URI        "mqtt://10.0.2.2:1883"     /* QEMU içinden Mac = 10.0.2.2 */
#define TOPIC_BASE        "factory/motor1"
#define TOPIC_STATUS      TOPIC_BASE "/gateway/status"
#define TOPIC_HEARTBEAT   TOPIC_BASE "/gateway/heartbeat"
#define TOPIC_CMD_ALL     TOPIC_BASE "/cmd/#"         /* # = altındaki her başlık */

static esp_mqtt_client_handle_t s_client;
static volatile bool s_connected = false;

static void mqtt_event_handler(void *arg, esp_event_base_t base, int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        s_connected = true;
        ESP_LOGI(TAG, "Broker'a baglandi: %s", BROKER_URI);
        /* retain=1: yeni abone olan herkes son durumu hemen görür */
        esp_mqtt_client_publish(s_client, TOPIC_STATUS, "online", 0, 1, 1);
        esp_mqtt_client_subscribe(s_client, TOPIC_CMD_ALL, 1);
        break;

    case MQTT_EVENT_DISCONNECTED:
        s_connected = false;
        ESP_LOGW(TAG, "Broker baglantisi koptu, otomatik yeniden denenecek");
        break;

    case MQTT_EVENT_SUBSCRIBED:
        ESP_LOGI(TAG, "Komut basliklarina abone olundu: %s", TOPIC_CMD_ALL);
        break;

    case MQTT_EVENT_DATA:
        /* topic ve data sonu sıfırla bitmeyen tamponlar, uzunluklarıyla yazdırıyoruz */
        ESP_LOGI(TAG, "Komut geldi: %.*s = %.*s",
                 event->topic_len, event->topic, event->data_len, event->data);
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT hatasi");
        break;

    default:
        break;
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "ESP32 gateway basliyor");

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());                  /* TCP/IP yığını (lwIP) */
    ESP_ERROR_CHECK(esp_event_loop_create_default());   /* olay döngüsü */

    /* Ethernet'i başlat ve DHCP ile IP alana kadar bekle */
    ESP_ERROR_CHECK(example_connect());

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = BROKER_URI,
        .session.last_will = {
            .topic  = TOPIC_STATUS,
            .msg    = "offline",
            .qos    = 1,
            .retain = 1,
        },
    };
    s_client = esp_mqtt_client_init(&cfg);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);

    /* Heartbeat: gateway'in canlı olduğunu ve kaynak durumunu yayınla */
    char payload[96];
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        if (!s_connected) continue;

        uint32_t uptime_s = (uint32_t)(esp_timer_get_time() / 1000000);
        snprintf(payload, sizeof(payload),
                 "{\"uptime_s\":%" PRIu32 ",\"free_heap\":%" PRIu32 "}",
                 uptime_s, esp_get_free_heap_size());
        esp_mqtt_client_publish(s_client, TOPIC_HEARTBEAT, payload, 0, 0, 0);
    }
}
