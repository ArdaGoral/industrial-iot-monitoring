/*
 * Modbus RTU slave - protokol mantığı
 *
 * Bu modül donanımdan tamamen bağımsızdır: ne UART'ı ne FreeRTOS'u bilir.
 * Bir istek paketi alır, register tablosuna göre cevap paketini üretir.
 * Bu sayede aynı kod hem STM32'de hem de Mac'te (birim testleri) çalışır.
 */
#ifndef MODBUS_H
#define MODBUS_H

#include <stdint.h>
#include <stddef.h>

#define MB_SLAVE_ID        1
#define MB_MAX_FRAME       256

/* Input register'lar (FC04, salt okunur) - sensör değerleri.
 * Python tarafındaki live_sensor.py ile aynı harita. */
enum {
    MB_IR_VIB_RMS = 0,     /* g x 1000 */
    MB_IR_VIB_PEAK,        /* g x 1000 */
    MB_IR_KURTOSIS,        /* x 100    */
    MB_IR_TEMP,            /* C x 100  */
    MB_IR_CURRENT,         /* A x 1000 */
    MB_IR_COUNTER,         /* her ölçümde artar (heartbeat) */
    MB_IR_STATUS,          /* durum bitleri, aşağıda */
    MB_INPUT_COUNT
};

/* MB_IR_STATUS bitleri */
#define MB_ST_SENSOR_OFFLINE  (1u << 0)   /* sensörden 2 sn'dir veri gelmiyor */
#define MB_ST_TEMP_ALARM      (1u << 1)   /* sıcaklık alarm eşiğinin üstünde */

/* Holding register'lar (FC03 oku, FC06 yaz) - ayarlar ve komutlar */
enum {
    MB_HR_RELAY = 0,       /* 0 = kapalı, 1 = açık (LED ile gösteriliyor) */
    MB_HR_TEMP_ALARM,      /* sıcaklık alarm eşiği, C x 100 */
    MB_HOLDING_COUNT
};

/* Modbus istisna (exception) kodları */
enum {
    MB_EX_ILLEGAL_FUNCTION = 0x01,
    MB_EX_ILLEGAL_ADDRESS  = 0x02,
    MB_EX_ILLEGAL_VALUE    = 0x03
};

typedef struct {
    uint16_t input[MB_INPUT_COUNT];
    uint16_t holding[MB_HOLDING_COUNT];
} mb_regs_t;

uint16_t mb_crc16(const uint8_t *data, size_t len);

/*
 * Bir istek paketini işler.
 *   req/len : gelen paket (CRC dahil)
 *   regs    : register tablosu (FC06 ile değişebilir)
 *   resp    : cevabın yazılacağı tampon (en az MB_MAX_FRAME bayt)
 * Dönüş: cevabın uzunluğu. 0 ise hiç cevap verilmemeli
 *        (bozuk CRC, başka cihaza gelen paket, anlamsız uzunluk).
 */
size_t mb_process(uint8_t slave_id, const uint8_t *req, size_t len,
                  mb_regs_t *regs, uint8_t *resp);

#endif /* MODBUS_H */
